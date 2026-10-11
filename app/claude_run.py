"""共用的 `claude -p --output-format stream-json` 執行器。

夜巡(muse)與派遣(dispatch)原本各有一份幾乎逐行相同的程式碼:
`subprocess.Popen` + 走 stdin 餵 prompt、逐行讀 stream-json、解析 `assistant`/
`result`、累計 `total_cost_usd`,外加**一模一樣的 Windows `taskkill /F /T` 樹殺
看門狗**(連註解都互相引用「和 muse 同樣的修法」)。這裡把共通骨架抽成單一真相源:
殭屍程序的殺法、stream 解析只需維護一處,未來改一邊不會漏改另一邊。

兩邊的差異只剩「每個事件要做什麼」——夜巡更新面板進度、派遣寫時間線步驟 + 收集
改動檔案——交給呼叫端傳入的 `on_event` callback 處理。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Callable

# _kill_tree 下沉到更底層的 stream_recall 當單一真相源(與 drain_stderr 同),
# 兩邊看門狗共用同一套 Windows 樹殺邏輯,改一處不會漏改另一處。
from .stream_recall import drain_stderr, _kill_tree


def run_stream(cmd: list[str], cwd: str | None, prompt: str,
               on_event: Callable[[dict], None] | None = None,
               timeout: int = 600, hard_timeout: int | None = None) -> dict:
    """跑一次 claude stream-json,逐行解析並回傳彙總結果。阻塞,於執行緒呼叫。

    prompt 走 stdin:Windows 的 claude.CMD shim 會把含換行的 argv 參數打壞。
    自管逾時看門狗:到點用 taskkill /T 砍整棵樹,避免只殺外層 shim 留下孤兒程序。

    看門狗是**閒置逾時**而非固定硬逾時:只要還在吐 stream 事件(工具動作、訊息、
    進度)就代表「真的在跑」,計時器隨之重置、任務持續延長;唯有連續 `timeout` 秒
    毫無任何輸出才判定 hang 砍掉。這解決了「任務明明在推進,卻被牆鐘硬上限誤砍」
    (真實案例:派遣上傳大檔或多步任務跑過 600s 被攔腰砍)。`hard_timeout` 是總時長
    絕對上限(防失控燒錢/無限迴圈),None=不設。

    `on_event(d)` 會對每個成功解析的 stream-json 事件(原始 dict)呼叫一次,讓呼叫端
    自行做副作用(更新進度、寫時間線、收集改動檔案);本函式只負責抽出 text/cost/
    session_id 等通用欄位。

    回傳 dict:
    - started: 子程序是否起得來(False 時另有 error,其餘欄位無意義)
    - ok: started 且拿到 result 且未被看門狗砍
    - killed: 是否因逾時被看門狗砍掉
    - kill_reason: "idle"(閒置無輸出)/"hard"(超過總時長上限)/""(未被砍)
    - got_result: 是否收到 result 事件
    - text / cost / session_id: 從 result(session_id 另含 system init)抽出
    - returncode: 子程序結束碼
    - stderr: 無參數 callable,呼叫取得已排空的 stderr 字串
    """
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        proc.stdin.write(prompt)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except OSError as exc:
        return {"started": False, "ok": False, "error": f"無法啟動 claude:{exc}"}

    # 邊讀邊排空 stderr,否則 pipe 塞滿會讓子程序卡死、連帶不再吐 stdout(見 helper 說明)
    get_stderr = drain_stderr(proc)

    killed = {"v": False, "reason": ""}
    start = time.monotonic()
    last_activity = {"t": start}  # 最近一次收到 stream 輸出的時刻(閒置逾時基準)
    stop_watch = threading.Event()

    def _watchdog() -> None:
        # 每 5s 醒來檢查:閒置超過 timeout → idle 砍;總時長超過 hard_timeout → hard 砍
        while not stop_watch.wait(5):
            now = time.monotonic()
            if now - last_activity["t"] > timeout:
                killed["v"], killed["reason"] = True, "idle"
                _kill_tree(proc)
                return
            if hard_timeout and now - start > hard_timeout:
                killed["v"], killed["reason"] = True, "hard"
                _kill_tree(proc)
                return

    watch = threading.Thread(target=_watchdog, daemon=True)
    watch.start()

    text, cost, sid = "", None, None
    got_result = False
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
            t = d.get("type")
            if t == "system" and d.get("subtype") == "init":
                sid = d.get("session_id") or sid
            elif t == "result":
                got_result = True
                text = (d.get("result") or d.get("text") or text).strip()
                cost = d.get("total_cost_usd")
                sid = d.get("session_id") or sid
            if on_event is not None:
                on_event(d)
    finally:
        stop_watch.set()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    return {
        "started": True,
        "ok": got_result and not killed["v"],
        "killed": killed["v"],
        "kill_reason": killed["reason"],
        "got_result": got_result,
        "text": text,
        "cost": cost,
        "session_id": sid,
        "returncode": proc.returncode,
        "stderr": get_stderr,
    }
