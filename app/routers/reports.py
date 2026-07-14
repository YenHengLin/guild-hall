"""公會戰報 API —— 今日/本週、熱力圖、成就、布告欄任務。"""

from __future__ import annotations

from fastapi import APIRouter

from ..models import Report
from ..scanner import scan_report

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/report", response_model=Report)
def guild_report() -> Report:
    """回傳整座公會的成長戰報,給大廳的櫃檯小姐與布告欄使用。"""
    return scan_report()
