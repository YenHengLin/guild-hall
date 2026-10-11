"""公會夜巡 —— NPC 冒險者在預算 / 時段內自主巡視各專案,產出「改善靈感卡」。

這讓公會像遊戲:即使你沒下委託,冒險者也會在你設定的時段(如深夜)、且當日花費
未超預算時,自己去看某個 project,提出「這裡可以更好」的具體建議。你隔天醒來就有
一疊靈感卡可挑,一鍵採納即變成布告欄委託派出去做。

護欄(全部可在面板調整):
- **總開關** enabled:關掉就完全不跑。
- **每日預算** daily_budget_usd:當日「全公會」總花費(含你自己的派遣)達標即停巡。
- **時段** hour_start~hour_end:只在這段本地時間巡(預設深夜 1–6 點)。
- **唯讀**:巡邏只給 Read/Glob/Grep,絕不改任何檔;產出只是靈感卡,要不要做由你決定。
- 不與你的派遣搶資源:偵測到有派遣在跑就跳過這輪。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone

from . import config, db, scanner
from .claude_run import run_stream
from .models import MuseConfig
from .stream_recall import _summarize_tool

log = logging.getLogger("guild.muse")

# 工具 → 進度圖示(和派遣時間線一致)
_STEP_ICON = {
    "Read": "📖", "Grep": "🔍", "Glob": "🔍",
    "Bash": "⚙️", "WebSearch": "🌐", "WebFetch": "🌐",
}

# 夜巡即時進度(供面板輪詢 /muse/status 顯示「現在在巡什麼、到哪了」)
_progress_lock = threading.Lock()
_progress: dict = {
    "running": False, "guild": None, "step": "", "steps": [],
    "started_at": None, "added": None, "cost": None,
}


def _progress_start(guild: str) -> None:
    with _progress_lock:
        _progress.update(running=True, guild=guild, step="🚶 抵達公會,開始巡視…",
                         steps=[], started_at=datetime.now(timezone.utc).isoformat(),
                         added=None, cost=None)


def _progress_step(text: str) -> None:
    with _progress_lock:
        _progress["step"] = text
        _progress["steps"] = (_progress["steps"] + [text])[-40:]


def _progress_end(step: str, added: int | None = None, cost=None) -> None:
    with _progress_lock:
        _progress.update(running=False, step=step, added=added, cost=cost)


def get_progress() -> dict:
    with _progress_lock:
        return {**_progress, "steps": list(_progress["steps"])}

CLAUDE = shutil.which("claude") or "claude"
TIMEOUT = int(config.get("GUILD_MUSE_TIMEOUT", "240"))
MAX_TURNS = config.get("GUILD_MUSE_TURNS", "14")
SETTING_KEY = "muse"
_LAST_GUILD_KEY = "muse_last_guild"

_KIND_OK = {"improve", "bug", "refactor", "idea", "test", "doc", "perf", "ux"}
_EFFORT_OK = {"S", "M", "L"}

# 一次只跑一輪夜巡(避免多輪並行搶資源)
_lock = asyncio.Lock()

MUSE_PROMPT = """你是冒險者公會的「夜巡冒險者」。現在是深夜巡邏時間,你來到 **{guild}** 這座公會\
(專案資料夾),任務是:用唯讀工具實地看懂這個專案,提出**具體、可執行、真的能讓它更好**的改善靈感。

## 巡邏方式(唯讀,禁止改任何檔)
1. 先讀 README / 主要進入點 / 目錄結構,搞懂這專案在做什麼、目前狀態。
2. 挑幾個實際檔案看,找**真實存在**的問題或機會:例如可讀性差的區塊、缺測試、\
重複程式碼、明顯 bug 風險、效能隱憂、過時依賴、缺文件、UX 粗糙處、可自動化的手動流程。
3. 不要空泛喊「加測試」「寫文件」;要指出**在哪個檔、哪個點、為什麼、怎麼做**。
{existing}

## 寫給誰看(超重要)
主人白天打開只想看到**人話**:這次發現了什麼問題或機會、下一步該做什麼。\
**禁止術語堆砌**(少用 refactor / coupling / boilerplate 這類黑話,真要提就順帶白話解釋)。\
想像你在跟一個「懂產品但不想讀 code」的朋友講:一句就聽懂,聽完知道下一步怎麼動。

## 輸出(嚴格)
只輸出一個 JSON 陣列,最多 {n} 個物件,依價值高到低排序;每個物件:
[
  {{
    "kind": "improve|bug|refactor|idea|test|doc|perf|ux",
    "title": "<一句話點題,具體到看標題就懂要動什麼>",
    "headline": "<一句白話:這次發現了什麼問題或機會。像跟朋友講,不要術語,最多 40 字>",
    "next_step": "<一句白話:建議的下一步該做什麼,具體到能直接開工>",
    "impact": "高|中|低",
    "body": "<2-4 句 markdown 補充細節:涉及哪些檔案/位置、為什麼值得做、預期效益(給想深入的人看)>",
    "effort": "S|M|L",
    "auto_safe": true 或 false
  }}
]

- `headline` 與 `next_step` 是主人一眼掃的重點,**務必白話、務必填**;`body` 才放技術細節。
- `impact` 是這件事做了對專案的價值/影響有多大:高 / 中 / 低。

### auto_safe 怎麼判定(這關係到會不會被自動執行,請保守)
只有同時滿足以下才給 `true`:改動**低風險、自足(不牽動公開介面/資料格式)、不需要人做設計決定**、\
且做完容易客觀驗證。例如:補一個明確的單元測試、修一個明顯的 typo/錯字、加 docstring/註解、\
把重複片段抽成小函式。凡是涉及**設計取捨、改 API/資料結構、修需要判斷的邏輯 bug、跨檔大改、\
或你自己也不確定的**,一律給 `false`(留給人工挑)。

若這專案目前很乾淨、沒有值得提的改善,輸出空陣列 []。只輸出 JSON,不要其他文字。
"""


# ── 設定 ────────────────────────────────────────────────────────

def load_config() -> MuseConfig:
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return MuseConfig()
    try:
        return MuseConfig(**json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return MuseConfig()


def save_config(cfg: MuseConfig) -> MuseConfig:
    db.set_setting(SETTING_KEY, cfg.model_dump_json())
    return cfg


def available() -> bool:
    return shutil.which("claude") is not None


# ── 護欄 ────────────────────────────────────────────────────────

def _within_window(cfg: MuseConfig, now: datetime) -> bool:
    """現在本地時間是否落在允許巡邏的時段(支援跨夜,如 22→6)。"""
    h = now.hour
    s, e = cfg.hour_start % 24, cfg.hour_end % 24
    if s == e:
        return True  # 整天皆可
    if s < e:
        return s <= h < e
    return h >= s or h < e  # 跨夜


def spent_today() -> float:
    """今日(本地零點起)全公會總花費 USD。"""
    local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0)
    return db.spent_since(local_midnight.astimezone(timezone.utc).isoformat())


def budget_left(cfg: MuseConfig) -> float:
    return round(cfg.daily_budget_usd - spent_today(), 4)


# ── 巡邏 ────────────────────────────────────────────────────────

def _pick_guild(cfg: MuseConfig) -> tuple[str, str] | None:
    """挑一座公會巡(round-robin,接續上次)。回傳 (name, path);無可巡則 None。"""
    guilds = [g for g in scanner.scan_guilds() if g.path and os.path.isdir(g.path)]
    if cfg.include_guilds:
        guilds = [g for g in guilds if g.name in cfg.include_guilds]
    if not guilds:
        return None
    last = db.get_setting(_LAST_GUILD_KEY)
    names = [g.name for g in guilds]
    start = (names.index(last) + 1) if last in names else 0
    g = guilds[start % len(guilds)]
    return g.name, g.path


_JSON_DECODER = json.JSONDecoder()


def _iter_json_candidates(text: str):
    """依序產出可能的 JSON 片段:先 ```json/``` fenced 區塊,再每個 '[' 起點。"""
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        yield m.group(1).strip()
    for i, ch in enumerate(text):
        if ch == "[":
            yield text[i:]


def _extract_ideas(text: str) -> list[dict]:
    """從模型輸出撈第一個 JSON 陣列。

    容忍:```json 包裹、以及陣列前夾帶含中括號的說明(如夜巡 body 裡的
    `[連結](url)`、`[註]`)。做法用 raw_decode 對每個候選起點嘗試——它只吃到
    合法 JSON 結尾即停、遇雜訊就報錯,故雜訊 '[' 會被跳過,不像貪婪 `\\[.*\\]`
    那樣從第一個 '[' 吞到最後一個 ']' 導致整段解析失敗、靈感靜默歸零。"""
    for candidate in _iter_json_candidates(text):
        try:
            data, _ = _JSON_DECODER.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    return []


def _run_survey(prompt: str, cwd: str, model: str) -> dict:
    """唯讀巡視(Read/Glob/Grep),逐行讀 stream-json 即時更新進度。

    回傳 {ok, text/error, cost}。阻塞,於執行緒呼叫。Popen / 逐行解析 / 逾時樹殺看門狗
    都在共用的 claude_run.run_stream;這裡只負責把每步動作更新到面板進度。
    """
    cmd = [
        CLAUDE, "-p", "--output-format", "stream-json", "--verbose",
        "--model", model, "--max-turns", MAX_TURNS,
        "--allowedTools", "Read,Glob,Grep", "--permission-mode", "plan",
    ]

    def on_event(d: dict) -> None:
        if d.get("type") != "assistant":
            return
        for blk in (d.get("message") or {}).get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                name = blk.get("name", "?")
                icon = _STEP_ICON.get(name, "•")
                summ = _summarize_tool(name, blk.get("input") or {})
                _progress_step(f"{icon} {name}" + (f" · {summ}" if summ else ""))
            elif isinstance(blk, dict) and blk.get("type") == "text":
                snippet = (blk.get("text") or "").strip().splitlines()
                if snippet:
                    _progress_step(f"💭 {snippet[0][:80]}")

    res = run_stream(cmd, cwd, prompt, on_event=on_event, timeout=TIMEOUT)
    if not res.get("started"):
        return {"ok": False, "error": res["error"]}
    if res["killed"]:
        return {"ok": False, "error": f"夜巡逾時({TIMEOUT}s),已終止程序"}
    if not res["got_result"]:
        err = res["stderr"]()[:300]
        return {"ok": False, "error": err or f"claude 結束碼 {res['returncode']}"}
    return {"ok": True, "text": res["text"], "cost": res["cost"]}


# 白話欄位(headline/next_step/impact)DB 沒有對應欄位,故打包進 body 前綴的
# JSON 信封;router 讀出時拆封還原成獨立欄位。舊資料沒信封 → 原樣當 body。
_BODY_ENVELOPE = "﻿MUSE1"  # 罕見前綴,避免撞到正常 markdown 開頭
_IMPACT_OK = {"高", "中", "低"}


def _pack_body(headline: str | None, next_step: str | None,
               impact: str | None, body: str | None) -> str | None:
    """把白話三欄 + 詳述 body 打包成一個字串存進 muse_ideas.body。

    有任一白話欄位才用 JSON 信封;完全沒有就退回純 body(相容舊格式)。
    """
    hl = (headline or "").strip()[:120] or None
    ns = (next_step or "").strip()[:200] or None
    imp = (impact or "").strip()
    imp = imp if imp in _IMPACT_OK else None
    bd = (body or "").strip()[:2000] or None
    if not (hl or ns or imp):
        return bd
    env = json.dumps({"headline": hl, "next_step": ns, "impact": imp, "body": bd},
                     ensure_ascii=False)
    return _BODY_ENVELOPE + env


def unpack_body(raw: str | None) -> dict:
    """把 body 欄位拆回 {headline, next_step, impact, body};舊資料照原樣塞 body。"""
    if raw and raw.startswith(_BODY_ENVELOPE):
        try:
            d = json.loads(raw[len(_BODY_ENVELOPE):])
            return {"headline": d.get("headline"), "next_step": d.get("next_step"),
                    "impact": d.get("impact"), "body": d.get("body")}
        except (json.JSONDecodeError, TypeError):
            pass
    return {"headline": None, "next_step": None, "impact": None, "body": raw}


def goal_from_body(title: str, raw: str | None) -> str:
    """由靈感組出派遣目標:白話下一步 + 詳述,拆掉信封、不外洩 JSON。"""
    parts = unpack_body(raw)
    lines: list[str] = []
    if parts["next_step"]:
        lines.append(f"下一步:{parts['next_step']}")
    if parts["body"]:
        lines.append(parts["body"])
    return "\n\n".join(lines).strip() or (parts["headline"] or "") or title


def _goal_from_row(r) -> str:
    return goal_from_body(r["title"], r["body"])


_EFFORT_RANK = {"S": 1, "M": 2, "L": 3}


def _effort_ok(effort: str | None, max_effort: str) -> bool:
    """effort 是否在自動執行允許的工作量以內(S<=M<=L)。"""
    return _EFFORT_RANK.get((effort or "L").upper(), 3) <= \
        _EFFORT_RANK.get((max_effort or "S").upper(), 1)


# 正規化標題供近似去重:去空白/標點、轉小寫(\w 在 Python3 含中日韓,CJK 字保留)
_NORM_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm_title(s: str) -> str:
    return _NORM_RE.sub("", (s or "").lower())


def _existing_block(guild: str) -> str:
    """把該公會現有靈感標題組成一段注入 prompt,叫夜巡別重複產同一則(止血)。"""
    titles = db.idea_titles_for_guild(guild, 60)
    if not titles:
        return ""
    lines = "\n".join(f"- {t}" for t in titles)
    return (
        "\n## ⛔ 這些靈感已經提過了,別再重複(連換句話說的同義版本也不要)\n"
        f"{lines}\n"
        "只找上面**沒有**的全新問題/機會;若真的沒有新的,就輸出空陣列 []。\n"
    )


def _store_ideas(guild: str, cwd: str, items: list[dict], limit: int) -> list[dict]:
    """存入去重後的靈感,回傳實際建立的清單(含 id + 供自動執行判斷的欄位)。"""
    # 近似去重:先撈該公會現有標題正規化成集合,新標題正規化後撞到就跳過
    seen_norm = {_norm_title(t) for t in db.idea_titles_for_guild(guild, 200)}
    created: list[dict] = []
    for d in items[:limit]:
        title = (d.get("title") or "").strip()[:160]
        norm = _norm_title(title)
        if not title or norm in seen_norm or db.idea_title_exists(guild, title):
            continue
        seen_norm.add(norm)  # 同一輪內部也去重
        kind = (d.get("kind") or "idea").strip().lower()
        kind = kind if kind in _KIND_OK else "idea"
        effort = (d.get("effort") or "").strip().upper()
        effort = effort if effort in _EFFORT_OK else None
        body = _pack_body(d.get("headline"), d.get("next_step"),
                          d.get("impact"), d.get("body"))
        iid = db.add_idea(guild=guild, cwd=cwd, kind=kind, title=title,
                          body=body, effort=effort,
                          auto_safe=(d.get("auto_safe") is True))
        created.append({"id": iid, "title": title})
    return created


# 舊靈感沒有 auto_safe 標記時,退而用保守啟發式:只有這些類型才可自動做
_AUTO_SAFE_KINDS = {"test", "doc", "refactor", "perf"}


def _is_auto_candidate(row, cfg: MuseConfig) -> bool:
    """這張靈感是否適合自動執行:工作量夠小 + (模型標安全 或 舊資料屬安全類型)。"""
    if not row["cwd"] or not _effort_ok(row["effort"], cfg.auto_max_effort):
        return False
    safe = row["auto_safe"]  # 1 / 0 / None
    if safe == 1:
        return True
    if safe == 0:
        return False
    return (row["kind"] or "") in _AUTO_SAFE_KINDS  # None:保守啟發式


def _select_backlog(cfg: MuseConfig, limit: int) -> list:
    """從既有 new 靈感挑可自動執行的,排序:模型標安全者優先 → 工作量小 → 舊的先。"""
    cands = [r for r in db.new_ideas() if _is_auto_candidate(r, cfg)]
    cands.sort(key=lambda r: (0 if r["auto_safe"] == 1 else 1,
                              _EFFORT_RANK.get((r["effort"] or "L").upper(), 3),
                              r["id"]))
    return cands[:max(0, limit)]


def _enqueue_from_ideas(cfg: MuseConfig, rows: list) -> list[int]:
    """把選中的靈感建成委託並排入派遣佇列(免人工同意)。每張先看預算,超了就停。

    回傳排入的委託 id;呼叫端(事件迴圈內)需接著 dispatch.ensure_worker()。
    """
    queued: list[int] = []
    for r in rows:
        if budget_left(cfg) <= 0:
            break
        cid = db.add_commission(r["guild"], r["title"],
                                note="🌙 夜巡自動執行(免人工同意)")
        db.update_commission(cid, goal=(_goal_from_row(r) or r["title"]), cwd=r["cwd"],
                             model=cfg.model, status="queued")
        db.set_idea_status(r["id"], "promoted")
        db.add_event(cid, "system",
                     "🌙 由夜巡自動挑選(安全、低風險、工作量小),已排入派遣佇列")
        queued.append(cid)
        log.info("夜巡自動執行:靈感 #%s → 委託 #%s(%s)",
                 r["id"], cid, (r["title"] or "")[:30])
    return queued


def run_patrol_once(manual: bool = False) -> dict:
    """跑一輪夜巡(阻塞,於執行緒呼叫)。manual=True 由面板手動觸發,略過時段/開關檢查。

    回傳 {ok, skipped?, reason?, guild?, added?, cost?}。
    """
    cfg = load_config()
    if not manual and not cfg.enabled:
        return {"ok": True, "skipped": True, "reason": "夜巡未啟用"}
    if not available():
        return {"ok": False, "error": "找不到 claude CLI"}
    if not manual and not _within_window(cfg, datetime.now().astimezone()):
        return {"ok": True, "skipped": True,
                "reason": f"非巡邏時段({cfg.hour_start}–{cfg.hour_end} 點)"}
    left = budget_left(cfg)
    if left <= 0:
        return {"ok": True, "skipped": True,
                "reason": f"今日預算已用罄(上限 ${cfg.daily_budget_usd})"}

    picked = _pick_guild(cfg)
    if picked is None:
        return {"ok": True, "skipped": True, "reason": "沒有可巡的公會"}
    guild, cwd = picked

    _progress_start(guild)
    prompt = MUSE_PROMPT.format(guild=guild, n=cfg.ideas_per_run,
                                existing=_existing_block(guild))
    res = _run_survey(prompt, cwd, cfg.model)
    db.set_setting(_LAST_GUILD_KEY, guild)  # 無論成敗都輪替,避免卡在同一座
    if res.get("cost"):
        db.add_cost("muse", float(res["cost"]), guild=guild, model=cfg.model,
                    detail="夜巡")
    if not res["ok"]:
        _progress_end(f"❌ {res['error']}", cost=res.get("cost"))
        return {"ok": False, "error": res["error"], "guild": guild}
    items = _extract_ideas(res["text"])
    created = _store_ideas(guild, cwd, items, cfg.ideas_per_run)
    added = len(created)
    # 自動執行:從「整個 new 積壓」挑(含這輪剛產的 + 既有清單),不限於本輪新產
    queued = (_enqueue_from_ideas(cfg, _select_backlog(cfg, cfg.auto_per_run))
              if cfg.auto_execute else [])
    tail = f",自動接下 {len(queued)} 張" if queued else ""
    _progress_end(f"✅ 巡完 {guild},新增 {added} 張靈感卡{tail}", added=added,
                  cost=res.get("cost"))
    log.info("夜巡 %s:產出 %d 張靈感卡,自動執行 %d 張(cost=%s)",
             guild, added, len(queued), res.get("cost"))
    return {"ok": True, "guild": guild, "added": added, "queued": queued,
            "cost": res.get("cost")}


# ── 背景排程 ────────────────────────────────────────────────────

_task: "asyncio.Task | None" = None


async def _patrol_loop() -> None:
    """常駐背景 loop:每 interval 分鐘檢查一次,自我把關(開關/時段/預算)。"""
    log.info("夜巡背景 loop 啟動")
    # 開機不立刻跑,先睡一輪,避免啟動瞬間就吃資源
    while True:
        cfg = load_config()
        await asyncio.sleep(max(5, cfg.interval_min) * 60)
        try:
            from . import dispatch
            if dispatch.is_busy() or dispatch.queue_running():
                continue  # 有派遣在跑,這輪讓路
            async with _lock:
                res = await asyncio.to_thread(run_patrol_once, False)
            if res.get("queued"):
                dispatch.ensure_worker()  # 自動執行的委託:起佇列 worker 依序做掉
            if not res.get("ok"):
                log.warning("夜巡失敗:%s", res.get("error"))
        except Exception:  # noqa: BLE001 loop 要活著
            log.exception("夜巡 loop 例外")


def ensure_loop() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_patrol_loop())


async def run_now() -> dict:
    """面板手動觸發一輪(略過時段/開關;仍守預算)。自動執行的委託起佇列 worker。"""
    async with _lock:
        res = await asyncio.to_thread(run_patrol_once, True)
    if res.get("queued"):
        from . import dispatch
        dispatch.ensure_worker()
    return res


async def auto_do_now(count: int | None = None) -> dict:
    """不巡邏,直接從既有靈感清單挑可自動執行的做掉(手動觸發)。仍守預算與安全篩選。

    回傳 {ok, queued:[cids], reason?}。
    """
    cfg = load_config()
    if budget_left(cfg) <= 0:
        return {"ok": True, "queued": [],
                "reason": f"今日預算已用罄(上限 ${cfg.daily_budget_usd})"}
    n = count or cfg.auto_per_run
    rows = _select_backlog(cfg, n)
    if not rows:
        return {"ok": True, "queued": [],
                "reason": "目前清單沒有適合自動執行的靈感(需安全、且工作量在上限內)"}
    queued = _enqueue_from_ideas(cfg, rows)
    if queued:
        from . import dispatch
        dispatch.ensure_worker()
    return {"ok": True, "queued": queued}
