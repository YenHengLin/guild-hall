"""訓練所 API —— 自動化進化:headless 分析產提案 → 面板批准才落地。

- POST /training/propose   送一位冒險者進訓練所(分析其解決過的任務)
- POST /training/distill   全公會提煉(跨任務通則,llm-wiki-manager Prune 精神)
- GET  /proposals          提案清單(pending 在前)
- POST /proposals/{id}/approve|reject  裁決;approve 寫 artifact + 登記進化
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import db, training
from ..models import ProposalDecision, ProposalRecord, TrainReq

router = APIRouter(prefix="/api", tags=["training"])


def _row(r) -> ProposalRecord:
    return ProposalRecord(
        id=r["id"], adventurer_id=r["adventurer_id"], guild=r["guild"],
        route=r["route"], name=r["name"], detail=r["detail"],
        artifact_path=r["artifact_path"], artifact_content=r["artifact_content"],
        status=r["status"], decided_note=r["decided_note"],
        created_at=r["created_at"], decided_at=r["decided_at"],
    )


@router.post("/training/propose", response_model=ProposalRecord)
async def propose(body: TrainReq) -> ProposalRecord:
    """訓練一位冒險者:分析素材,產一筆待批准的進化提案(會跑 claude,較久)。"""
    if not training.available():
        raise HTTPException(status_code=503, detail="找不到 claude CLI")
    res = await asyncio.to_thread(training.propose_for, body.adventurer_id)
    if not res["ok"]:
        raise HTTPException(status_code=422, detail=res["error"])
    return _row(db.get_proposal(res["proposal_id"]))


@router.post("/training/distill", response_model=ProposalRecord)
async def distill() -> ProposalRecord:
    """全公會提煉:掃近期完成回報,提取跨任務通則成一筆提案。"""
    if not training.available():
        raise HTTPException(status_code=503, detail="找不到 claude CLI")
    res = await asyncio.to_thread(training.distill)
    if not res["ok"]:
        raise HTTPException(status_code=422, detail=res["error"])
    return _row(db.get_proposal(res["proposal_id"]))


@router.get("/proposals", response_model=list[ProposalRecord])
def list_proposals() -> list[ProposalRecord]:
    rows = db.list_proposals()
    # pending 排前面,其餘按新→舊
    return sorted((_row(r) for r in rows),
                  key=lambda p: (p.status != "pending", -p.id))


@router.post("/proposals/{pid}/approve", response_model=ProposalRecord)
def approve_proposal(pid: int) -> ProposalRecord:
    res = training.approve(pid)
    if not res["ok"]:
        raise HTTPException(status_code=422, detail=res["error"])
    return _row(db.get_proposal(pid))


@router.post("/proposals/{pid}/reject", response_model=ProposalRecord)
def reject_proposal(pid: int, body: ProposalDecision) -> ProposalRecord:
    res = training.reject(pid, body.note)
    if not res["ok"]:
        raise HTTPException(status_code=422, detail=res["error"])
    return _row(db.get_proposal(pid))
