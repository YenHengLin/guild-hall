"""公會夜巡 API —— 靈感卡清單 / 設定 / 手動觸發 / 採納成委託。

- GET  /muse/ideas          靈感卡清單(new 在前)
- GET  /muse/config         夜巡設定 + 今日花費/剩餘預算
- PUT  /muse/config         更新設定
- POST /muse/run            立即手動巡一輪(略過時段/開關,仍守預算與唯讀)
- POST /muse/ideas/{id}/promote  採納:把靈感卡變成布告欄委託(open)
- POST /muse/ideas/{id}/dismiss  略過
- DELETE /muse/ideas/{id}   刪除
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import db, muse
from ..models import Commission, MuseConfig, MuseIdea

router = APIRouter(prefix="/api", tags=["muse"])


def _idea(r) -> MuseIdea:
    # body 欄位可能是打包了白話三欄(headline/next_step/impact)的信封,拆封還原
    parts = muse.unpack_body(r["body"])
    return MuseIdea(
        id=r["id"], guild=r["guild"], cwd=r["cwd"], kind=r["kind"] or "idea",
        title=r["title"], body=parts["body"], effort=r["effort"],
        headline=parts["headline"], next_step=parts["next_step"],
        impact=parts["impact"],
        status=r["status"] or "new", created_at=r["created_at"],
    )


@router.get("/muse/ideas", response_model=list[MuseIdea])
def list_ideas() -> list[MuseIdea]:
    return [_idea(r) for r in db.list_ideas()]


@router.get("/muse/config")
def get_config() -> dict:
    cfg = muse.load_config()
    return {
        "config": cfg.model_dump(),
        "available": muse.available(),
        "spent_today": round(muse.spent_today(), 4),
        "budget_left": muse.budget_left(cfg),
    }


@router.put("/muse/config")
async def put_config(cfg: MuseConfig) -> dict:
    # async:啟用時要 asyncio.create_task 起背景 loop,需在事件迴圈執行緒內
    muse.save_config(cfg)
    if cfg.enabled:
        muse.ensure_loop()
    return get_config()


@router.get("/muse/status")
def muse_status() -> dict:
    """夜巡即時進度(供面板輪詢:現在在巡哪座公會、最近一步在做什麼)。"""
    return muse.get_progress()


@router.post("/muse/run")
async def run_now() -> dict:
    if not muse.available():
        raise HTTPException(status_code=503, detail="找不到 claude CLI")
    res = await muse.run_now()
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res.get("error", "夜巡失敗"))
    return res


@router.post("/muse/auto-run")
async def auto_run() -> dict:
    """不巡邏,直接從既有靈感清單挑可自動執行的做掉(排入派遣佇列)。"""
    return await muse.auto_do_now()


@router.post("/muse/ideas/{iid}/promote", response_model=Commission)
def promote_idea(iid: int) -> Commission:
    """採納靈感卡 → 建一張布告欄委託(open),帶上工作資料夾與目標。"""
    r = db.get_idea(iid)
    if r is None:
        raise HTTPException(status_code=404, detail="找不到這張靈感卡")
    goal = muse.goal_from_body(r["title"], r["body"])
    cid = db.add_commission(r["guild"], r["title"], note="🌙 來自公會夜巡的靈感")
    db.update_commission(cid, goal=goal, cwd=r["cwd"])
    db.set_idea_status(iid, "promoted")
    from .commissions import _get  # 復用委託組裝
    return _get(cid)


@router.post("/muse/ideas/{iid}/dismiss", response_model=MuseIdea)
def dismiss_idea(iid: int) -> MuseIdea:
    if db.get_idea(iid) is None:
        raise HTTPException(status_code=404, detail="找不到這張靈感卡")
    db.set_idea_status(iid, "dismissed")
    return _idea(db.get_idea(iid))


@router.delete("/muse/ideas/{iid}")
def delete_idea(iid: int) -> dict:
    db.delete_idea(iid)
    return {"status": "deleted", "id": iid}
