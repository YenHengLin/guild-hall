"""召回冒險者 —— stream-json 即時串流版。

與 recall.py(一次拿最終結果)的差別:這裡用 `--output-format stream-json --verbose`
讓 claude 逐步吐出「喚醒 → 讀檔 → 執行 → 完成」的事件,後端即時解析成白話步驟,
透過 SSE 推給前端,讓使用者**看著被召回的冒險者一步步幹活**。

只對「面板自己 spawn 的這隻 claude」有效(外部 session 的即時觀測靠 hook)。

Windows 限制:uvicorn 用 SelectorEventLoop,不支援 asyncio 子行程 → 用 subprocess.Popen
在背景執行緒逐行讀 stdout,經 asyncio.Queue 橋接回事件圈。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

log = logging.getLogger("guild.stream_recall")

CLAUDE = shutil.which("claude") or "claude"
CHEAP_MODEL = "claude-haiku-4-5-20251001"

REPORT_PROMPT = (
    "【結案回報】你正被公會櫃台召回。請只『回顧』這個 session 從開始到現在"
    "已經『實際發生』的事,用繁體中文寫一份結案報告。"
    "嚴禁規劃未來、嚴禁反問、嚴禁列出『接下來可以做什麼』、"
    "嚴禁描述自己正處於計畫模式或任何系統狀態。"
    "格式:1) 一句話成果標題 2) 實際做了哪些事(條列) "
    "3) 主要改動或建立的檔案 4) 值得記住的心得。"
    "若這個 session 幾乎沒做實質工作,就如實簡短說明。"
    "直接輸出報告文字,不要問問題、不要動任何檔案。"
)


def available() -> bool:
    return shutil.which("claude") is not None


def _basename(p) -> str:
    try:
        return Path(str(p)).name or str(p)
    except Exception:
        return str(p)


def _summarize_tool(tool: str, ti: dict) -> str:
    """把工具 + 參數濃縮成一句白話(對齊 hooks/activity_logger.summarize)。"""
    ti = ti or {}
    try:
        if tool in ("Edit", "Write", "NotebookEdit", "Read"):
            return _basename(ti.get("file_path") or ti.get("notebook_path") or "")
        if tool in ("Bash", "PowerShell"):
            return (ti.get("description") or ti.get("command") or "")[:80]
        if tool in ("Grep", "Glob"):
            return ti.get("pattern") or ti.get("query") or ""
        if tool == "Skill":
            return ti.get("skill") or ti.get("command") or ""
        if tool in ("Task", "Agent"):
            return ti.get("description") or ti.get("subagent_type") or ""
        if tool == "WebSearch":
            return (ti.get("query") or "")[:80]
        if tool == "WebFetch":
            return ti.get("url") or ""
    except Exception:
        pass
    return ""


def _events_from_line(d: dict) -> Iterator[dict]:
    """把一行 stream-json 事件轉成 0..N 個白話步驟給前端。"""
    t = d.get("type")
    if t == "system" and d.get("subtype") == "init":
        yield {"kind": "start", "text": "喚醒冒險者,載入 session 脈絡…"}
        return
    if t == "assistant":
        for blk in (d.get("message") or {}).get("content") or []:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_use":
                name = blk.get("name", "?")
                yield {"kind": "tool", "tool": name,
                       "text": _summarize_tool(name, blk.get("input"))}
            elif blk.get("type") == "text":
                txt = (blk.get("text") or "").strip()
                if txt:
                    yield {"kind": "say", "text": txt}
        return
    if t == "result":
        yield {"kind": "done",
               "text": (d.get("result") or d.get("text") or "").strip(),
               "cost": d.get("total_cost_usd")}
        return


def stream_events(sid: str, prompt: str, cwd: str | None,
                  read_only: bool = True, cheap: bool = False) -> Iterator[dict]:
    """阻塞式產生器:逐行讀 claude stream-json,yield 白話步驟事件。

    在背景執行緒中迭代(見 router),絕不在事件圈直接跑。
    """
    # prompt 走 stdin:Windows claude.CMD 會把含換行的 argv 參數打壞(旗標失效)
    cmd = [CLAUDE, "-p", "-r", sid,
           "--output-format", "stream-json", "--verbose"]
    if read_only:
        cmd += ["--permission-mode", "plan"]
    if cheap:
        cmd += ["--model", CHEAP_MODEL]

    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        proc.stdin.write(prompt)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except OSError as exc:
        yield {"kind": "error", "text": f"無法啟動 claude:{exc}"}
        return

    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield from _events_from_line(d)
    finally:
        proc.wait()
        if proc.returncode not in (0, None):
            err = ""
            if proc.stderr:
                err = (proc.stderr.read() or "").strip()[:400]
            if err:
                yield {"kind": "error", "text": err}
