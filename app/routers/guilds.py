"""公會列表 API。"""

from __future__ import annotations

from fastapi import APIRouter

from ..models import Guild
from ..scanner import scan_guilds

router = APIRouter(prefix="/api", tags=["guilds"])


@router.get("/guilds", response_model=list[Guild])
def list_guilds() -> list[Guild]:
    """回傳所有公會與其冒險者摘要(依活躍度排序)。"""
    return scan_guilds()
