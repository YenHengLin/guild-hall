"""深度合成 —— 櫃台把回報「綜合」進 LLM Wiki 主題頁(A4+)。

結構化存檔(reception.py)只是保底的記帳;真正符合大腦哲學的是「持續合成」:
每筆回報都應該去更新對應的主題頁、補 index.md 連結,而不是堆流水帳。
這一步交給 headless Claude(llm-wiki-manager 準則)在 D:\\myInfo 內執行。

護欄:
- 合成失敗不影響回報歸檔(存檔先完成,合成是加值)。
- 只允許 Read/Glob/Grep/Edit/Write 工具,不給 Bash;cwd 鎖在 wiki 目錄的上層。
- 便宜模型(預設 haiku)+ --max-turns 上限,逾時保護。
- GUILD_SYNTHESIS=0 可整個關掉。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("guild.synthesis")

ENABLED = os.environ.get("GUILD_SYNTHESIS", "1") == "1"
MODEL = os.environ.get("GUILD_SYNTH_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = int(os.environ.get("GUILD_SYNTH_TIMEOUT", "300"))  # 秒
MAX_TURNS = "15"
CLAUDE = shutil.which("claude") or "claude"

PROMPT_TEMPLATE = """你是 LLM Wiki 的櫃台知識編纂員(llm-wiki-manager)。一位冒險者剛完成任務回報,\
櫃台已把原文存進 wiki/公會回報/ 存檔頁。你的工作是「深度合成」:把這筆成果融進知識網格,而不是再堆一份流水帳。

## 回報內容
- 公會(專案):{guild}
- 標題:{title}
- 結果:{outcome}
- 摘要:{summary}
- 改動檔案:{files}
- 心得/教訓:{lessons}

## 你要做的事(全部在 wiki/ 目錄內)
1. 讀 wiki/index.md,找出與本回報最相關的**主題頁**(例如專案頁、技術頁)。
2. 把回報中的成果與教訓**綜合**進該主題頁的相關段落:更新既有敘述、補充新事實;\
若與舊內容矛盾,保留兩者並以「⚠️ 矛盾」標註。更新該頁 frontmatter 的 last_verified 為今天。
3. 在該頁內文建立指向存檔頁的連結 [[{archive_page}|公會回報存檔]],並視語義補其他 [[相關頁]] 連結。
4. 若 index.md 尚無該主題頁的連結,補上一行「[[頁名]] — 一句話摘要」;若找不到任何合適的主題頁,\
才依標準 frontmatter 模板建新頁並掛進 index.md。
5. **不要**修改 wiki/log.md 與 wiki/公會回報/ 下的檔案(櫃台已寫好),不要執行任何指令。

## 輸出
完成後只輸出一行,格式:`SYNTH: <你更新或新建的頁面名> — <15字內做了什麼>`
若回報內容太薄、無可合成,不改任何檔案,輸出:`SYNTH: skip — 內容不足`
"""


def available() -> bool:
    return ENABLED and shutil.which("claude") is not None


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
    )


def synthesize(row, wiki_dir: Path, archive_page: str) -> str:
    """對一筆已歸檔的回報執行深度合成。回傳一行結果描述(永不 raise)。

    阻塞呼叫 —— 由櫃台 worker 在執行緒中呼叫,天然序列化,不會兩個合成同時改 wiki。
    """
    if not ENABLED:
        return "合成:關閉(GUILD_SYNTHESIS=0)"
    if shutil.which("claude") is None:
        return "合成:跳過(找不到 claude CLI)"
    # 內容太薄就不花 token
    substance = f"{row['summary'] or ''}{row['lessons'] or ''}".strip()
    if len(substance) < 12:
        return "合成:跳過(內容太薄)"

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
        return f"合成:逾時({TIMEOUT}s)"
    except OSError as exc:
        return f"合成:無法啟動 claude({exc})"

    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:200]
        log.warning("合成失敗 rc=%s: %s", proc.returncode, msg)
        return f"合成:失敗({msg or f'rc={proc.returncode}'})"

    raw = proc.stdout.decode("utf-8", "replace").strip()
    cost = None
    text = raw
    try:
        data = json.loads(raw)
        text = (data.get("result") or data.get("text") or raw).strip()
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        pass

    # 取最後一行的 SYNTH 標記(模型前面可能有多餘輸出)
    line = next(
        (ln.strip() for ln in reversed(text.splitlines()) if ln.strip().startswith("SYNTH:")),
        text.splitlines()[-1].strip() if text else "無輸出",
    )
    cost_s = f" (${cost:.4f})" if isinstance(cost, (int, float)) else ""
    return f"合成:{line.removeprefix('SYNTH:').strip()}{cost_s}"
