"""櫃台回報 API —— 冒險者提交回報、查詢佇列狀態。"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..models import (
    QueueStatus, ReportRecord, ReportSubmit, ReviewReject, ReviewSkill,
)
from ..reception import reception_status

router = APIRouter(prefix="/api", tags=["reception"])


def _col(row, name, default=None):
    """安全讀取 sqlite Row 欄位(舊 DB 可能缺欄位)。"""
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


def _row_to_record(row) -> ReportRecord:
    try:
        files = json.loads(row["files_changed"] or "[]")
    except (json.JSONDecodeError, TypeError):
        files = []
    return ReportRecord(
        id=row["id"],
        adventurer_id=row["adventurer_id"],
        guild=row["guild"],
        title=row["title"],
        summary=row["summary"],
        files_changed=files,
        lessons=row["lessons"],
        outcome=row["outcome"] or "success",
        status=row["status"],
        result=row["result"],
        created_at=row["created_at"],
        processed_at=row["processed_at"],
        review=_col(row, "review") or "pending",
        review_note=_col(row, "review_note"),
    )


@router.post("/reports")
def submit_report(body: ReportSubmit) -> dict:
    """冒險者提交一筆回報 → 入列,由櫃台工人序列化寫回 Wiki。"""
    rid = db.enqueue(
        adventurer_id=body.adventurer_id,
        guild=body.guild,
        title=body.title.strip() or "無題回報",
        summary=body.summary,
        files_changed=json.dumps(body.files_changed, ensure_ascii=False),
        lessons=body.lessons,
        outcome=body.outcome,
    )
    return {"id": rid, "status": "queued"}


@router.get("/reports", response_model=list[ReportRecord])
def list_reports(limit: int = 30) -> list[ReportRecord]:
    return [_row_to_record(r) for r in db.list_reports(limit)]


@router.post("/reports/{report_id}/approve")
def approve_report(report_id: int) -> dict:
    """幕後審核:認可這份報告。"""
    if db.get_report(report_id) is None:
        raise HTTPException(status_code=404, detail="找不到這份報告")
    db.set_review(report_id, "approved", None)
    return {"ok": True, "review": "approved"}


@router.post("/reports/{report_id}/reject")
def reject_report(report_id: int, body: ReviewReject) -> dict:
    """幕後審核:退回報告 → 標記 rejected,並重新入列讓櫃台重寫一份改進版。

    用於「報告本身寫不清楚」的情況(不動實際工作,只重新整理文字)。
    另一種「實際工作沒做好」則由前端呼叫召回 fix 串流處理。
    """
    row = db.get_report(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這份報告")
    db.set_review(report_id, "rejected", body.note)

    # 退回櫃台重寫:以原內容 + 退回原因,重新入列一份(交由 worker 重新處理)
    orig_title = (row["title"] or "無題回報").lstrip("🔄 ").strip()
    summary = row["summary"] or ""
    if body.note:
        summary = f"{summary}\n\n[退回原因] {body.note}".strip()
    new_id = db.enqueue(
        adventurer_id=row["adventurer_id"], guild=row["guild"],
        title=f"🔄 重寫:{orig_title}", summary=summary,
        files_changed=row["files_changed"] or "[]",
        lessons=row["lessons"], outcome=row["outcome"] or "success",
    )
    return {"ok": True, "review": "rejected", "rewrite_id": new_id}


@router.get("/reports/queue", response_model=QueueStatus)
def queue_status() -> QueueStatus:
    c = db.counts()
    recent = [_row_to_record(r) for r in db.list_reports(12)]
    level, next_at, skills = reception_status(c["done"])
    return QueueStatus(
        **c, recent=recent,
        reception_level=level,
        reception_next_at=next_at,
        review_skills=[ReviewSkill(**s) for s in skills],
    )
