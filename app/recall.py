"""召回冒險者 —— 用 session id 復活原本的 Claude,請它回報 / 答詢 / 接續。

這是面板從「觀測層」跨到「執行層」的一步:會實際啟動 claude CLI、花 token。
護欄:
- 一次只跑一個召回(避免同時 spawn 多個 Claude)。
- 唯讀用途(回報/答詢)走 `--permission-mode plan`,不讓它改檔。
- 逾時保護。
- 摘要類用較便宜的模型。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess

log = logging.getLogger("guild.recall")

CLAUDE = shutil.which("claude") or "claude"
TIMEOUT = 180  # 秒
CHEAP_MODEL = "claude-haiku-4-5-20251001"  # 摘要用,省 token

# 一次只允許一個召回在跑
_lock = asyncio.Lock()

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


def _run_blocking(cmd: list[str], cwd: str | None,
                  prompt: str) -> subprocess.CompletedProcess:
    # 在執行緒中跑,避開 Windows uvicorn(SelectorEventLoop)不支援 async subprocess 的限制
    # prompt 走 stdin:Windows claude.CMD 會把含換行的 argv 參數打壞(旗標失效)
    return subprocess.run(cmd, cwd=cwd, capture_output=True,
                          input=prompt.encode("utf-8"), timeout=TIMEOUT)


async def _run(sid: str, prompt: str, cwd: str | None,
               read_only: bool = True, cheap: bool = False) -> dict:
    """執行一次 claude --resume,回傳 {ok, result/error, cost}。"""
    cmd = [CLAUDE, "-p", "-r", sid, "--output-format", "json"]
    if read_only:
        cmd += ["--permission-mode", "plan"]
    if cheap:
        cmd += ["--model", CHEAP_MODEL]

    try:
        proc = await asyncio.to_thread(_run_blocking, cmd, cwd, prompt)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"召回逾時({TIMEOUT}s)"}
    except OSError as exc:
        return {"ok": False, "error": f"無法啟動 claude:{exc}"}

    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:600]
        return {"ok": False, "error": msg or f"claude 結束碼 {proc.returncode}"}

    raw = proc.stdout.decode("utf-8", "replace").strip()
    try:
        data = json.loads(raw)
        result = data.get("result") or data.get("text") or raw
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        result, cost = raw, None
    return {"ok": True, "result": result, "cost": cost}


async def recall(sid: str, prompt: str, cwd: str | None,
                 read_only: bool = True, cheap: bool = False) -> dict:
    """對外入口:序列化執行(一次一個)。"""
    if not available():
        return {"ok": False, "error": "找不到 claude CLI(請確認已安裝且在 PATH)"}
    if _lock.locked():
        return {"ok": False, "error": "已有一個召回進行中,請稍候再試"}
    async with _lock:
        log.info("召回 session %s (read_only=%s cheap=%s)", sid[:8], read_only, cheap)
        return await _run(sid, prompt, cwd, read_only, cheap)
