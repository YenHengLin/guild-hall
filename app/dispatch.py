"""派遣系統 —— 把布告欄委託「派遣」給一位 headless 冒險者,回合制執行。

為什麼是回合制:`claude -p` 一旦啟動,中途無法插話;把任務切成多個回合
(首回合開新 session、之後 `-r <sid>` 續跑),回合之間就有了「櫃台視窗」:
- 讀取使用者的中途指示(steer)與最新目標(goal)注入下一回合 → **中途調整任務**
- 檢查 stop_requested → 隨時可叫停
- 每回合的 STATUS 行寫進 commission_events → 面板即時看進度

完成閉環:冒險者回報 DONE 時,自動把 REPORT 內容 enqueue 進櫃台佇列
(→ 存檔 → llm-wiki-manager 深度合成),委託標記完成。

護欄:並行上限(GUILD_DISPATCH_PARALLEL)且同資料夾同時只跑一張、回合上限、單回合逾時、工具白名單(預設含 Bash,
可用 GUILD_DISPATCH_TOOLS 收緊)、cwd 鎖在委託指定的公會資料夾。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time

from . import config, db, uploads
from .claude_run import run_stream
from .stream_recall import _summarize_tool

# 工具 → 時間線步驟圖示(讓使用者一眼看出冒險者在做什麼類型的事)
_STEP_ICON = {
    "Edit": "✏️", "Write": "✏️", "NotebookEdit": "✏️",
    "Read": "📖", "Grep": "🔍", "Glob": "🔍",
    "Bash": "⚙️", "PowerShell": "⚙️",
    "WebSearch": "🌐", "WebFetch": "🌐",
    "Skill": "📜", "Task": "🤖", "Agent": "🤖",
}

# Windows 程序「還沒跑起來就初始化失敗」的結束碼,通常是暫時性資源/環境問題,值得重試
# 0xC0000142 STATUS_DLL_INIT_FAILED / 0xC0000005 存取違規 / 0xC0000017 記憶體不足
_STARTUP_FAIL_CODES = {0xC0000142, 0xC0000005, 0xC0000017}
_STARTUP_RETRIES = int(config.get("GUILD_DISPATCH_STARTUP_RETRIES", "3"))

log = logging.getLogger("guild.dispatch")

CLAUDE = shutil.which("claude") or "claude"
MODEL = config.get("GUILD_DISPATCH_MODEL", "")  # 空 = 用使用者預設模型
MAX_ROUNDS = int(config.get("GUILD_DISPATCH_ROUNDS", "6"))
# 閒置逾時:連續這麼久「毫無任何輸出」才視為 hang 砍掉(不是牆鐘硬上限)。
# 只要冒險者還在吐工具動作/進度就持續延長 —— 真的在跑就不會被誤砍。
ROUND_TIMEOUT = int(config.get("GUILD_DISPATCH_TIMEOUT", "600"))
# 總時長絕對上限:防失控燒錢/無限迴圈。再怎麼「有在動」單回合也不超過這個。
HARD_CAP = int(config.get("GUILD_DISPATCH_HARD_CAP", "2400"))  # 預設 40 分
MAX_TURNS = config.get("GUILD_DISPATCH_TURNS", "25")  # claude 內部回合上限
TOOLS = config.get(
    "GUILD_DISPATCH_TOOLS", "Read,Glob,Grep,Edit,Write,Bash,WebSearch,WebFetch")

# 櫃台驗收:DONE 時另起 claude 唯讀查驗產出是否達標,未過自動退回重做。
# 預設用 sonnet:haiku 查得出「檔案在不在」,查不出「這修法根本沒解決問題」的深層錯誤
# (真實案例:委託 #80 用弱模型驗收,把一個修錯根因的假修法蓋章放行)。
ACCEPTANCE = config.get("GUILD_ACCEPTANCE", "1") == "1"
ACCEPT_MODEL = config.get("GUILD_ACCEPT_MODEL", "claude-sonnet-4-6")
MAX_REWORK = int(config.get("GUILD_ACCEPTANCE_REWORK", "1"))  # 最多自動退回幾次
ACCEPT_TIMEOUT = int(config.get("GUILD_ACCEPT_TIMEOUT", "300"))

# 自建任務資料夾的基底:並行派遣各用專屬子資料夾,避免同資料夾互卡。
# 空 = 以委託指定的 cwd(或 home)為基底,在其下建 _tasks/<領域>/<標題-id>/。
TASK_BASE = config.get("GUILD_TASK_BASE", "")


def _slug(s: str) -> str:
    return (re.sub(r"[^\w\-]+", "-", str(s)).strip("-") or "x")[:40]


def _is_task_folder(path: str, cid: int) -> bool:
    """path 看起來已是本委託的任務資料夾(避免續派時重複往下巢狀)。"""
    return bool(path) and "_tasks" in path and os.path.basename(path).endswith(f"-{cid}")


def _task_folder_path(row) -> str:
    """「自建資料夾」委託的專屬任務資料夾路徑(依 id 穩定,續派沿用;不建立)。"""
    keys = row.keys()
    cwd = row["cwd"] or ""
    if _is_task_folder(cwd, row["id"]):
        return cwd  # 續派:沿用既有任務資料夾,不再往下巢狀
    base = TASK_BASE or cwd or os.path.join(os.path.expanduser("~"), "guild-tasks")
    domain = (row["domain"] if "domain" in keys else None) or "general"
    return os.path.join(base, "_tasks", _slug(domain), f"{_slug(row['title'])}-{row['id']}")


def _make_task_folder(row) -> str:
    folder = _task_folder_path(row)
    os.makedirs(folder, exist_ok=True)
    return folder


def _effective_cwd(row) -> str:
    """派遣實際會在哪個資料夾跑(自建資料夾的委託 = 專屬任務資料夾)。"""
    if "self_folder" in row.keys() and row["self_folder"]:
        return _task_folder_path(row)
    return row["cwd"] or ""


def _folder_key(row) -> str:
    return os.path.normcase(os.path.abspath(_effective_cwd(row) or "."))


# 並行派遣:同時最多 PARALLEL 張(多個 headless Claude 吃 Windows 桌面堆,別開太多),
# 且**同一資料夾同時只跑一張**(共用資料夾會互相改到檔案)—— 要並行就勾「自建資料夾」。
PARALLEL = max(1, int(config.get("GUILD_DISPATCH_PARALLEL", "2")))
_running: dict[int, str] = {}  # 執行中委託 id → 資料夾 key

# 派遣佇列:背景 worker 依 FIFO 抽取 status='queued' 的委託,有空位且資料夾沒被占就開跑。
# 使用者可一次排多張,晚上掛著,worker 自動跑完(醒來收成)。
_worker_task: "asyncio.Task | None" = None
_changed: "asyncio.Event | None" = None  # 有派遣結束時叫醒 worker 補位

# STATUS 行:不做行首錨定,並容許前綴反引號/粗體(模型常寫成 `STATUS: DONE — …`、
# **STATUS: DONE**)。這是「任務能否被判定完成」的關鍵 —— 抓不到就永遠卡在回合上限。
# 取最後一個匹配(prose 中提到的以最後定案的為準);訊息抓到行尾、去掉尾端反引號。
_STATUS_RE = re.compile(
    r"STATUS:\s*(WORKING|DONE|BLOCKED)\b[ \t]*[—\-–:*]*[ \t]*([^\n`]*)",
    re.IGNORECASE)
# 結案回報四段(prompt 順序:REPORT → FILES → LESSONS → CARD),各段抓到下一段標記或結尾
# 邊界前綴容許反引號/空白:模型常把每段包成 `FILES: ...`、`CARD:` {...} 這種 code span
_REPORT_RE = re.compile(
    r"REPORT:\s*(.+?)(?=\n[\s`]*(?:FILES|LESSONS|CARD)\s*:|\Z)", re.IGNORECASE | re.DOTALL)
_FILES_RE = re.compile(
    r"FILES:\s*(.+?)(?=\n[\s`]*(?:LESSONS|CARD)\s*:|\Z)", re.IGNORECASE | re.DOTALL)
_LESSONS_RE = re.compile(
    r"LESSONS:\s*(.+?)(?=\n[\s`]*CARD\s*:|\Z)", re.IGNORECASE | re.DOTALL)
# CARD:單行 JSON;容許 CARD: 與 { 之間夾反引號/空白(模型常寫成 `CARD:` {...})
_CARD_RE = re.compile(r"CARD:[^{]*(\{.*\})", re.IGNORECASE | re.DOTALL)
# 這些「等於沒有」的字就當作無(FILES / LESSONS 通用)
_NO_VALUE = {"無", "無。", "沒有", "none", "n/a", "na", "-", "(無)", "無檔案改動"}
_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)\s*[—\-–]?\s*(.*)",
                         re.IGNORECASE | re.DOTALL)

ACCEPT_PROMPT = """你是冒險者公會櫃台的驗收員。一位冒險者宣稱完成了委託,請你獨立、嚴格地查驗產出是否真的達標——不要只信他的說詞。

## 委託目標
{goal}

## 冒險者的結案回報
{report}

## 你的工作
在目前資料夾內用唯讀工具(Read/Glob/Grep)實地查驗:回報是否屬實、目標是否真的達成、有沒有明顯遺漏或錯誤。查完後**只輸出一行**裁決,擇一:
- `VERDICT: PASS — <一句話為何達標>`
- `VERDICT: FAIL — <具體哪裡沒達成、還要補什麼(給冒險者可執行的指示)>`

只在確實達標時給 PASS。不要動任何檔案。
"""

FIRST_PROMPT = """你是冒險者公會派遣的冒險者,接下這張委託,在目前資料夾內實際動手完成它。

## 委託
- 標題:{title}
- 目標:{goal}
- 備註:{note}
{capabilities}
## 工作方式(回合制)
你現在是第 1 回合(共最多 {max_rounds} 回合)。每回合請推進一段實質進度;
回合結束時**必須**在輸出最後回報狀態,格式(擇一):
- `STATUS: WORKING — <這回合完成了什麼,下回合要做什麼>`(還沒做完)
- `STATUS: BLOCKED — <卡在哪裡,需要使用者什麼決定>`(需要人)
- 全部完成時輸出結案回報(讓公會櫃台能完整歸檔,請認真填):
  `STATUS: DONE — <一句話成果>`
  `REPORT: <2-4 句:實際做了什麼、達成了什麼、怎麼驗證的>`
  `FILES: <改動/新增的主要檔案,逗號分隔;純分析類無檔案改動則寫 無>`
  `LESSONS: <值得記住的心得、踩過的坑或後續注意事項;無則寫 無>`
{card_contract}

## 原則
- **若是修 bug:先實際重現它再動手** —— 跑指令 / 寫最小 repro,親眼看到錯誤發生,\
再定位根因;修完要能證明同一個重現步驟錯誤已消失。**別只讀 code 憑腦補猜根因**\
(讀 code 猜錯根因、加個防呆訊息就收工,是最常見的假修法)。重現不出來就回報 BLOCKED 問使用者。
- 找根因而非貼 OK 繃:優先想「能不能刪掉出錯的東西 / 改對源頭」,而不是加一層 try/except 或錯誤訊息蓋住它。
- 小步前進,每回合結束前確認改動可運作(能跑測試就跑)。
- 不確定的重大決定不要猜:用 `AskUserQuestion` 工具把選項列清楚問使用者\
(公會面板會把你的選項渲染成可點按鈕,使用者點一下就回覆),再回報 BLOCKED 等答覆。
- 使用者可能在回合之間更新目標或給新指示,以最新指示為準。
{style_directive}"""

NEXT_PROMPT = """(公會櫃台)繼續執行這張委託,第 {round} 回合(最多 {max_rounds} 回合)。

## 最新目標
{goal}
{steer_block}
繼續推進,回合結束時同樣以 `STATUS: ...`(必要時加 `REPORT: ...`)收尾。
{card_contract}{style_directive}"""

STEER_BLOCK = """
## ⚠️ 使用者中途指示(優先於原目標)
{steers}
"""

# 教學專用:一次產出字卡就好,禁止寫檔/建課程/分多次(搭配下方 read-only 工具限制)
TEACHING_PROMPT = """你是公會派遣的教學冒險者。使用者想學:{goal}

請**直接產出一組互動教學卡**——不要寫任何檔案、不要建課程大綱、不要分成多篇或多次,一次整理完即可。
用繁體中文,像跟朋友聊天一樣**生活化**:多用日常比喻、具體例子、簡單的話,少用術語(必要時用括號解釋)。
**教學不離現實:盡量用真實案例**——真實公司、真實事件、真實數字。數字請用你確實知道的公開歷史數據,\
標註年份並註明「約」;不確定的關鍵數字可用 WebSearch 查證,**絕不編造數據**(寧可選一家你數據明確的公司)。
把重點整理成 4-6 張由淺入深的卡,每張包含:
- front:一個問題或概念(口語、好懂)
- back:白話解答(用生活比喻,2-4 句)
- options:一題「3 選 1」小測驗的選項(把 back 的重點變成選擇題,讓使用者「答」而不只是「讀」;錯的選項要像真的、別太好猜)
- answer:正確選項的索引(0、1 或 2)
- chart(可選):當這個概念用「趨勢」或「對照」更好懂時,附一張示意圖表,把抽象變成看得見的關係。\
格式:{{"type":"line","title":"<標題>","xlabels":["<x1>","<x2>"],"series":[{{"label":"<系列名>","data":[數字]}}],"note":"<一句話重點>"}}。\
**data 用真實數據**:真實公司的歷年真實數字(標註年份;約略可以但必須是真的,不確定可用 WebSearch 查證);\
最多 6 個資料點、最多 2 條 series。\
例如教「股價會跟著財報走」,就附一張某真實公司(如台積電、蘋果)近幾年「營收 vs 股價」的**真實**對照折線圖,\
並在 title 或 note 標明資料年份,讓人一眼看出兩者一起動。

另外:
- next:2-3 個「可以再深入學的主題」。
- category:一個廣義「學習分類」(例如「財經學習」「程式學習」「語言學習」)。{categories_hint}

嚴格照下面兩行輸出(先 CARD 再 STATUS,不要有多餘文字):
CARD: {{"kind":"teaching","category":"<學習分類>","headline":"<這次教了什麼,一句話>","cards":[{{"front":"<問題>","back":"<生活化解答>","options":["<選項A>","<選項B>","<選項C>"],"answer":0}}],"next":["<延伸主題1>","<延伸主題2>"]}}
STATUS: DONE — <一句話成果>
"""


# 進化路線 → 派遣時給冒險者的一句「怎麼用」提示
_ROUTE_USE = {
    "skill": ("📜 skill", "可用 Skill 工具叫用,或直接參考其做法"),
    "memory": ("🧠 記憶", "這是本公會累積的通則/慣例,遵循它"),
    "tool": ("🔧 工具", "優先用這個工具,別用更笨的做法"),
    "script": ("⚙️ 腳本", "已寫好的自動化,直接跑別重造"),
}


def _capability_block(guild: str | None, session_id: str | None) -> str:
    """組「本公會已習得能力」段落注入首回合 prompt —— 讓進化成果真正被派遣用到。

    來源:①該公會所有進化紀錄(evolutions)②若為續派、原冒險者的自訂能力標籤。
    無任何能力時回空字串(prompt 不留突兀空段)。
    """
    lines: list[str] = []
    seen: set[str] = set()
    for row in db.evolutions_for_guild(guild):
        name = (row["name"] or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        icon, howto = _ROUTE_USE.get(row["route"], ("•", ""))
        detail = (row["detail"] or "").strip().replace("\n", " ")
        bits = f"{icon} `{name}`"
        if detail:
            bits += f" — {detail[:120]}"
        bits += f"({howto})" if howto else ""
        lines.append(f"- {bits}")

    if session_id:
        for row in db.list_abilities(session_id):
            name = (row["name"] or "").strip()
            if name and name not in seen:
                seen.add(name)
                lines.append(f"- 🏷️ `{name}`(你先前被標記的專長)")

    ability_block = ""
    if lines:
        body = "\n".join(lines[:12])  # 上限避免灌爆 prompt
        ability_block = (
            "\n## 🎓 本公會已習得的能力(善用它們,別重造輪子)\n"
            f"{body}\n"
        )
    return ability_block + _work_prefs_block(guild)


def _image_block(row) -> str:
    """把委託附圖的磁碟絕對路徑列進首回合 prompt —— 冒險者用 Read 讀圖理解需求。

    claude -p 的 stdin 只吃文字,無法直接餵圖;但給出實體檔路徑,Claude Code
    就能自己 Read 圖片(派遣工具白名單含 Read)。找不到檔的 URL 靜默略過。
    """
    if "images" not in row.keys() or not row["images"]:
        return ""
    try:
        urls = json.loads(row["images"])
    except (json.JSONDecodeError, TypeError):
        return ""
    paths = [uploads.abs_path(u) for u in urls if isinstance(u, str)]
    paths = [str(p) for p in paths if p]
    if not paths:
        return ""
    lines = "\n".join(f"- `{p}`" for p in paths)
    return (
        "\n## 🖼️ 委託附圖(先用 Read 工具讀這些圖片檔,理解需求再動手)\n"
        f"{lines}\n"
    )


def _work_prefs_block(guild: str | None) -> str:
    """使用者過往叫冒險者繼續做時給的指示 = 偏好的做事方式,注入首回合 prompt。

    這是學習迴路 B 的即時效果:被要求「這樣做」過的習慣,下次派遣自動帶上。
    """
    notes = [(p["note"] or "").strip() for p in db.list_work_prefs(guild, 6)]
    notes = [n for n in notes if n]
    if not notes:
        return ""
    lines = "\n".join(f"- {n}" for n in notes)
    return (
        "\n## 🧭 使用者偏好的做事方式(從過往指示累積,請照這些習慣做)\n"
        f"{lines}\n"
    )


def available() -> bool:
    return shutil.which("claude") is not None


def is_busy() -> bool:
    """是否有任何派遣在跑(夜巡據此避開忙碌時段)。"""
    return bool(_running)


def block_reason(row) -> str | None:
    """這張委託現在不能開跑的原因;None = 可以跑。"""
    if row["id"] in _running:
        return "這張委託已在執行中"
    if len(_running) >= PARALLEL:
        return f"並行派遣已滿({PARALLEL} 張),請稍候或改用排隊"
    if _folder_key(row) in _running.values():
        return "同一資料夾已有派遣在跑(勾「自建資料夾」即可並行)"
    return None


def _run_round(cmd: list[str], cwd: str | None, prompt: str,
               cid: int | None = None, rnd: int | None = None) -> dict:
    """跑一回合 claude(stream-json),回傳 {ok, text, session_id, cost, error}。

    逐行讀 stream-json:每個工具動作即時寫進 commission_events(type='step'),
    讓使用者看著冒險者一步步幹活,回合不再是黑箱。阻塞,於執行緒呼叫。

    prompt 走 stdin:Windows 的 claude.CMD shim 會把含換行的 argv 參數打壞。
    針對程序初始化失敗碼(0xC0000142 等)自動重試,連續失敗才回報並建議重開機。
    """
    for attempt in range(1, _STARTUP_RETRIES + 1):
        res = _stream_round(cmd, cwd, prompt, cid, rnd)
        if not res.get("startup_fail") or attempt >= _STARTUP_RETRIES:
            return res
        log.warning("claude 啟動失敗 0x%08X(第 %s/%s 次),稍候重試",
                    res.get("code", 0), attempt, _STARTUP_RETRIES)
        time.sleep(1.5 * attempt)
    return res  # 理論上不會到這(迴圈內已 return)


# coding 卡的 diff:每檔最多留幾段、每段前後最多幾字(避免整份檔案灌爆 JSON)
_MAX_HUNKS_PER_FILE = 5
_MAX_HUNK_CHARS = 800


def _clip(s: str) -> str:
    s = s or ""
    return s if len(s) <= _MAX_HUNK_CHARS else s[:_MAX_HUNK_CHARS] + "\n…(截斷)"


def _count_lines(text: str) -> int:
    """數一段文字有幾行(空字串 = 0 行,尾端換行不多算一行)。"""
    if not text:
        return 0
    return len(text.rstrip("\n").split("\n"))


def _hunks_stat(hs: list[dict]) -> tuple[int, int]:
    """加總一個檔所有 hunk 的新增(after 行數)/刪除(before 行數),回傳 (adds, dels)。

    以「before 行數計為刪除、after 行數計為新增」的粗略估算,足以在卡片頂部給出量感;
    Write(整檔覆寫)before 為空 → 全算新增,符合直覺。
    """
    adds = dels = 0
    for h in hs:
        dels += _count_lines(h.get("before", ""))
        adds += _count_lines(h.get("after", ""))
    return adds, dels


def _collect_hunk(hunks: dict[str, list[dict]], key: str, tool: str, ti: dict) -> None:
    """從 Edit/Write 工具輸入客觀抽出 before→after,累積成該檔的 diff 段。"""
    lst = hunks.setdefault(key, [])
    if len(lst) >= _MAX_HUNKS_PER_FILE:
        return
    if tool == "Edit":
        lst.append({"before": _clip(ti.get("old_string", "")),
                    "after": _clip(ti.get("new_string", ""))})
    elif tool == "Write":
        # 新檔 / 整檔覆寫:before 留空,after 放內容(截斷)
        lst.append({"before": "", "after": _clip(ti.get("content", ""))})
    # NotebookEdit 結構特殊,只記檔名不取 diff


# 機關師 ops 卡:最多記幾條指令、每條最多幾字(避免灌爆)
_MAX_CMDS = 30
_MAX_CMD_CHARS = 300


def _collect_cmd(cmds: list[dict], ti: dict) -> None:
    """客觀收集一條 Bash/PowerShell 指令(含冒險者自附的 description)供 ops 卡呈現。"""
    if len(cmds) >= _MAX_CMDS:
        return
    cmd = str(ti.get("command") or "").strip()
    if not cmd:
        return
    cmds.append({"cmd": cmd[:_MAX_CMD_CHARS],
                 "desc": str(ti.get("description") or "").strip()[:120]})


def _emit_step(cid: int | None, rnd: int | None, tool: str, summ: str) -> None:
    """把一個工具動作寫成時間線步驟(type='step')。cid 為 None(如驗收)時不寫。"""
    if cid is None:
        return
    icon = _STEP_ICON.get(tool, "•")
    content = f"{icon} {tool}" + (f" · {summ}" if summ else "")
    db.add_event(cid, "step", content[:200], rnd)


def _stream_round(cmd: list[str], cwd: str | None, prompt: str,
                  cid: int | None, rnd: int | None) -> dict:
    """單次執行:走共用的 claude_run.run_stream(Popen + 逐行 stream-json + 逾時樹殺),
    這裡只負責把工具動作寫成時間線步驟、客觀收集改動檔案 / diff。
    回傳含 startup_fail 供上層決定是否重試。"""
    changed: set[str] = set()  # 客觀收集本回合實際改動的檔案
    hunks: dict[str, list[dict]] = {}  # 檔案 → [{before, after}] 客觀 diff(供 coding 卡)
    cmds: list[dict] = []  # 客觀收集跑過的指令 [{cmd, desc}](供機關師 ops 卡)
    asked: list[dict] = []  # 冒險者這回合用 AskUserQuestion 問的題目(含選項),供面板渲染選單

    def on_event(d: dict) -> None:
        if d.get("type") != "assistant":
            return
        for blk in (d.get("message") or {}).get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                name = blk.get("name", "?")
                ti = blk.get("input") or {}
                _emit_step(cid, rnd, name, _summarize_tool(name, ti))
                if name in ("Edit", "Write", "NotebookEdit"):
                    fp = ti.get("file_path") or ti.get("notebook_path")
                    if fp:
                        key = str(fp).replace("\\", "/")
                        changed.add(key)
                        _collect_hunk(hunks, key, name, ti)
                elif name in ("Bash", "PowerShell"):
                    _collect_cmd(cmds, ti)
                elif name == "AskUserQuestion":
                    # headless -p 跳不出真選單,但 tool_use 的 input 帶著結構化題目+選項;
                    # 收下來讓面板渲染成可點按鈕(點了=送調整指示),取最後一次提問為準。
                    qs = ti.get("questions")
                    if isinstance(qs, list) and qs:
                        asked[:] = qs

    res = run_stream(cmd, cwd, prompt, on_event=on_event,
                     timeout=ROUND_TIMEOUT, hard_timeout=HARD_CAP)
    if not res.get("started"):
        return {"ok": False, "error": res["error"]}
    if res["killed"]:
        # 區分「閒置卡死」與「總時長爆表」,讓看板訊息說得清楚(非籠統逾時)
        if res.get("kill_reason") == "hard":
            err = f"回合超過總時長上限({HARD_CAP}s,仍未收尾)"
        else:
            err = f"回合閒置逾時({ROUND_TIMEOUT}s 毫無進度,判定卡死)"
        return {"ok": False, "error": err,
                "files": sorted(changed), "hunks": hunks, "cmds": cmds}
    if res["got_result"]:
        return {"ok": True, "text": res["text"], "session_id": res["session_id"],
                "cost": res["cost"], "files": sorted(changed), "hunks": hunks,
                "cmds": cmds, "questions": list(asked)}

    # 沒拿到 result → 失敗。判斷是否為可重試的啟動失敗
    code = (res["returncode"] or 0) & 0xFFFFFFFF
    if code in _STARTUP_FAIL_CODES and not res["text"] and res["session_id"] is None:
        return {"ok": False, "startup_fail": True, "code": code, "error": (
            f"claude 無法啟動(0x{code:08X} 程序初始化失敗)。這通常是 Windows "
            f"資源/環境暫時性問題(殘留程序過多、桌面堆積吃緊)。"
            f"建議關掉多餘視窗或重開機後再派遣。")}
    err = res["stderr"]()[:400]
    return {"ok": False, "error": err or f"claude 結束碼 {res['returncode']}"}


def _build_cmd(resume_sid: str | None, model: str | None = None,
               teaching: bool = False) -> list[str]:
    """組 claude 指令(不含 prompt;prompt 由 stdin 餵入)。

    model = 冒險者位階:委託自帶 > 全域 env > CLI 預設。
    teaching = 教學類:唯讀 + 可查網路(禁 Edit/Write,不會寫檔漂移),用真實數據教學。
    """
    cmd = [CLAUDE, "-p"]
    if resume_sid:
        cmd += ["-r", resume_sid]
    cmd += [
        # stream-json:逐步吐出工具動作,讓派遣回合不再是黑箱(即時寫進時間線)
        "--output-format", "stream-json", "--verbose",
        # 教學可查真實數據 → 放寬回合內 turns;仍禁 Edit/Write 避免漂移去寫檔
        "--max-turns", ("12" if teaching else MAX_TURNS),
        "--allowedTools",
        ("Read,Grep,Glob,WebSearch,WebFetch" if teaching else TOOLS),
        "--permission-mode", "bypassPermissions",
    ]
    chosen = model or MODEL
    if chosen:
        cmd += ["--model", chosen]
    return cmd


def _parse_status(text: str) -> tuple[str, str]:
    """從回合輸出取 (status, message)。找不到 STATUS 行時視為 WORKING。"""
    m = None
    for m in _STATUS_RE.finditer(text):
        pass  # 取最後一個
    if m is None:
        tail = text.strip().splitlines()[-1][:120] if text.strip() else "(無輸出)"
        return "WORKING", tail
    # IGNORECASE 可能抓到小寫 → 統一大寫(下游用 == "DONE" 比對);訊息去頭尾殘留符號
    return m.group(1).upper(), m.group(2).strip().strip("`—-–:* ").strip()[:300]


def _parse_lessons(text: str) -> str | None:
    m = _LESSONS_RE.search(text)
    if not m:
        return None
    val = m.group(1).strip().rstrip("`").strip()
    return None if val.lower() in _NO_VALUE or val in _NO_VALUE else (val[:1000] or None)


def _files_from_report(text: str) -> list[str]:
    """從 REPORT 的 FILES: 段解析檔案清單(客觀串流收集不到時的備援)。"""
    m = _FILES_RE.search(text)
    if not m:
        return []
    raw = m.group(1).strip()
    if raw.lower() in _NO_VALUE or raw in _NO_VALUE:
        return []
    parts = re.split(r"[,、\n]", raw)
    return [p.strip().strip("`").lstrip("-").strip() for p in parts
            if p.strip() and p.strip() not in _NO_VALUE][:30]


def _parse_card(text: str) -> dict:
    """解析 DONE 回報裡的 CARD: 單行 JSON(headline / why / notes)。失敗回 {}。"""
    m = _CARD_RE.search(text or "")
    if not m:
        return {}
    try:
        obj = json.loads(m.group(1).strip())
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _build_coding_card(text: str, headline_fallback: str,
                       hunks: dict[str, list[dict]]) -> str | None:
    """組 coding 結果卡:客觀 diff(hunks)+ LLM 的 headline/why/notes。

    無任何改檔(純分析)→ 回 None,前端就退回 markdown 呈現。
    """
    if not hunks:
        return None
    meta = _parse_card(text)
    why = meta.get("why") if isinstance(meta.get("why"), dict) else {}
    files = []
    total_adds = total_dels = 0
    for path, hs in list(hunks.items())[:20]:
        # why 的鍵可能是完整路徑或只有檔名,兩種都試
        w = why.get(path) or why.get(path.split("/")[-1]) or ""
        adds, dels = _hunks_stat(hs)
        total_adds += adds
        total_dels += dels
        # adds/dels 為新增欄位;舊卡沒有,前端會退回不顯示,故向後相容
        files.append({"path": path, "why": str(w)[:200], "hunks": hs,
                      "adds": adds, "dels": dels})
    card = {
        "kind": "coding",
        "headline": (str(meta.get("headline") or headline_fallback or "")[:120]),
        # 解決了哪一類問題(bugfix/feature/…)+ 一句話說明 —— 讓完成項目可被歸類、看懂
        "category": _norm_category(meta.get("category")),
        "solved": str(meta.get("solved") or "")[:200],
        "files": files,
        # 卡片層級總量(檔數 + 總 +/-);前端沒讀到時可自行由 files 加總,兩邊都相容
        "adds": total_adds,
        "dels": total_dels,
        "notes": str(meta.get("notes") or "")[:400],
    }
    return json.dumps(card, ensure_ascii=False)


# ── 職業 → 結果皮膚(呈現方式)──────────────────────────────
# 委託指定的 klass 決定回來的結果卡種類;未指定則有改檔就 coding、否則 markdown。
def _skin_for(klass: str | None, has_hunks: bool) -> str | None:
    if klass == "工程師":
        return "coding"
    if klass == "機關師":
        return "ops"  # 機關師顧基礎設施/服務:成果用「指令/服務」呈現,不是 code diff
    if klass == "鑑定師":
        return "review"  # 鑑定師查驗:成果是「查驗清單 + 裁決」,不是 diff
    if klass == "斥候":
        return "ship"  # 斥候出貨:成果是「送出的 commit / 推送 / 部署」,不是 diff
    if klass == "書記官":
        return "teaching"
    if not klass:
        return "coding" if has_hunks else None
    return None  # 其他職業尚無專屬皮膚 → markdown 保底


# coding 卡的「解決問題分類」slug —— 與前端 labels.js 的 PROBLEM_KINDS key 單一對應。
# 讓每張完成卡標出「到底解決哪一類問題」,已完成項目才能被歸類、一眼看懂。
_CODING_CATEGORIES = {
    "bugfix", "feature", "refactor", "perf",
    "config", "docs", "test", "ui", "chore", "other",
}
# 容忍模型回中文標籤 / 近義詞 → 正規化成上面的 slug
_CATEGORY_ALIASES = {
    "bug": "bugfix", "fix": "bugfix", "修bug": "bugfix", "修 bug": "bugfix",
    "除錯": "bugfix", "修復": "bugfix",
    "新功能": "feature", "功能": "feature", "feat": "feature",
    "重構": "refactor",
    "效能": "perf", "performance": "perf", "優化": "perf",
    "設定": "config", "環境": "config", "設定/環境": "config", "infra": "config",
    "文件": "docs", "doc": "docs", "文檔": "docs",
    "測試": "test", "tests": "test",
    "介面": "ui", "ui/ux": "ui", "樣式": "ui", "frontend": "ui",
    "雜務": "chore", "雜項": "chore",
}


def _norm_category(raw) -> str:
    """把模型回的分類字串正規化成已知 slug。認不得 → 'other'(永遠給得出一類)。"""
    s = str(raw or "").strip().lower()
    if not s:
        return "other"
    if s in _CODING_CATEGORIES:
        return s
    return _CATEGORY_ALIASES.get(s, "other")


# 各皮膚要冒險者在 DONE 回報附上的 CARD 契約(注入首回合 prompt)
_CARD_CONTRACT = {
    "coding": '  `CARD: <單行 JSON,渲染成 coding 卡。格式:'
              '{"headline":"一句話做了什麼","category":"<問題類型,擇一:bugfix 修Bug/'
              'feature 新功能/refactor 重構/perf 效能/config 設定環境/docs 文件/test 測試/'
              'ui 介面/chore 雜務/other 其他>","solved":"一句話:到底解決了哪個問題",'
              '"why":{"檔案路徑":"這個檔為什麼改"},"notes":"可選"};'
              '純分析無改檔則寫 無>`',
    "ops": '  `CARD: <單行 JSON,渲染成機關師卡(顯示跑了哪些指令、動了哪些服務/設定,'
           '不是程式碼 diff)。格式:{"headline":"一句話做了什麼",'
           '"category":"<問題類型,擇一:bugfix 修Bug/feature 新功能/refactor 重構/perf 效能/'
           'config 設定環境/docs 文件/test 測試/ui 介面/chore 雜務/other 其他>",'
           '"solved":"一句話:到底解決了哪個問題","services":["動到的服務/容器/主機,可選"],'
           '"why":{"設定檔路徑":"這個檔為什麼改"},"notes":"可選"};純查看無動作則寫 無>`',
    "review": '  `CARD: <單行 JSON,渲染成鑑定師查驗卡(顯示查了哪些項目、通過與否、總裁決,'
              '不是程式碼 diff)。格式:{"headline":"一句話查了什麼",'
              '"category":"<問題類型,擇一:bugfix 修Bug/feature 新功能/refactor 重構/perf 效能/'
              'config 設定環境/docs 文件/test 測試/ui 介面/chore 雜務/other 其他>",'
              '"verdict":"<總裁決,擇一:pass 通過/warn 有疑慮/fail 未過>",'
              '"checks":[{"item":"查驗項目","ok":true,"note":"說明,可選"}],'
              '"solved":"一句話結論","notes":"可選"};純瀏覽無查驗則寫 無>`',
    "ship": '  `CARD: <單行 JSON,渲染成斥候出貨卡(顯示送出的 commit / 推送 / 部署去哪,'
            '不是程式碼 diff)。格式:{"headline":"一句話出了什麼貨",'
            '"category":"<問題類型,擇一:bugfix 修Bug/feature 新功能/refactor 重構/perf 效能/'
            'config 設定環境/docs 文件/test 測試/ui 介面/chore 雜務/other 其他>",'
            '"target":"推去哪,如 origin/main(可選)","commits":["commit 訊息"],'
            '"solved":"一句話:交付了什麼","notes":"可選"};純查看無出貨則寫 無>`',
    "teaching": '  `CARD: <單行 JSON,渲染成教學字卡。格式:'
                '{"kind":"teaching","headline":"這次教了什麼",'
                '"cards":[{"front":"問題/概念","back":"白話解答"}]};'
                '整理成 3-6 張由淺入深的字卡>`',
}


def _card_contract(klass: str | None) -> str:
    """首回合 prompt 用:依委託指定職業給對應的 CARD 契約(無皮膚則不加)。"""
    return _CARD_CONTRACT.get(_skin_for(klass, True) or "", "")


# 內容產出型(教學/整理)不適合 code 的「小步多回合」:要求一回合內生完並回報 DONE,
# 避免內容任務被鼓勵慢慢迭代、撞到回合上限卻沒歸檔。
_ONESHOT_DIRECTIVE = (
    "\n## ⚡ 這是內容產出型任務(教學 / 整理),不要當成寫 code 的多回合工作\n"
    "請在**這一個回合內**就把完整內容整理好,直接輸出 `STATUS: DONE` + `REPORT:` + 上面的 `CARD:`;"
    "**不要**拆成多回合、不要只做一部分、不要回報 WORKING。內容型任務一次交付完整成果。\n"
)


def _style_directive(klass: str | None) -> str:
    return _ONESHOT_DIRECTIVE if _skin_for(klass, True) == "teaching" else ""


def _existing_categories() -> list[str]:
    """掃出目前教學卡已用過的學習分類,注入 prompt 讓 AI 沿用(相似的併同類)。"""
    cats: list[str] = []
    for r in db.reports_with_card():
        try:
            c = json.loads(r["card"])
        except (json.JSONDecodeError, TypeError):
            continue
        cat = (c.get("category") or "").strip() if c.get("kind") == "teaching" else ""
        if cat and cat not in cats:
            cats.append(cat)
    return cats[:20]


def _categories_hint() -> str:
    cats = _existing_categories()
    if not cats:
        return ""
    return ("\n  現有分類:" + "、".join(cats)
            + "。若這次屬於其中一類,category 請用該類原本的名字(別自創同義詞);不屬於才新增。")


def _clean_chart(ch) -> dict | None:
    """消毒教學卡的示意圖表:限制系列數 / 資料點數,數字轉 float,避免灌爆。"""
    if not isinstance(ch, dict):
        return None
    series = ch.get("series")
    if not isinstance(series, list):
        return None
    clean_series = []
    for s in series[:2]:
        if not (isinstance(s, dict) and isinstance(s.get("data"), list)):
            continue
        data = []
        for v in s["data"][:6]:
            try:
                data.append(round(float(v), 3))
            except (TypeError, ValueError):
                pass
        if data:
            clean_series.append({"label": str(s.get("label") or "")[:20], "data": data})
    if not clean_series:
        return None
    xl = ch.get("xlabels")
    clean_x = [str(x)[:12] for x in xl][:6] if isinstance(xl, list) else []
    return {
        "type": "bar" if ch.get("type") == "bar" else "line",
        "title": str(ch.get("title") or "")[:60],
        "xlabels": clean_x,
        "series": clean_series,
        "note": str(ch.get("note") or "")[:120],
    }


def _build_teaching_card(text: str, headline_fallback: str) -> str | None:
    """從 DONE 的 CARD JSON 組教學字卡。抽不到有效字卡 → None(退回 markdown)。"""
    meta = _parse_card(text)
    cards = meta.get("cards")
    if not isinstance(cards, list):
        return None
    clean = []
    for c in cards[:8]:
        if not (isinstance(c, dict) and (c.get("front") or c.get("back"))):
            continue
        item = {"front": str(c.get("front") or "")[:200],
                "back": str(c.get("back") or "")[:600]}
        # 單選小測驗(可選):把重點變成一題,讓使用者作答而非只讀
        opts = c.get("options")
        if isinstance(opts, list):
            clean_opts = [str(o).strip()[:120] for o in opts
                          if isinstance(o, (str, int, float)) and str(o).strip()][:4]
            if len(clean_opts) >= 2:
                item["options"] = clean_opts
                ans = c.get("answer")
                item["answer"] = ans if isinstance(ans, int) and 0 <= ans < len(clean_opts) else 0
        # 示意圖表(可選):把趨勢/對照畫成圖,讓概念看得見
        chart = _clean_chart(c.get("chart"))
        if chart:
            item["chart"] = chart
        clean.append(item)
    if not clean:
        return None
    nxt = meta.get("next")
    clean_next = ([str(t).strip()[:40] for t in nxt if isinstance(t, str) and t.strip()][:3]
                  if isinstance(nxt, list) else [])
    return json.dumps({
        "kind": "teaching",
        "category": str(meta.get("category") or "").strip()[:20] or "未分類",
        "headline": str(meta.get("headline") or headline_fallback or "")[:120],
        "cards": clean,
        "next": clean_next,
    }, ensure_ascii=False)


def _build_ops_card(text: str, headline_fallback: str,
                    cmds: list[dict], hunks: dict[str, list[dict]]) -> str | None:
    """組機關師 ops 結果卡:客觀收集的指令 + 動到的設定檔 + LLM 的 headline/solved/services。

    機關師沒跑任何指令也沒改設定檔(純查看)→ 回 None,前端退回 markdown。
    """
    if not cmds and not hunks:
        return None
    meta = _parse_card(text)
    steps = [{"cmd": c.get("cmd", ""), "desc": c.get("desc", "")} for c in cmds[:_MAX_CMDS]]
    why = meta.get("why") if isinstance(meta.get("why"), dict) else {}
    files = []
    for path, hs in list((hunks or {}).items())[:20]:
        w = why.get(path) or why.get(path.split("/")[-1]) or ""
        adds, dels = _hunks_stat(hs)
        files.append({"path": path, "why": str(w)[:200], "adds": adds, "dels": dels})
    svc = meta.get("services")
    services = ([str(s).strip()[:40] for s in svc if isinstance(s, str) and s.strip()][:12]
                if isinstance(svc, list) else [])
    card = {
        "kind": "ops",
        "headline": str(meta.get("headline") or headline_fallback or "")[:120],
        "category": _norm_category(meta.get("category")),
        "solved": str(meta.get("solved") or "")[:200],
        "steps": steps,
        "services": services,
        "files": files,
        "notes": str(meta.get("notes") or "")[:400],
    }
    return json.dumps(card, ensure_ascii=False)


# 鑑定師查驗裁決 slug(與前端 labels.js 的 VERDICT_META key 一致);認不得 → 保守的 warn
_VERDICT_WORDS = {
    "pass": "pass", "ok": "pass", "passed": "pass", "通過": "pass", "達標": "pass",
    "fail": "fail", "failed": "fail", "未過": "fail", "不通過": "fail", "不達標": "fail",
    "warn": "warn", "warning": "warn", "有疑慮": "warn", "部分": "warn",
}


def _norm_verdict(raw) -> str:
    return _VERDICT_WORDS.get(str(raw or "").strip().lower(), "warn")


def _build_review_card(text: str, headline_fallback: str,
                       cmds: list[dict]) -> str | None:
    """組鑑定師查驗卡:LLM 的查驗清單 + 總裁決 + 客觀收集的查驗指令。

    沒有任何查驗項目、也沒跑任何指令(純瀏覽)→ 回 None,前端退回 markdown。
    """
    meta = _parse_card(text)
    checks = []
    raw_checks = meta.get("checks")
    if isinstance(raw_checks, list):
        for c in raw_checks[:20]:
            if isinstance(c, dict) and c.get("item"):
                checks.append({
                    "item": str(c.get("item"))[:160],
                    "ok": bool(c.get("ok", c.get("pass", False))),
                    "note": str(c.get("note") or "")[:160],
                })
    steps = [{"cmd": c.get("cmd", ""), "desc": c.get("desc", "")}
             for c in (cmds or [])[:_MAX_CMDS]]
    if not checks and not steps:
        return None
    card = {
        "kind": "review",
        "headline": str(meta.get("headline") or headline_fallback or "")[:120],
        "category": _norm_category(meta.get("category")),
        "solved": str(meta.get("solved") or "")[:200],
        "verdict": _norm_verdict(meta.get("verdict")),
        "checks": checks,
        "steps": steps,
        "notes": str(meta.get("notes") or "")[:400],
    }
    return json.dumps(card, ensure_ascii=False)


# 斥候出貨卡:從收集到的指令挑出「出貨型」動作(git / 部署 / 推送)
_SHIP_CMD_RE = re.compile(
    r"\b(git|gh|docker\s+push|docker\s+compose\s+push|npm\s+publish|"
    r"deploy|rsync|scp|kubectl|helm|vercel|netlify)\b", re.IGNORECASE)


def _build_ship_card(text: str, headline_fallback: str,
                     cmds: list[dict]) -> str | None:
    """組斥候出貨卡:LLM 回報的 commit / 目標 + 客觀收集的 git/部署指令。

    沒有任何 commit、也沒跑任何出貨指令 → 回 None,前端退回 markdown。
    """
    meta = _parse_card(text)
    raw_commits = meta.get("commits")
    commits = ([str(c).strip()[:160] for c in raw_commits if isinstance(c, str) and c.strip()][:20]
               if isinstance(raw_commits, list) else [])
    ship_steps = [{"cmd": c.get("cmd", ""), "desc": c.get("desc", "")}
                  for c in (cmds or []) if _SHIP_CMD_RE.search(c.get("cmd", ""))][:_MAX_CMDS]
    if not commits and not ship_steps:
        return None
    card = {
        "kind": "ship",
        "headline": str(meta.get("headline") or headline_fallback or "")[:120],
        "category": _norm_category(meta.get("category")),
        "solved": str(meta.get("solved") or "")[:200],
        "target": str(meta.get("target") or "")[:80],
        "commits": commits,
        "steps": ship_steps,
        "notes": str(meta.get("notes") or "")[:400],
    }
    return json.dumps(card, ensure_ascii=False)


def _build_card(row, text: str, headline: str,
                hunks: dict[str, list[dict]] | None,
                cmds: list[dict] | None = None) -> str | None:
    """依委託指定職業組對應的結果卡(coding / ops / review / ship / teaching);無則 None → markdown。"""
    klass = row["klass"] if "klass" in row.keys() else None
    skin = _skin_for(klass, bool(hunks))
    if skin == "coding":
        return _build_coding_card(text, headline, hunks or {})
    if skin == "ops":
        return _build_ops_card(text, headline, cmds or [], hunks or {})
    if skin == "review":
        return _build_review_card(text, headline, cmds or [])
    if skin == "ship":
        return _build_ship_card(text, headline, cmds or [])
    if skin == "teaching":
        return _build_teaching_card(text, headline)
    return None


def _enqueue_report(row, text: str, last_msg: str,
                    files: list[str] | None = None,
                    outcome: str = "success",
                    hunks: dict[str, list[dict]] | None = None,
                    cmds: list[dict] | None = None) -> None:
    """DONE 時把結案回報送進櫃台佇列(→ wiki 歸檔 + 深度合成)。

    files:回合中「客觀」收集到的實際改動檔案(串流看到的 Edit/Write);
    為空時退而解析冒險者自報的 FILES: 段。lessons 從 LESSONS: 段解析。
    hunks:客觀收集的 before→after,組成 coding 結果卡(職業介面渲染)。
    """
    m = _REPORT_RE.search(text)
    summary = (m.group(1).strip() if m else last_msg)[:2000]
    changed = files or _files_from_report(text)
    lessons = _parse_lessons(text)
    card = _build_card(row, text, last_msg, hunks, cmds)
    db.enqueue(
        adventurer_id=row["session_id"],
        guild=row["guild"],
        title=f"[派遣] {row['title']}",
        summary=summary,
        files_changed=json.dumps(changed, ensure_ascii=False),
        lessons=lessons,
        outcome=outcome,
        card=card,
        to_wiki=not _task_memory_on(row),
    )
    # 任務記憶:成果/心得一律累積在委託自己身上,續派直接帶上(不必撈全域 wiki)
    entry = [f"[{time.strftime('%Y-%m-%d')}] {'✅' if outcome == 'success' else '🟡'} {last_msg}",
             f"成果:{summary[:600]}"]
    if lessons:
        entry.append(f"心得:{lessons[:400]}")
    if changed:
        entry.append("檔案:" + ", ".join(changed[:10]))
    db.append_task_memory(row["id"], "\n".join(entry))


def _task_memory_on(row) -> bool:
    return "task_memory" in row.keys() and bool(row["task_memory"])


def _task_memory_block(row) -> str:
    """把委託自己的任務記憶注入首回合 prompt;記憶特化時也叫冒險者別去撈全域 wiki。"""
    keys = row.keys()
    mem = (row["memory"] if "memory" in keys else None) or ""
    parts: list[str] = []
    if mem.strip():
        parts.append("\n## 🧠 本任務記憶(先前派遣累積的成果/心得,接著這些做,別重來)\n"
                     f"{mem.strip()}\n")
    if _task_memory_on(row):
        parts.append("\n## 📌 記憶範圍\n本委託的記憶特化在任務內:"
                     "**不要去讀寫全域 llm wiki**,相關脈絡以上方任務記憶與目前資料夾為準。\n")
    return "".join(parts)


def _report_text(text: str, fallback: str) -> str:
    m = _REPORT_RE.search(text)
    return (m.group(1).strip() if m else fallback)[:1500]


def _blocked_detail(text: str) -> str:
    """BLOCKED 時把冒險者的完整說明/提問留給使用者看,而非只有 STATUS 那行摘要。

    使用者的痛點:冒險者反問「該怎麼用/選哪個」時,問題正文往往寫在 STATUS 行
    之前,卻只有被截成一句話的 STATUS 摘要被存進時間線,內容看不到。這裡回傳
    冒險者這回合的完整輸出(去頭尾空白、上限 4000 字),供面板完整呈現。
    """
    return (text or "").strip()[:4000]


# 連接器 OAuth token 過期:headless 冒險者無法自己完成瀏覽器登入,只能回報受阻。
# 這種卡點看起來像「一直斷線/反覆受阻」,其實是等使用者去 Settings → Connectors
# 重新授權。偵測到就把受阻訊息標清楚(🔑),別讓它混進籠統的「受阻」害人誤判。
_REAUTH_RE = re.compile(
    r"requires?\s+re-?auth(?:oriz|enticat)ation|token expired|re-?authoriz",
    re.IGNORECASE)


def _needs_reauth(text: str) -> bool:
    return bool(_REAUTH_RE.search(text or ""))


def _acceptance_review(row, done_text: str, cwd: str | None) -> dict:
    """櫃台驗收:另起唯讀的 claude 實地查驗產出。回傳 {passed, reason, cost, errored}。

    查驗失敗(claude 起不來/逾時/無裁決)時**放行**(passed=True)避免卡死整張委託,
    但一律標 errored=True —— 上層要據此誠實回報「未把關,請自行確認」,而不是蓋上
    「✅ 驗收通過」的假章(那正是驗收閘門形同虛設的根源)。只在明確 FAIL 時退回。
    """
    goal = row["goal"] or row["title"]
    prompt = ACCEPT_PROMPT.format(goal=goal, report=_report_text(done_text, ""))
    cmd = [CLAUDE, "-p", "--output-format", "json",
           "--model", ACCEPT_MODEL, "--max-turns", "12",
           "--allowedTools", "Read,Glob,Grep",
           "--permission-mode", "plan"]
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True,
                              input=prompt.encode("utf-8"), timeout=ACCEPT_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("驗收器啟動失敗,放行但標記未把關:%s", exc)
        return {"passed": True, "reason": "驗收器無法執行", "cost": None, "errored": True}
    if proc.returncode != 0:
        log.warning("驗收器結束碼 %s,放行但標記未把關", proc.returncode)
        return {"passed": True, "reason": "驗收器執行失敗", "cost": None, "errored": True}

    raw = proc.stdout.decode("utf-8", "replace").strip()
    text, cost = raw, None
    try:
        data = json.loads(raw)
        text = (data.get("result") or data.get("text") or raw).strip()
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        pass
    m = _VERDICT_RE.search(text)
    if m is None:
        return {"passed": True, "reason": "驗收器未給明確裁決",
                "cost": cost, "errored": True}
    passed = m.group(1).upper() == "PASS"
    reason = m.group(2).strip().splitlines()[0][:300] if m.group(2).strip() else ""
    return {"passed": passed, "reason": reason, "cost": cost, "errored": False}


async def _run(cid: int, key: str) -> None:
    """跑一張派遣(呼叫前已在 _running 占好位子);結束釋放位子並叫醒佇列補位。"""
    try:
        await _loop(cid)
    except Exception:  # noqa: BLE001 — 單張失敗不可拖垮其他並行派遣
        log.exception("委託 #%s 執行時例外", cid)
        db.update_commission(cid, status="failed")
        db.add_event(cid, "system", "❌ 派遣時發生未預期錯誤,已中止(其他派遣不受影響)")
    finally:
        _running.pop(cid, None)
        if _changed is not None:
            _changed.set()
        if db.queued_ids():
            ensure_worker()  # 立即派遣結束也要讓排隊的接手


async def run_dispatch(cid: int) -> None:
    """單張立即派遣:背景 task 執行,狀態全寫進 DB / events,前端輪詢即可看到。"""
    row = db.get_commission(cid)
    if row is None:
        return
    reason = block_reason(row)
    if reason:
        db.add_event(cid, "system", f"{reason},本次未啟動")
        return
    key = _folder_key(row)
    _running[cid] = key  # 同步占位(無 await 之間),不會 TOCTOU
    await _run(cid, key)


# ── 派遣佇列(排隊自動跑,有空位就並行補上)──────────────────

def queue_running() -> bool:
    return _worker_task is not None and not _worker_task.done()


def ensure_worker() -> None:
    """確保佇列 worker 在跑;沒在跑就起一個。enqueue / 啟動時呼叫。"""
    global _worker_task
    if queue_running():
        return
    _worker_task = asyncio.create_task(_drain_queue())


async def _drain_queue() -> None:
    """把佇列中的委託跑完:FIFO 掃描,有空位且資料夾沒被占的就開跑;
    跑不了的(資料夾被占/滿位)留在佇列,等有派遣結束再補位。佇列與執行都清空才結束。"""
    global _changed
    _changed = asyncio.Event()  # 綁定當前事件迴圈(每次起 worker 重建)
    tasks: set[asyncio.Task] = set()
    while True:
        _changed.clear()
        pending = [c for c in db.queued_ids() if c not in _running]
        if not pending and not _running:
            return
        for cid in pending:
            row = db.get_commission(cid)
            # 排隊期間可能被使用者取消(狀態改回 open)或刪除 → 跳過
            if row is None or row["status"] != "queued":
                continue
            if block_reason(row):
                continue
            key = _folder_key(row)
            _running[cid] = key
            log.info("佇列:開始執行委託 #%s(並行 %s/%s)", cid, len(_running), PARALLEL)
            t = asyncio.create_task(_run(cid, key))
            tasks.add(t)  # 持有參照防 GC
            t.add_done_callback(tasks.discard)
        await _changed.wait()


async def _loop(cid: int) -> None:
    row = db.get_commission(cid)
    if row is None:
        return
    cwd = row["cwd"] or None
    # 自建任務資料夾:在專屬子資料夾執行,讓並行任務不共用同一資料夾互卡。
    # 依 id 穩定命名,續派沿用;已是任務資料夾則不再往下巢狀。
    if "self_folder" in row.keys() and row["self_folder"]:
        cwd = _make_task_folder(row)
        if cwd != row["cwd"]:
            db.update_commission(cid, cwd=cwd)
    db.update_commission(cid, status="working", stop_requested=0)
    db.add_event(cid, "system",
                 f"🚀 派遣啟動(資料夾:{cwd or '預設'},"
                 f"位階:{row['model'] or MODEL or 'CLI 預設'})")

    sid = row["session_id"]  # 之前派過可續冒險
    reworks = 0  # 櫃台驗收退回重做的次數(上限 MAX_REWORK)
    changed_files: set[str] = set()  # 跨回合累積的實際改動檔案(客觀,供回報)
    changed_hunks: dict[str, list[dict]] = {}  # 跨回合累積的 before→after(coding 卡)
    ran_cmds: list[dict] = []  # 跨回合累積跑過的指令(機關師 ops 卡)
    for rnd in range(1, MAX_ROUNDS + 1):
        row = db.get_commission(cid)
        if row is None or row["stop_requested"]:
            db.update_commission(cid, status="open", stop_requested=0)
            db.add_event(cid, "system", "⏹ 已依要求停止派遣(委託回到招募中)")
            return

        goal = row["goal"] or row["title"]
        steers = db.take_steers(cid)
        _klass = row["klass"] if "klass" in row.keys() else None
        _is_teaching = _skin_for(_klass, True) == "teaching"
        steer_block = (STEER_BLOCK.format(
            steers="\n".join(f"- {s}" for s in steers)) if steers else "")
        if _is_teaching:
            # 教學類:每回合都用「只出字卡」的專用 prompt(搭配 read-only 工具,防漂移)
            prompt = TEACHING_PROMPT.format(
                goal=goal, categories_hint=_categories_hint()) + steer_block
        elif sid is None:
            prompt = FIRST_PROMPT.format(
                title=row["title"], goal=goal,
                note=row["note"] or "(無)", max_rounds=MAX_ROUNDS,
                capabilities=(_capability_block(row["guild"], sid) + _image_block(row)
                              + _task_memory_block(row)),
                card_contract=_card_contract(_klass),
                style_directive=_style_directive(_klass))
            if steers:
                prompt += STEER_BLOCK.format(steers="\n".join(f"- {s}" for s in steers))
        else:
            prompt = NEXT_PROMPT.format(round=rnd, max_rounds=MAX_ROUNDS,
                                        goal=goal, steer_block=steer_block,
                                        card_contract=_card_contract(_klass),
                                        style_directive=_style_directive(_klass))

        log.info("委託 #%s 回合 %s/%s 開始 (sid=%s)", cid, rnd, MAX_ROUNDS,
                 (sid or "new")[:8])
        db.add_event(cid, "system",
                     f"🔄 第 {rnd} 回合開始,冒險者工作中…", rnd)
        res = await asyncio.to_thread(
            _run_round, _build_cmd(sid, row["model"], teaching=_is_teaching),
            cwd, prompt, cid, rnd)
        changed_files.update(res.get("files") or [])
        for _p, _hs in (res.get("hunks") or {}).items():
            _cur = changed_hunks.setdefault(_p, [])
            for _h in _hs:
                if len(_cur) < _MAX_HUNKS_PER_FILE:
                    _cur.append(_h)
        for _c in (res.get("cmds") or []):
            if len(ran_cmds) < _MAX_CMDS:
                ran_cmds.append(_c)

        if not res["ok"]:
            # 環境問題(claude 起不來,如 0xC0000142 桌面堆耗盡)≠ 任務失敗:
            # 標 blocked(排除後直接再派遣即可)而非 failed,看板不被機器噪音污染。
            if res.get("startup_fail"):
                db.update_commission(cid, status="blocked", rounds=rnd)
                db.add_event(cid, "system",
                             f"⚙️ 環境問題(非任務失敗):{res['error']} "
                             f"排除後可直接再派遣。", rnd)
            else:
                db.update_commission(cid, status="failed", rounds=rnd)
                db.add_event(cid, "system", f"❌ 回合 {rnd} 失敗:{res['error']}", rnd)
            return

        log.info("委託 #%s 回合 %s 回傳: sid=%s cost=%s", cid, rnd,
                 res.get("session_id"), res.get("cost"))
        if res.get("session_id"):
            sid = res["session_id"]
        if isinstance(res.get("cost"), (int, float)) and res["cost"]:
            db.add_cost("dispatch", float(res["cost"]), commission_id=cid,
                        adventurer_id=sid, guild=row["guild"],
                        model=row["model"] or MODEL or None,
                        detail=f"回合 {rnd}")
        db.update_commission(cid, session_id=sid, rounds=rnd)

        total_cost = db.commission_cost(cid)
        status, msg = _parse_status(res["text"])
        cost_s = f" (${total_cost:.3f} 累計)" if total_cost else ""
        db.add_event(cid, "progress", f"{msg}{cost_s}", rnd)

        # 內容型(教學):只要這回合吐出有效字卡就算完成,不依賴 STATUS: DONE。
        # (模型常把教學當連載、每回合生新卡卻不回報 DONE,會撞回合上限而卡住)
        if _skin_for(_klass, True) == "teaching" and _build_teaching_card(res["text"], msg):
            _enqueue_report(row, res["text"], msg,
                            files=sorted(changed_files), hunks=changed_hunks)
            db.update_commission(cid, status="done",
                                 done_at=db._now())  # noqa: SLF001 同模組層級使用
            db.add_event(cid, "system", "🏁 教學內容已完成,歸檔成字卡", rnd)
            return

        if status == "DONE":
            row = db.get_commission(cid)
            # 櫃台驗收:實地查驗產出是否達標,未過自動退回重做(閉環)。
            # 教學/內容型任務沒有可「唯讀查驗」的檔案產出,跳過驗收以免誤判失敗。
            if ACCEPTANCE and _skin_for(_klass, True) != "teaching":
                db.add_event(cid, "system", "🔍 櫃台驗收中(唯讀查驗產出)…", rnd)
                verdict = await asyncio.to_thread(
                    _acceptance_review, row, res["text"], cwd)
                if verdict.get("cost"):
                    db.add_cost("dispatch", float(verdict["cost"]), commission_id=cid,
                                adventurer_id=sid, guild=row["guild"],
                                model=ACCEPT_MODEL, detail=f"驗收 R{rnd}")
                if not verdict["passed"] and reworks < MAX_REWORK and rnd < MAX_ROUNDS:
                    # 還有回合可退回重做
                    reworks += 1
                    db.add_event(cid, "system",
                                 f"↩️ 驗收未過(第 {reworks}/{MAX_REWORK} 次退回):"
                                 f"{verdict['reason']}", rnd)
                    db.add_event(cid, "steer",
                                 f"櫃台驗收未通過:{verdict['reason']} "
                                 f"請針對這點修正後再回報 DONE。")
                    continue  # 帶著退回原因進下一回合
                if not verdict["passed"]:
                    # 已達自動重做上限仍未過,或最後一回合驗收未過 → 標 blocked 轉人工
                    _enqueue_report(row, res["text"], msg,
                                    files=sorted(changed_files), outcome="partial",
                                    hunks=changed_hunks, cmds=ran_cmds)
                    db.update_commission(cid, status="blocked")
                    reason = (
                        f"最後回合({MAX_ROUNDS})驗收未過"
                        if rnd == MAX_ROUNDS
                        else f"已達自動重做上限({MAX_REWORK})"
                    )
                    db.add_event(cid, "system",
                                 f"⚠️ 驗收仍未過({reason}),轉人工審核:{verdict['reason']}",
                                 rnd)
                    return
                if verdict.get("errored"):
                    db.add_event(cid, "system",
                                 f"⚠️ 未把關就放行({verdict['reason']}),"
                                 f"這張沒被驗收,請自行確認產出", rnd)
                else:
                    db.add_event(cid, "system",
                                 f"✅ 驗收通過:{verdict['reason']}", rnd)
            _enqueue_report(row, res["text"], msg, files=sorted(changed_files),
                            hunks=changed_hunks, cmds=ran_cmds)
            db.update_commission(cid, status="done",
                                 done_at=db._now())  # noqa: SLF001 同模組層級使用
            db.add_event(cid, "system",
                         f"🏁 委託完成(共 {rnd} 回合),結案回報已送櫃台歸檔", rnd)
            return
        if status == "BLOCKED":
            db.update_commission(cid, status="blocked")
            # 完整保留冒險者的說明/提問(不只截斷的 STATUS 摘要),面板才看得到內容
            detail = _blocked_detail(res["text"])
            if detail:
                db.add_event(cid, "question", detail, rnd)
            # 冒險者有用 AskUserQuestion 問 → 存結構化選項,面板渲染成可點選單(同回合對齊)
            asked = res.get("questions")
            if asked:
                db.add_event(cid, "options",
                             json.dumps({"questions": asked}, ensure_ascii=False), rnd)
            if _needs_reauth(res["text"]):
                db.add_event(cid, "system",
                             "🔑 需重新授權連接器:冒險者無法自己登入(token 過期)。"
                             "請到 Claude 的 Settings → Connectors 重新授權後再派遣。",
                             rnd)
            else:
                db.add_event(cid, "system",
                             f"🧱 冒險者受阻,等待你的「調整指示」後可再派遣", rnd)
            return

    db.update_commission(cid, status="blocked")
    db.add_event(cid, "system",
                 f"⏸ 已達回合上限({MAX_ROUNDS}),暫停;可下指示後再派遣繼續")
