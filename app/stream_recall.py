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
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("guild.stream_recall")


def _kill_tree(proc: subprocess.Popen) -> None:
    """整棵砍掉程序樹。Windows 的 claude.CMD 會包一層,只 kill 外層會留孤兒 node,
    必須用 taskkill /T 連子孫一起殺(見這功能修復前留下的殭屍事故)。

    放在這個最底層模組當單一真相源:claude_run 與 stream_recall 兩邊的看門狗共用。
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def drain_stderr(proc: subprocess.Popen, max_lines: int = 50) -> Callable[[], str]:
    """邊讀邊排空子程序 stderr,避免 PIPE 塞滿造成死鎖。

    當只在主迴圈逐行讀 stdout、stderr 卻要到最後才 read() 時:claude 若往 stderr
    大量輸出(node deprecation、警告),OS pipe buffer(約 4–64KB)一滿,子程序
    寫 stderr 就阻塞、連帶停止吐 stdout,主迴圈永遠讀不到下一行 → 回合莫名卡死到
    看門狗逾時。這裡開一條 daemon thread 持續把 stderr 吸進 bounded deque(只留
    最後 max_lines 行),既排空 pipe 又保留錯誤尾巴供失敗回報。

    回傳一個 callable:呼叫它會 join drain thread 並回傳最後 max_lines 行的字串。
    """
    stream = proc.stderr
    if stream is None:
        return lambda: ""
    buf: deque[str] = deque(maxlen=max_lines)

    def _pump() -> None:
        try:
            for line in stream:
                buf.append(line)
        except Exception:  # noqa: BLE001
            pass

    th = threading.Thread(target=_pump, daemon=True)
    th.start()

    def _result() -> str:
        th.join(timeout=5)
        return "".join(buf).strip()

    return _result

CLAUDE = shutil.which("claude") or "claude"
CHEAP_MODEL = "claude-haiku-4-5-20251001"

# 即時串流的閒置逾時(秒):連續這麼久毫無任何 stream 輸出就判定被召回的 claude
# hang 住,taskkill 整棵程序樹。與 recall.TIMEOUT 對齊(非串流召回也是 180s)。
IDLE_TIMEOUT = int(os.environ.get("GUILD_STREAM_RECALL_TIMEOUT", "180"))

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
                  read_only: bool = True, cheap: bool = False,
                  timeout: int = IDLE_TIMEOUT) -> Iterator[dict]:
    """阻塞式產生器:逐行讀 claude stream-json,yield 白話步驟事件。

    在背景執行緒中迭代(見 router),絕不在事件圈直接跑。

    閒置逾時看門狗:只要還在吐 stream 事件就代表真的在跑,計時器隨之重置;唯有連續
    `timeout` 秒毫無任何輸出才判定被召回的 claude hang 住,taskkill 整棵程序樹。
    沒有這道看門狗時,claude 一 hang 這個產生器與 `proc.wait()` 都會無限阻塞,
    router 抱著的 `_stream_busy` 鎖永遠放不掉,即時觀看功能被整個鎖死到重啟 server
    (骨架重用自 claude_run.run_stream)。
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

    # 邊讀邊排空 stderr,否則 pipe 塞滿會讓子程序卡死、連帶不再吐 stdout
    get_stderr = drain_stderr(proc)

    killed = {"v": False}
    last_activity = {"t": time.monotonic()}  # 最近一次收到 stream 輸出的時刻
    stop_watch = threading.Event()

    def _watchdog() -> None:
        # 每 5s 醒來:閒置超過 timeout 毫無輸出 → 判定 hang,taskkill 整棵樹
        while not stop_watch.wait(5):
            if time.monotonic() - last_activity["t"] > timeout:
                killed["v"] = True
                _kill_tree(proc)
                return

    watch = threading.Thread(target=_watchdog, daemon=True)
    watch.start()

    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            last_activity["t"] = time.monotonic()  # 有輸出=還活著,重置閒置計時器
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield from _events_from_line(d)
    finally:
        stop_watch.set()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            _kill_tree(proc)
        if killed["v"]:
            yield {"kind": "error",
                   "text": f"被召回的冒險者閒置逾時({timeout}s 毫無輸出,判定卡死),"
                           f"已終止程序並釋放串流鎖"}
        elif proc.returncode not in (0, None):
            err = get_stderr()[:400]
            if err:
                yield {"kind": "error", "text": err}
