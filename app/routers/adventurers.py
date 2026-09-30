"""單一冒險者細節 API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db
from ..models import Ability, AbilityAdd, AdventurerDetail, IdentitySet
from ..scanner import get_adventurer

router = APIRouter(prefix="/api", tags=["adventurers"])


def _abilities(session_id: str) -> list[Ability]:
    return [Ability(id=r["id"], name=r["name"], created_at=r["created_at"])
            for r in db.list_abilities(session_id)]


@router.get("/adventurers/{session_id}", response_model=AdventurerDetail)
def adventurer_detail(session_id: str) -> AdventurerDetail:
    """回傳冒險者細節與近期冒險日誌 + 使用者自訂能力。"""
    adv = get_adventurer(session_id)
    if adv is None:
        raise HTTPException(status_code=404, detail="找不到這位冒險者")
    adv.custom_abilities = _abilities(session_id)
    return adv


@router.post("/adventurers/{session_id}/abilities", response_model=list[Ability])
def add_ability(session_id: str, body: AbilityAdd) -> list[Ability]:
    """給冒險者手動加一項能力標籤。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="能力名稱不可空白")
    db.add_ability(session_id, name[:40])
    return _abilities(session_id)


@router.delete("/adventurers/{session_id}/abilities/{ability_id}",
               response_model=list[Ability])
def remove_ability(session_id: str, ability_id: int) -> list[Ability]:
    """移除一項自訂能力。"""
    db.delete_ability(ability_id)
    return _abilities(session_id)


@router.post("/adventurers/{session_id}/identity")
def set_identity(session_id: str, body: IdentitySet) -> dict:
    """設定冒險者身份:取名 / 指定職業。取名會讓這位從合併的職業角色獨立出來。"""
    db.set_override(session_id, (body.name or "").strip()[:24] or None,
                    (body.klass or "").strip() or None)
    return {"ok": True, "id": session_id}


@router.post("/adventurers/{session_id}/hide")
def hide_adventurer(session_id: str) -> dict:
    """讓冒險者退隱 —— 僅從面板隱藏,不會刪除任何 session 檔案。"""
    db.hide_adventurer(session_id)
    return {"status": "hidden", "id": session_id}


@router.post("/adventurers/{session_id}/unhide")
def unhide_adventurer(session_id: str) -> dict:
    db.unhide_adventurer(session_id)
    return {"status": "visible", "id": session_id}
