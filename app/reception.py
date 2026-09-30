"""櫃台小姐 —— 單一消費者 worker,把冒險者回報寫回 LLM Wiki。

為什麼要單一 worker:多位冒險者可同時回報,但寫入的是共享檔(wiki/log.md)。
只讓一個 worker 逐筆處理,對共享檔的寫入就天然序列化,不會互相覆蓋。
(這正是把「佇列」放進系統的理由。)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import date
from pathlib import Path

from . import db, synthesis

log = logging.getLogger("guild.reception")

WIKI_DIR = Path(os.environ.get("GUILD_WIKI_DIR", r"D:\myInfo\wiki"))
ARCHIVE_DIR = WIKI_DIR / "公會回報"
LOG_FILE = WIKI_DIR / "log.md"
# 預設不自動 git commit(D:\ 是個龐大的 repo,避免意外提交)。設 1 才開。
GIT_COMMIT = os.environ.get("GUILD_GIT_COMMIT", "0") == "1"

_INVALID = re.compile(r'[<>:"/\\|?*]')

OUTCOME_EMOJI = {"success": "✅", "partial": "🟡", "failed": "❌"}

# 櫃台審查技能:隨累積處理報告數解鎖,實際套用在寫入檢查上
REVIEW_SKILLS = [
    ("format", "格式檢查", "確保標題與內容完整,太簡略會標註", 5),
    ("dup", "重複偵測", "抓出同公會的同名重複回報", 15),
    ("link", "關聯比對", "在存檔頁列出相關的過往回報", 30),
    ("conflict", "矛盾檢查", "找出與舊紀錄衝突的說法", 50),
]


def reception_status(done: int) -> tuple[int, int | None, list[dict]]:
    """依已處理報告數算櫃台等級、下一門檻、審查技能解鎖狀態。"""
    skills = []
    level = 1
    next_at = None
    for key, name, desc, at in REVIEW_SKILLS:
        unlocked = done >= at
        if unlocked:
            level += 1
        elif next_at is None:
            next_at = at
        skills.append({"key": key, "name": name, "desc": desc,
                       "at": at, "unlocked": unlocked})
    return level, next_at, skills


def _unlocked_keys(done: int) -> set[str]:
    return {k for k, _n, _d, at in REVIEW_SKILLS if done >= at}


def _safe_name(name: str) -> str:
    return _INVALID.sub("_", (name or "未知公會").strip()) or "未知公會"


def _archive_page(guild: str) -> Path:
    return ARCHIVE_DIR / f"{_safe_name(guild)}.md"


def _ensure_archive(path: Path, guild: str) -> None:
    if path.exists():
        return
    path.write_text(
        f"""---
title: {guild} 公會回報
tags:
  - 公會回報
  - 知識管理
completeness: low
status: draft
---

# {guild} 公會回報存檔

> 由冒險者公會櫃台自動記錄。每筆為一次任務完成回報。
""",
        encoding="utf-8",
    )


def _append_archive(path: Path, entry: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def _prepend_log(entry_line: str) -> None:
    """把一行 log 插到 log.md 的介紹區塊之後(reverse chronological)。"""
    if not LOG_FILE.exists():
        return
    text = LOG_FILE.read_text(encoding="utf-8")
    marker = "\n---\n"
    idx = text.find(marker)
    if idx == -1:
        LOG_FILE.write_text(entry_line + "\n" + text, encoding="utf-8")
        return
    cut = idx + len(marker)
    new = text[:cut] + "\n" + entry_line + "\n" + text[cut:]
    LOG_FILE.write_text(new, encoding="utf-8")


def _git_commit(paths: list[Path], msg: str) -> str:
    if not GIT_COMMIT:
        return "written (git commit disabled)"
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=str(WIKI_DIR),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "add", *[str(p) for p in paths]], cwd=top, check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=top, check=True,
                       capture_output=True, text=True)
        return "committed"
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("git commit 失敗: %s", exc)
        return f"written (commit failed: {exc})"


def _card_kind(row) -> str | None:
    card = row["card"] if "card" in row.keys() else None
    if not card:
        return None
    try:
        return json.loads(card).get("kind")
    except (json.JSONDecodeError, TypeError):
        return None


def process_report(row) -> tuple[str, str]:
    """把一筆回報寫入 wiki。回傳 (status, result)。IO 密集,在執行緒中呼叫。"""
    # 教學卡是自足的學習成品:不寫工作回報 wiki、不跑櫃檯合成(省 token),直接完成。
    if _card_kind(row) == "teaching":
        return "done", None
    guild = row["guild"] or "未知公會"
    title = row["title"] or "無題回報"
    today = date.today().strftime("%Y-%m-%d")
    try:
        files = json.loads(row["files_changed"] or "[]")
    except json.JSONDecodeError:
        files = []
    emoji = OUTCOME_EMOJI.get(row["outcome"], "✅")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    page = _archive_page(guild)
    _ensure_archive(page, guild)

    # 櫃台審查:依已處理報告數決定她會做哪些檢查
    done_so_far = db.counts().get("done", 0)
    unlocked = _unlocked_keys(done_so_far)
    notes: list[str] = []

    existing = page.read_text(encoding="utf-8") if page.exists() else ""

    if "format" in unlocked:
        thin = not row["summary"] or len(row["summary"].strip()) < 12
        notes.append("⚠️ 內容偏簡略,建議補充" if thin else "✅ 格式完整")
    if "dup" in unlocked and f"] {title}" in existing:
        notes.append("🔁 疑似重複(存檔頁已有同名回報)")
    if "conflict" in unlocked and row["lessons"] and "但" in (row["lessons"] or ""):
        notes.append("🔎 含轉折語,已留意是否與舊紀錄矛盾")
    review = " · ".join(notes) if notes else "（新手櫃台:僅存檔,尚未解鎖審查）"

    parts = [f"\n## [{today}] {emoji} {title}\n"]
    if row["summary"]:
        parts.append(f"\n{row['summary']}\n")
    if files:
        parts.append("\n**改動檔案**:\n")
        parts.extend(f"- `{f}`\n" for f in files)
    if row["lessons"]:
        parts.append(f"\n**心得 / 教訓**:{row['lessons']}\n")
    if notes:
        parts.append(f"\n> 🧝‍♀️ 櫃台審查:{review}\n")
    if row["adventurer_id"]:
        parts.append(f"\n<sub>冒險者 `{row['adventurer_id'][:8]}`</sub>\n")
    _append_archive(page, "".join(parts))

    _prepend_log(f"## [{today}] task | {guild} 回報:{title}")

    # A4+ 深度合成:同一次 headless Claude 呼叫既把回報綜合進主題頁 + index.md,
    # 也回一份給使用者看的講評(失敗不影響歸檔)。
    synth = synthesis.synthesize(row, WIKI_DIR, page.stem)

    committed = _git_commit([WIKI_DIR], f"公會回報: {guild} — {title}")

    # 顯示優先用 LLM 講評(它真的讀懂了報告);合成沒跑/沒產出才退回啟發式檢查。
    foot = " · ".join(x for x in [
        f"🗂 {page.name}",
        synth["synth"],
        f"${synth['cost']:.4f}" if isinstance(synth.get("cost"), (int, float)) and synth["cost"] else "",
        committed,
    ] if x)
    if synth.get("review"):
        result = f"{synth['review']}\n\n<sub>{foot}</sub>"
    else:
        result = f"{review}\n\n<sub>{foot}</sub>"
    return "done", result


async def worker_loop(poll_sec: float = 2.0) -> None:
    """櫃台背景工人:逐筆序列化處理佇列。"""
    log.info("櫃台工人啟動,wiki=%s git_commit=%s", WIKI_DIR, GIT_COMMIT)
    while True:
        row = await asyncio.to_thread(db.claim_next)
        if row is None:
            await asyncio.sleep(poll_sec)
            continue
        rid = row["id"]
        log.info("櫃台處理回報 #%s: %s", rid, row["title"])
        try:
            status, result = await asyncio.to_thread(process_report, row)
        except Exception as exc:  # noqa: BLE001 — worker 要活著
            log.exception("回報 #%s 處理失敗", rid)
            status, result = "failed", str(exc)
        await asyncio.to_thread(db.finish, rid, status, result)
