"""委託布告欄 API —— 貼 / 完成 / 刪除,以及派遣系統(headless 冒險者執行)。

派遣生命週期:
open(招募中)→ POST /dispatch → working(回合制執行)
  → 回合間可 POST /steer(中途指示)、PATCH(改目標)、POST /stop
  → done(自動回報歸檔)/ blocked(等指示,可再派遣續跑)/ failed
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException

from .. import db, dispatch
from ..models import (
    Commission, CommissionEvent, CommissionPatch, CommissionPost,
    DispatchReq, SteerPost,
)

router = APIRouter(prefix="/api", tags=["commissions"])

# 進行中的派遣背景 task(防止被 GC,也供健檢)
_tasks: set[asyncio.Task] = set()


def _images(r, keys) -> list[str]:
    if "images" not in keys or not r["images"]:
        return []
    try:
        val = json.loads(r["images"])
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _row(r) -> Commission:
    keys = r.keys()
    return Commission(
        id=r["id"], guild=r["guild"], title=r["title"], note=r["note"],
        status=r["status"], created_at=r["created_at"], done_at=r["done_at"],
        goal=r["goal"] if "goal" in keys else None,
        cwd=r["cwd"] if "cwd" in keys else None,
        session_id=r["session_id"] if "session_id" in keys else None,
        rounds=r["rounds"] or 0 if "rounds" in keys else 0,
        model=r["model"] if "model" in keys else None,
        klass=r["klass"] if "klass" in keys else None,
        cost=r["cost"] or 0.0 if "cost" in keys else 0.0,
        images=_images(r, keys),
    )


def _get(cid: int) -> Commission:
    r = db.get_commission(cid)
    if r is None:
        raise HTTPException(status_code=404, detail="找不到這張委託")
    return _row(r)


@router.get("/commissions", response_model=list[Commission])
def list_commissions() -> list[Commission]:
    return [_row(r) for r in db.list_commissions(include_done=True)]


@router.get("/costs")
def cost_summary() -> dict:
    """成本帳本:月報表 + 各來源小計 + 總計。"""
    return db.cost_summary()


@router.post("/commissions", response_model=Commission)
def create_commission(body: CommissionPost) -> Commission:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="委託標題不可空白")
    imgs = [u for u in (body.images or []) if isinstance(u, str) and u.startswith("/uploads/")]
    cid = db.add_commission(body.guild, title, body.note,
                            json.dumps(imgs, ensure_ascii=False) if imgs else None)
    extra = {k: v for k, v in
             {"goal": body.goal, "cwd": body.cwd, "model": body.model,
              "klass": body.klass}.items() if v}
    if extra:
        db.update_commission(cid, **extra)
    return _get(cid)


@router.patch("/commissions/{cid}", response_model=Commission)
def patch_commission(cid: int, body: CommissionPatch) -> Commission:
    """中途調整:改目標 / 標題 / 備註。派遣執行中改 goal,下一回合即生效。"""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        db.update_commission(cid, **fields)
        if "goal" in fields:
            db.add_event(cid, "system", f"🎯 目標已調整:{fields['goal']}")
    return _get(cid)


@router.post("/commissions/{cid}/dispatch", response_model=Commission)
async def dispatch_commission(cid: int, body: DispatchReq) -> Commission:
    """派遣 headless 冒險者執行這張委託(背景回合制)。"""
    c = _get(cid)
    if not dispatch.available():
        raise HTTPException(status_code=503, detail="找不到 claude CLI")
    if c.status == "working":
        raise HTTPException(status_code=409, detail="這張委託已在執行中")
    if c.status == "done":
        raise HTTPException(status_code=409, detail="委託已完成;要重做請先取消完成")
    if dispatch.is_busy():
        raise HTTPException(status_code=409, detail="已有其他派遣進行中,請稍候")
    if body.cwd:
        db.update_commission(cid, cwd=body.cwd)
    elif not c.cwd:
        raise HTTPException(status_code=400, detail="請先指定工作資料夾(公會)")
    if body.model is not None:
        db.update_commission(cid, model=body.model or None)
    task = asyncio.create_task(dispatch.run_dispatch(cid))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _get(cid)


@router.post("/commissions/{cid}/enqueue", response_model=Commission)
async def enqueue_commission(cid: int, body: DispatchReq) -> Commission:
    """把委託加入派遣佇列;背景 worker 會依序自動執行(可一次排多張)。"""
    c = _get(cid)
    if not dispatch.available():
        raise HTTPException(status_code=503, detail="找不到 claude CLI")
    if c.status in ("working", "queued"):
        raise HTTPException(status_code=409, detail="這張委託已在佇列或執行中")
    if c.status == "done":
        raise HTTPException(status_code=409, detail="委託已完成;要重做請先取消完成")
    if body.cwd:
        db.update_commission(cid, cwd=body.cwd)
    elif not c.cwd:
        raise HTTPException(status_code=400, detail="請先指定工作資料夾(公會)")
    if body.model is not None:
        db.update_commission(cid, model=body.model or None)
    db.update_commission(cid, status="queued")
    ahead = max(0, len(db.queued_ids()) - 1)
    db.add_event(cid, "system",
                 f"🎫 已加入派遣佇列(前方還有 {ahead} 張)" if ahead
                 else "🎫 已加入派遣佇列,即將開始")
    dispatch.ensure_worker()
    return _get(cid)


@router.post("/commissions/{cid}/steer", response_model=list[CommissionEvent])
def steer_commission(cid: int, body: SteerPost) -> list[CommissionEvent]:
    """對執行中(或受阻)的委託下中途指示,下一回合注入。"""
    c = _get(cid)
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="指示內容不可空白")
    db.add_event(cid, "steer", content)
    # 學習迴路 B:使用者叫冒險者「這樣做」= 做事方式偏好,存起來注入之後的派遣、
    # 並作為訓練所提煉 skill 的素材。
    db.add_work_pref(content, c.guild, c.session_id)
    return list_commission_events(cid)


@router.post("/commissions/{cid}/stop", response_model=Commission)
def stop_commission(cid: int) -> Commission:
    """停止派遣。排隊中→直接移出佇列;執行中→回合結束時生效,委託回到招募中。"""
    c = _get(cid)
    if c.status == "queued":
        db.update_commission(cid, status="open")
        db.add_event(cid, "system", "🎫 已從派遣佇列移除(回到招募中)")
        return _get(cid)
    db.update_commission(cid, stop_requested=1)
    db.add_event(cid, "system", "⏹ 已送出停止要求(本回合結束後生效)")
    return _get(cid)


@router.get("/commissions/{cid}/events", response_model=list[CommissionEvent])
def list_commission_events(cid: int) -> list[CommissionEvent]:
    return [
        CommissionEvent(id=e["id"], type=e["type"], content=e["content"],
                        round=e["round"], created_at=e["created_at"])
        for e in db.list_events(cid)
    ]


@router.post("/commissions/{cid}/done", response_model=Commission)
def complete_commission(cid: int) -> Commission:
    db.set_commission_status(cid, "done")
    return _get(cid)


@router.post("/commissions/{cid}/reopen", response_model=Commission)
def reopen_commission(cid: int) -> Commission:
    db.set_commission_status(cid, "open")
    return _get(cid)


@router.delete("/commissions/{cid}")
def remove_commission(cid: int) -> dict:
    db.delete_commission(cid)
    return {"status": "deleted", "id": cid}
