"""召回冒險者 API —— 請原本的 Claude(依 session id)回報 / 答詢 / 接續。

會實際啟動 claude CLI。前端應在按下前提醒使用者這會花 token。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import db, recall, scanner, stream_recall
from ..models import RecallAsk, RecallReq

log = logging.getLogger("guild.recall.api")

router = APIRouter(prefix="/api/recall", tags=["recall"])

# 一次只允許一個即時串流召回(避免同時 spawn 多個 claude)
_stream_busy = threading.Lock()


def _resolve_or_404(sid: str) -> tuple[str, str | None]:
    """找 session 的真實工作目錄;找不到就明確報錯(而非開個必定失敗的終端)。

    `claude --resume <id>` 只在「該 id 的 jsonl 存在於某個 project 目錄」時才成功。
    若 id 打錯一個字、或那是別處的 headless session 沒被掃到,session_location
    會回 (None, None) —— 此時直接回 404,前端就不會看到 claude CLI 那句
    看不懂的「No conversation found with session ID」。
    """
    cwd, guild = scanner.session_location(sid)
    if cwd is None:
        raise HTTPException(
            status_code=404,
            detail=(f"找不到 session {sid[:12]}… 的紀錄(這個 id 可能打錯了,"
                    f"或該對話不在已掃描的公會目錄裡)。請確認 session id 正確。"),
        )
    return cwd, guild


def _title_from(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#*-0123456789. )").strip()
        if s:
            return s[:48]
    return "召回回報"


@router.post("/ask")
async def recall_ask(body: RecallAsk) -> dict:
    """復活該 session,帶著完整脈絡回答使用者的追問(不歸檔)。"""
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="問題不可空白")
    cwd, _ = _resolve_or_404(body.adventurer_id)
    res = await recall.recall(body.adventurer_id, body.question, cwd,
                              read_only=True, cheap=False)
    if not res["ok"]:
        raise HTTPException(status_code=502, detail=res["error"])
    if res.get("cost"):
        db.add_cost("recall", float(res["cost"]), adventurer_id=body.adventurer_id,
                    detail="召回答詢")
    return {"ok": True, "result": res["result"], "cost": res.get("cost")}


@router.get("/stream")
async def recall_stream(
    adventurer_id: str = Query(...),
    mode: str = Query("report"),  # report(唯讀敘述) / fix(可改檔)
    prompt: str | None = Query(None),  # fix 模式的指示
) -> StreamingResponse:
    """SSE 即時串流:看被召回的冒險者一步步幹活。

    - mode=report:唯讀,請它即時把 session 進度講一遍(便宜模型)。
    - mode=fix:給它一項指示接續/改善,**會實際改檔**(前端須先確認)。
    """
    if not stream_recall.available():
        raise HTTPException(status_code=502, detail="找不到 claude CLI")
    if _stream_busy.locked():
        raise HTTPException(status_code=409, detail="已有一個即時串流進行中,請稍候")

    cwd, guild = _resolve_or_404(adventurer_id)
    if mode == "fix":
        instr = (prompt or "").strip()
        if not instr:
            raise HTTPException(status_code=400, detail="fix 模式需要指示內容")
        run_prompt = (
            f"你被公會召回接續這個 session 的工作。請依以下指示動手完成/改善,"
            f"用繁體中文邊做邊簡短說明,完成後總結成果:\n{instr}"
        )
        read_only, cheap = False, False
    else:
        run_prompt = stream_recall.REPORT_PROMPT
        read_only, cheap = True, True

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    _SENTINEL = {"kind": "end"}

    def produce() -> None:
        final_text, final_cost = "", None
        with _stream_busy:
            try:
                for ev in stream_recall.stream_events(
                    adventurer_id, run_prompt, cwd, read_only=read_only, cheap=cheap
                ):
                    if ev.get("kind") == "done":
                        final_text = ev.get("text") or final_text
                        final_cost = ev.get("cost")
                    loop.call_soon_threadsafe(q.put_nowait, ev)
                # report 模式:串流結束後自動歸檔進櫃台(= 舊「請他回報」的沉澱能力,
                # 現在併進串流:你看它一步步做,做完成果照樣寫進 wiki 知識庫)。
                if mode == "report" and final_text.strip():
                    if final_cost:
                        db.add_cost("recall", float(final_cost),
                                    adventurer_id=adventurer_id, guild=guild,
                                    model=stream_recall.CHEAP_MODEL, detail="召回回報")
                    rid = db.enqueue(
                        adventurer_id=adventurer_id, guild=guild,
                        title=_title_from(final_text), summary=final_text,
                        files_changed=json.dumps([], ensure_ascii=False),
                        lessons=None, outcome="success",
                    )
                    loop.call_soon_threadsafe(
                        q.put_nowait, {"kind": "filed", "report_id": rid})
            except Exception as exc:  # noqa: BLE001 — 串流要善終
                loop.call_soon_threadsafe(
                    q.put_nowait, {"kind": "error", "text": str(exc)})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

    threading.Thread(target=produce, daemon=True).start()

    async def gen():
        # 先送一個 meta 事件(公會名),讓前端標題可用
        yield _sse({"kind": "meta", "guild": guild, "mode": mode})
        while True:
            ev = await q.get()
            if ev is _SENTINEL:
                yield _sse({"kind": "end"})
                break
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/continue")
def recall_continue(body: RecallReq) -> dict:
    """開一個互動終端,resume 該 session,讓使用者親自接續做事。"""
    if not recall.available():
        raise HTTPException(status_code=502, detail="找不到 claude CLI")
    cwd, guild = _resolve_or_404(body.adventurer_id)
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail="接續目前僅支援 Windows")
    try:
        # 開新的 cmd 視窗,在該公會目錄 resume 這個 session
        subprocess.Popen(
            f'start "召回 {guild or ""}" cmd /k claude --resume {body.adventurer_id}',
            shell=True, cwd=cwd or None,
        )
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"無法開啟終端:{exc}")
    return {"ok": True, "launched": True, "cwd": cwd}
