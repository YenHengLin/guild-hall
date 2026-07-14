"""冒險者進化 API —— 每完成 5 次任務可進化一次,依解決內容提示進化方向。

面板只負責:偵測「可進化」+ 匯整素材 + 記錄已習得能力。
真正的進化動作(寫 memory / 建 skill / 找工具 / 寫 script)由 `訓練所` skill 在 Claude 內執行,
完成後 POST 回這裡登記。
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..models import EvolutionCandidate, EvolutionRecord, EvolutionSubmit

router = APIRouter(prefix="/api", tags=["evolution"])

# 關鍵字 → 建議進化路線(粗略啟發式;真正判斷交給訓練所 skill)
_ROUTE_HINTS = [
    ("tool", ["工具", "tool", "慢", "手動", "繁瑣", "cli", "指令"]),
    ("script", ["腳本", "script", "自動化", "重複", "每次都", "批次"]),
    ("skill", ["流程", "步驟", "skill", "重寫", "固化", "workflow"]),
    ("memory", ["記住", "心得", "教訓", "偏好", "慣例", "設定"]),
]


def _suggest_route(titles: list[str], lessons: list[str]) -> str:
    blob = " ".join(titles + lessons).lower()
    for route, kws in _ROUTE_HINTS:
        if any(k.lower() in blob for k in kws):
            return route
    return "memory"


def _record(row) -> EvolutionRecord:
    return EvolutionRecord(
        id=row["id"], adventurer_id=row["adventurer_id"], guild=row["guild"],
        route=row["route"], name=row["name"], detail=row["detail"],
        based_on=row["based_on"] or 0, created_at=row["created_at"],
    )


@router.get("/evolutions", response_model=list[EvolutionCandidate])
def list_candidates() -> list[EvolutionCandidate]:
    """回傳所有「有完成任務或已習得能力」的冒險者進化狀態。"""
    completed = db.completed_count_map()
    claimed_map = db.evolution_count_map()
    ids = set(completed) | set(claimed_map)

    out: list[EvolutionCandidate] = []
    for aid in ids:
        done = completed.get(aid, 0)
        earned = done // db.EVOLVE_EVERY
        claimed = claimed_map.get(aid, 0)
        learned_rows = db.list_evolutions(aid)
        reports = db.completed_reports(aid)
        titles = [r["title"] for r in reports[:5] if r["title"]]
        lessons = [r["lessons"] for r in reports[:5] if r["lessons"]]
        guild = reports[0]["guild"] if reports else (
            learned_rows[0]["guild"] if learned_rows else None)
        name = titles[0][:16] if titles else aid[:8]
        out.append(EvolutionCandidate(
            adventurer_id=aid,
            guild=guild,
            name=name,
            completed=done,
            earned=earned,
            claimed=claimed,
            available=max(0, earned - claimed),
            suggested_route=_suggest_route(titles, lessons),  # type: ignore[arg-type]
            recent_titles=titles,
            recent_lessons=lessons,
            learned=[_record(r) for r in learned_rows],
        ))
    # 可進化的排前面
    out.sort(key=lambda c: (-c.available, -c.completed))
    return out


@router.post("/evolutions")
def submit_evolution(body: EvolutionSubmit) -> dict:
    """登記一次進化(冒險者習得新能力)。"""
    based_on = db.completed_count_map().get(body.adventurer_id or "", 0)
    eid = db.add_evolution(
        adventurer_id=body.adventurer_id,
        guild=body.guild,
        route=body.route,
        name=body.name.strip() or "未命名能力",
        detail=body.detail,
        based_on=based_on,
    )
    return {"id": eid, "status": "evolved", "route": body.route,
            "label": db.ROUTE_LABEL.get(body.route, body.route)}
