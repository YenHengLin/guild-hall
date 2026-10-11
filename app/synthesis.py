"""深度合成 —— 櫃台把回報「綜合」進 LLM Wiki 主題頁(A4+)。

結構化存檔(reception.py)只是保底的記帳;真正符合大腦哲學的是「持續合成」:
每筆回報都應該去更新對應的主題頁、補 index.md 連結,而不是堆流水帳。
這一步交給 headless Claude(llm-wiki-manager 準則)在 wiki 目錄(config 的 wiki_dir)內執行。

護欄:
- 合成失敗不影響回報歸檔(存檔先完成,合成是加值)。
- 只允許 Read/Glob/Grep/Edit/Write 工具,不給 Bash;cwd 鎖在 wiki 目錄的上層。
- 便宜模型(預設 haiku)+ --max-turns 上限,逾時保護。
- GUILD_SYNTHESIS=0 可整個關掉。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import config, db

log = logging.getLogger("guild.synthesis")

ENABLED = config.get("GUILD_SYNTHESIS", "1") == "1"
MODEL = config.get("GUILD_SYNTH_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = int(config.get("GUILD_SYNTH_TIMEOUT", "300"))  # 秒
MAX_TURNS = "15"
CLAUDE = shutil.which("claude") or "claude"

PROMPT_TEMPLATE = """你是 LLM Wiki 的櫃台知識編纂員(llm-wiki-manager),同時也是這位冒險者回報的第一位讀者。\
櫃台已把原文存進 wiki/公會回報/ 存檔頁。你要做兩件事:(A) 把成果融進知識網格;(B) 回一份讓使用者一眼看懂的講評。

## 回報內容
- 公會(專案):{guild}
- 標題:{title}
- 結果:{outcome}
- 摘要:{summary}
- 改動檔案:{files}
- 心得/教訓:{lessons}

## (A) 融進 wiki(全部在 wiki/ 目錄內)
1. 讀 wiki/index.md,找出與本回報最相關的**主題頁**(例如專案頁、技術頁)。
2. 把成果與教訓**綜合**進該主題頁的相關段落:更新既有敘述、補充新事實;\
若與舊內容矛盾,保留兩者並以「⚠️ 矛盾」標註。更新該頁 frontmatter 的 last_verified 為今天。
3. 在該頁內文建立指向存檔頁的連結 [[{archive_page}|公會回報存檔]],並視語義補其他 [[相關頁]] 連結。
4. 若 index.md 尚無該主題頁的連結,補上一行「[[頁名]] — 一句話摘要」;找不到任何合適主題頁,\
才依標準 frontmatter 模板建新頁並掛進 index.md。
5. **不要**修改 wiki/log.md 與 wiki/公會回報/ 下的檔案(櫃台已寫好),不要執行任何指令。

## (B) 講評(給使用者看,不寫進任何檔案)
根據你剛剛為了合成而讀懂的內容,寫一份 Markdown 講評 —— 這是使用者唯一會看到的回饋,\
用第二人稱對使用者說話,不要客套或複述題目。\
每段用 1-2 句把來龍去脈講清楚(不要只丟關鍵字),但整體控制在能一眼掃完的長度;\
提到動到的關鍵檔案時,用反引號標出路徑(如 `wiki/投資心法.md`)。\
嚴格照下面骨架(沒內容的段落可整段省略,但「🎯 一句話」必留):
{style_prefs}
## 輸出(依序輸出這兩塊,不要有其他多餘文字)
先輸出講評,包在標記之間:
[[REVIEW]]
🎯 一句話:<20 字內點出這次做了什麼、現況如何>
✅ 完成了什麼
- <達成的事,寫清楚、不含糊;關鍵檔案用 `反引號` 標出>
🔧 具體作法
- <關鍵手法或決策,讓人知道「怎麼做到的」>
💡 學到的東西
- <可複用的教訓、洞見或方法;沒有就省略整段>
📚 參考來源
> <若有引用著名文獻/人物/資料來源才寫,否則整段省略。用引用區塊 + 粗體標出來源,例如:>
> **巴菲特〈致股東信 1989〉** — 以合理價買好公司,勝過以便宜價買平庸公司
⚠️ 我注意到
- <你合成時發現的:與過往回報的關聯 / 疑似重複 / 矛盾 / 未收尾的風險;沒有就省略整段>
[[/REVIEW]]

最後單獨一行輸出 wiki 動作記錄:
`SYNTH: <你更新或新建的頁面名> — <15字內做了什麼>`
若回報內容太薄無可合成,不改任何檔案,但講評照樣寫(就寫你從標題/摘要理解到的),SYNTH 輸出:`SYNTH: skip — 內容不足`
"""

# 從模型輸出抽出給使用者看的講評區塊(其餘是 wiki 編輯的副產物)
_REVIEW_RE = re.compile(r"\[\[REVIEW\]\](.*?)\[\[/REVIEW\]\]", re.DOTALL)


def available() -> bool:
    return ENABLED and shutil.which("claude") is not None


def _style_prefs_block() -> str:
    """把使用者過往退回報告時累積的敘事偏好組成一段,注入講評 prompt。"""
    prefs = db.list_reception_prefs(6)
    notes = [p["note"].strip() for p in prefs if (p["note"] or "").strip()]
    if not notes:
        return ""
    lines = "\n".join(f"- {n}" for n in notes)
    return (
        "\n## 🧝‍♀️ 使用者偏好的敘事方式(從過往退回累積,寫講評時務必遵循)\n"
        f"{lines}\n"
    )


def _build_prompt(row, archive_page: str) -> str:
    try:
        files = ", ".join(json.loads(row["files_changed"] or "[]")) or "(無)"
    except json.JSONDecodeError:
        files = "(無)"
    return PROMPT_TEMPLATE.format(
        guild=row["guild"] or "未知公會",
        title=row["title"] or "無題回報",
        outcome=row["outcome"] or "success",
        summary=(row["summary"] or "(無)").strip(),
        files=files,
        lessons=(row["lessons"] or "(無)").strip(),
        archive_page=archive_page,
        style_prefs=_style_prefs_block(),
    )


def synthesize(row, wiki_dir: Path, archive_page: str) -> dict:
    """對一筆已歸檔的回報執行深度合成 + 產生使用者講評(永不 raise)。

    回傳 {"review": str|None, "synth": str, "cost": float|None}:
    - review:給使用者看的 Markdown 講評(同一次呼叫的副產物,沒產出則 None)。
    - synth:wiki 動作的一行描述(或跳過/失敗原因),供記錄用。
    - cost:本次呼叫花費(USD)。

    阻塞呼叫 —— 由櫃台 worker 在執行緒中呼叫,天然序列化,不會兩個合成同時改 wiki。
    """
    if not ENABLED:
        return {"review": None, "synth": "關閉(GUILD_SYNTHESIS=0)", "cost": None}
    if shutil.which("claude") is None:
        return {"review": None, "synth": "跳過(找不到 claude CLI)", "cost": None}
    # 內容太薄就不花 token
    substance = f"{row['summary'] or ''}{row['lessons'] or ''}".strip()
    if len(substance) < 12:
        return {"review": None, "synth": "跳過(內容太薄)", "cost": None}

    # prompt 走 stdin:Windows claude.CMD 會把含換行的 argv 參數打壞(旗標失效)
    cmd = [
        CLAUDE, "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--max-turns", MAX_TURNS,
        "--allowedTools", "Read,Glob,Grep,Edit,Write",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(wiki_dir.parent), capture_output=True,
            input=_build_prompt(row, archive_page).encode("utf-8"),
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"review": None, "synth": f"逾時({TIMEOUT}s)", "cost": None}
    except OSError as exc:
        return {"review": None, "synth": f"無法啟動 claude({exc})", "cost": None}

    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:200]
        log.warning("合成失敗 rc=%s: %s", proc.returncode, msg)
        return {"review": None, "synth": f"失敗({msg or f'rc={proc.returncode}'})", "cost": None}

    raw = proc.stdout.decode("utf-8", "replace").strip()
    cost = None
    text = raw
    try:
        data = json.loads(raw)
        text = (data.get("result") or data.get("text") or raw).strip()
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        pass

    # 抽出給使用者看的講評(標記之間);抽不到就 None,由櫃台退回啟發式檢查
    m = _REVIEW_RE.search(text)
    review = m.group(1).strip() if m else None

    # 取最後一行的 SYNTH 標記(模型前面可能有多餘輸出)
    line = next(
        (ln.strip() for ln in reversed(text.splitlines()) if ln.strip().startswith("SYNTH:")),
        "無輸出",
    )
    if isinstance(cost, (int, float)) and cost:
        db.add_cost("synthesis", float(cost), adventurer_id=row["adventurer_id"],
                    guild=row["guild"], model=MODEL, detail="深度合成")
    return {"review": review, "synth": line.removeprefix("SYNTH:").strip(), "cost": cost}
