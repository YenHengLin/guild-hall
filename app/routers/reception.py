"""櫃台回報 API —— 冒險者提交回報、查詢佇列狀態。"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db, classify as classify_mod, polish as polish_mod
from ..models import (
    PolishReq, QueueStatus, ReportRecord, ReportSubmit, ReviewReject, ReviewSkill,
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
    card = None
    raw_card = _col(row, "card")
    if raw_card:
        try:
            card = json.loads(raw_card)
        except (json.JSONDecodeError, TypeError):
            card = None
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
        card=card,
        to_wiki=_col(row, "to_wiki", 1) != 0,
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


@router.post("/reports/polish")
def polish_report(body: PolishReq) -> dict:
    """把使用者的草稿回報送 AI 潤稿(便宜、唯讀、不歸檔)→ 回傳整理後的文字。"""
    if not polish_mod.available():
        raise HTTPException(status_code=502, detail="找不到 claude CLI")
    if not (body.summary or body.title or "").strip():
        raise HTTPException(status_code=400, detail="草稿是空的,先寫點東西再潤稿")
    res = polish_mod.polish(body.title or "", body.summary, body.lessons)
    if not res["ok"]:
        raise HTTPException(status_code=502, detail=res.get("error") or "潤稿失敗")
    if res.get("cost"):
        db.add_cost("polish", float(res["cost"]), guild=body.guild,
                    model=polish_mod.MODEL, detail="回報潤稿")
    return res


@router.get("/reports", response_model=list[ReportRecord])
def list_reports(limit: int = 30) -> list[ReportRecord]:
    return [_row_to_record(r) for r in db.list_reports(limit)]


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

    # 學習迴路 A:退回時的說明 = 使用者的敘事偏好,存起來注入之後的合成講評
    if body.note:
        db.add_reception_pref(body.note, row["guild"])

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
        to_wiki=_col(row, "to_wiki", 1) != 0,
    )
    return {"ok": True, "review": "rejected", "rewrite_id": new_id}


@router.get("/lessons")
def lessons() -> dict:
    """學習書架:把所有教學字卡依 AI 分類彙整,讓使用者不必在櫃檯裡翻找。"""
    groups: dict[str, list] = {}
    for r in db.reports_with_card():
        try:
            card = json.loads(r["card"])
        except (json.JSONDecodeError, TypeError):
            continue
        if card.get("kind") != "teaching":
            continue
        cat = (card.get("category") or "未分類").strip() or "未分類"
        groups.setdefault(cat, []).append({
            "id": r["id"],
            "guild": r["guild"],
            "headline": card.get("headline") or r["title"] or "教學",
            "created_at": r["created_at"],
            "card": card,
        })
    # 卡片多的分類排前面;分類內新→舊(reports_with_card 已是新→舊)
    cats = [{"category": k, "count": len(v), "lessons": v}
            for k, v in groups.items()]
    cats.sort(key=lambda c: -c["count"])
    return {"categories": cats, "total": sum(c["count"] for c in cats)}


@router.post("/lessons/reclassify")
def reclassify_lessons() -> dict:
    """整理學習書架:替未分類 / 缺延伸主題的教學卡,用便宜模型補上分類 + 延伸主題。"""
    if not classify_mod.available():
        raise HTTPException(status_code=502, detail="找不到 claude CLI")
    rows = db.reports_with_card()

    def _load(r):
        try:
            c = json.loads(r["card"])
            return c if c.get("kind") == "teaching" else None
        except (json.JSONDecodeError, TypeError):
            return None

    # 現有(非未分類)分類 → 讓整理員沿用,相似的併同類
    cats: list[str] = []
    for r in rows:
        c = _load(r)
        cc = (c.get("category") or "").strip() if c else ""
        if cc and cc != "未分類" and cc not in cats:
            cats.append(cc)

    fixed, total_cost = 0, 0.0
    for r in rows:
        card = _load(r)
        if card is None:
            continue
        need_cat = (card.get("category") or "未分類") == "未分類"
        need_next = not card.get("next")
        if not (need_cat or need_next):
            continue
        fronts = [c.get("front") for c in (card.get("cards") or [])
                  if isinstance(c, dict) and c.get("front")]
        res = classify_mod.classify(card.get("headline"), fronts, cats)
        if not res["ok"]:
            continue
        if need_cat and res["category"]:
            card["category"] = res["category"]
            if res["category"] not in cats:
                cats.append(res["category"])
        if need_next and res["next"]:
            card["next"] = res["next"]
        db.update_report_card(r["id"], json.dumps(card, ensure_ascii=False))
        fixed += 1
        if isinstance(res.get("cost"), (int, float)):
            total_cost += res["cost"]

    if total_cost:
        db.add_cost("classify", float(total_cost), model=classify_mod.MODEL,
                    detail="整理學習書架")
    return {"ok": True, "fixed": fixed, "cost": total_cost}


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
        reception_prefs=[p["note"] for p in db.list_reception_prefs(8) if p["note"]],
        work_prefs=[w["note"] for w in db.list_work_prefs(None, 8) if w["note"]],
    )
