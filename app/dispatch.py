"""派遣系統 —— 把布告欄委託「派遣」給一位 headless 冒險者,回合制執行。

為什麼是回合制:`claude -p` 一旦啟動,中途無法插話;把任務切成多個回合
(首回合開新 session、之後 `-r <sid>` 續跑),回合之間就有了「櫃台視窗」:
- 讀取使用者的中途指示(steer)與最新目標(goal)注入下一回合 → **中途調整任務**
- 檢查 stop_requested → 隨時可叫停
- 每回合的 STATUS 行寫進 commission_events → 面板即時看進度

完成閉環:冒險者回報 DONE 時,自動把 REPORT 內容 enqueue 進櫃台佇列
(→ 存檔 → llm-wiki-manager 深度合成),委託標記完成。

護欄:一次只跑一個派遣、回合上限、單回合逾時、工具白名單(預設含 Bash,
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

from . import db

log = logging.getLogger("guild.dispatch")

CLAUDE = shutil.which("claude") or "claude"
MODEL = os.environ.get("GUILD_DISPATCH_MODEL", "")  # 空 = 用使用者預設模型
MAX_ROUNDS = int(os.environ.get("GUILD_DISPATCH_ROUNDS", "6"))
ROUND_TIMEOUT = int(os.environ.get("GUILD_DISPATCH_TIMEOUT", "600"))  # 秒/回合
MAX_TURNS = os.environ.get("GUILD_DISPATCH_TURNS", "25")  # claude 內部回合上限
TOOLS = os.environ.get(
    "GUILD_DISPATCH_TOOLS", "Read,Glob,Grep,Edit,Write,Bash,WebSearch,WebFetch")

# 一次只允許一個派遣在跑(本機單人場景;避免多個 headless Claude 搶資源)
_lock = asyncio.Lock()

_STATUS_RE = re.compile(r"^STATUS:\s*(WORKING|DONE|BLOCKED)\s*[—\-–]?\s*(.*)$",
                        re.MULTILINE)
_REPORT_RE = re.compile(r"^REPORT:\s*(.+)$", re.MULTILINE | re.DOTALL)

FIRST_PROMPT = """你是冒險者公會派遣的冒險者,接下這張委託,在目前資料夾內實際動手完成它。

## 委託
- 標題:{title}
- 目標:{goal}
- 備註:{note}

## 工作方式(回合制)
你現在是第 1 回合(共最多 {max_rounds} 回合)。每回合請推進一段實質進度;
回合結束時**必須**在輸出最後回報狀態,格式(擇一):
- `STATUS: WORKING — <這回合完成了什麼,下回合要做什麼>`(還沒做完)
- `STATUS: BLOCKED — <卡在哪裡,需要使用者什麼決定>`(需要人)
- 全部完成時輸出兩行:
  `STATUS: DONE — <一句話成果>`
  `REPORT: <3-5 句結案總結:實際做了什麼、改了哪些檔案、值得記住的心得>`

## 原則
- 小步前進,每回合結束前確認改動可運作(能跑測試就跑)。
- 不確定的重大決定不要猜,回報 BLOCKED 讓使用者用「調整指示」告訴你。
- 使用者可能在回合之間更新目標或給新指示,以最新指示為準。
"""

NEXT_PROMPT = """(公會櫃台)繼續執行這張委託,第 {round} 回合(最多 {max_rounds} 回合)。

## 最新目標
{goal}
{steer_block}
繼續推進,回合結束時同樣以 `STATUS: ...`(必要時加 `REPORT: ...`)收尾。
"""

STEER_BLOCK = """
## ⚠️ 使用者中途指示(優先於原目標)
{steers}
"""


def available() -> bool:
    return shutil.which("claude") is not None


def is_busy() -> bool:
    return _lock.locked()


def _run_round(cmd: list[str], cwd: str | None, prompt: str) -> dict:
    """跑一回合 claude,回傳 {ok, text, session_id, cost, error}。阻塞,於執行緒呼叫。

    prompt 走 stdin 而不是 argv:Windows 的 claude.CMD shim 會把含換行的參數
    整組打壞(換行後的旗標被併進 prompt,--output-format json 失效)。
    """
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True,
                              input=prompt.encode("utf-8"),
                              timeout=ROUND_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"回合逾時({ROUND_TIMEOUT}s)"}
    except OSError as exc:
        return {"ok": False, "error": f"無法啟動 claude:{exc}"}

    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:400]
        return {"ok": False, "error": msg or f"claude 結束碼 {proc.returncode}"}

    raw = proc.stdout.decode("utf-8", "replace").strip()
    text, sid, cost = raw, None, None
    try:
        data = json.loads(raw)
        text = (data.get("result") or data.get("text") or raw).strip()
        sid = data.get("session_id")
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        log.warning("回合輸出非 JSON(前 200 字):%r", raw[:200])
    return {"ok": True, "text": text, "session_id": sid, "cost": cost}


def _build_cmd(resume_sid: str | None) -> list[str]:
    """組 claude 指令(不含 prompt;prompt 由 stdin 餵入)。"""
    cmd = [CLAUDE, "-p"]
    if resume_sid:
        cmd += ["-r", resume_sid]
    cmd += [
        "--output-format", "json",
        "--max-turns", MAX_TURNS,
        "--allowedTools", TOOLS,
        "--permission-mode", "acceptEdits",
    ]
    if MODEL:
        cmd += ["--model", MODEL]
    return cmd


def _parse_status(text: str) -> tuple[str, str]:
    """從回合輸出取 (status, message)。找不到 STATUS 行時視為 WORKING。"""
    m = None
    for m in _STATUS_RE.finditer(text):
        pass  # 取最後一個
    if m is None:
        tail = text.strip().splitlines()[-1][:120] if text.strip() else "(無輸出)"
        return "WORKING", tail
    return m.group(1), m.group(2).strip()[:300]


def _enqueue_report(row, text: str, last_msg: str) -> None:
    """DONE 時把 REPORT 內容送進櫃台佇列(→ wiki 歸檔 + 深度合成)。"""
    m = _REPORT_RE.search(text)
    summary = (m.group(1).strip() if m else last_msg)[:2000]
    db.enqueue(
        adventurer_id=row["session_id"],
        guild=row["guild"],
        title=f"[派遣] {row['title']}",
        summary=summary,
        files_changed="[]",
        lessons=None,
        outcome="success",
    )


async def run_dispatch(cid: int) -> None:
    """派遣主迴圈:背景 task 執行,狀態全寫進 DB / events,前端輪詢即可看到。"""
    if _lock.locked():
        db.add_event(cid, "system", "已有派遣進行中,本次未啟動")
        return
    async with _lock:
        await _loop(cid)


async def _loop(cid: int) -> None:
    row = db.get_commission(cid)
    if row is None:
        return
    cwd = row["cwd"] or None
    db.update_commission(cid, status="working", stop_requested=0)
    db.add_event(cid, "system", f"🚀 派遣啟動(資料夾:{cwd or '預設'})")

    sid = row["session_id"]  # 之前派過可續冒險
    total_cost = 0.0
    for rnd in range(1, MAX_ROUNDS + 1):
        row = db.get_commission(cid)
        if row is None or row["stop_requested"]:
            db.update_commission(cid, status="open", stop_requested=0)
            db.add_event(cid, "system", "⏹ 已依要求停止派遣(委託回到招募中)")
            return

        goal = row["goal"] or row["title"]
        steers = db.take_steers(cid)
        if sid is None:
            prompt = FIRST_PROMPT.format(
                title=row["title"], goal=goal,
                note=row["note"] or "(無)", max_rounds=MAX_ROUNDS)
            if steers:
                prompt += STEER_BLOCK.format(steers="\n".join(f"- {s}" for s in steers))
        else:
            steer_block = (STEER_BLOCK.format(
                steers="\n".join(f"- {s}" for s in steers)) if steers else "")
            prompt = NEXT_PROMPT.format(round=rnd, max_rounds=MAX_ROUNDS,
                                        goal=goal, steer_block=steer_block)

        log.info("委託 #%s 回合 %s/%s 開始 (sid=%s)", cid, rnd, MAX_ROUNDS,
                 (sid or "new")[:8])
        res = await asyncio.to_thread(_run_round, _build_cmd(sid), cwd, prompt)

        if not res["ok"]:
            db.update_commission(cid, status="failed", rounds=rnd)
            db.add_event(cid, "system", f"❌ 回合 {rnd} 失敗:{res['error']}", rnd)
            return

        log.info("委託 #%s 回合 %s 回傳: sid=%s cost=%s", cid, rnd,
                 res.get("session_id"), res.get("cost"))
        if res.get("session_id"):
            sid = res["session_id"]
        if isinstance(res.get("cost"), (int, float)):
            total_cost += res["cost"]
        db.update_commission(cid, session_id=sid, rounds=rnd)

        status, msg = _parse_status(res["text"])
        cost_s = f" (${total_cost:.3f} 累計)" if total_cost else ""
        db.add_event(cid, "progress", f"{msg}{cost_s}", rnd)

        if status == "DONE":
            row = db.get_commission(cid)
            _enqueue_report(row, res["text"], msg)
            db.update_commission(cid, status="done",
                                 done_at=db._now())  # noqa: SLF001 同模組層級使用
            db.add_event(cid, "system",
                         f"🏁 委託完成(共 {rnd} 回合),結案回報已送櫃台歸檔", rnd)
            return
        if status == "BLOCKED":
            db.update_commission(cid, status="blocked")
            db.add_event(cid, "system",
                         f"🧱 冒險者受阻,等待你的「調整指示」後可再派遣", rnd)
            return

    db.update_commission(cid, status="blocked")
    db.add_event(cid, "system",
                 f"⏸ 已達回合上限({MAX_ROUNDS}),暫停;可下指示後再派遣繼續")
