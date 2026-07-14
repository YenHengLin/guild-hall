"""即時動態 API —— 讀 PostToolUse hook 寫的活動 log,做零 token 觀測。

hook(hooks/activity_logger.py)每次工具呼叫寫一行 JSON。這裡讀尾端幾行給大廳顯示,
完全不經過模型 → 不花 token。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from ..models import ActivityEntry

router = APIRouter(prefix="/api", tags=["activity"])

LOG = Path(os.environ.get(
    "GUILD_ACTIVITY_LOG", str(Path.home() / ".claude" / "guild-activity.jsonl")))


def _guild_of(cwd: str | None) -> str | None:
    if not cwd:
        return None
    return Path(cwd).name or cwd


@router.get("/activity", response_model=list[ActivityEntry])
def recent_activity(limit: int = 40) -> list[ActivityEntry]:
    """回傳最近的工具活動(最新在前)。"""
    if not LOG.exists():
        return []
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    out: list[ActivityEntry] = []
    for line in reversed(lines[-limit:]):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(ActivityEntry(
            ts=d.get("ts"),
            session_id=d.get("session_id"),
            guild=_guild_of(d.get("cwd")),
            tool=d.get("tool", "?"),
            summary=d.get("summary", ""),
        ))
    return out
