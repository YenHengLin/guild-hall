"""guild-activity.jsonl 的輪替與歸檔。

零 token 觀測靠 PostToolUse hook 每個工具呼叫寫一行到 `~/.claude/guild-activity.jsonl`。
若永不清理,幾個月後檔案膨脹 → 面板 `/api/activity` 讀取變慢、hook 寫入也變慢。

哲學(見 wiki「冒險者公會改善計畫」#2):**存檔永遠是保底**。舊活動不丟棄,而是按月
搬到 `guild-activity.archive/<YYYY-MM>.jsonl`。這既控制主檔大小,又把歷史活動變成
熱力圖 / 戰報的原始素材(變廢為寶)。

任何錯誤都吞掉並降級,絕不讓輪替拖垮後端啟動。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("guild.activity_log")

LOG = Path(os.environ.get(
    "GUILD_ACTIVITY_LOG", str(Path.home() / ".claude" / "guild-activity.jsonl")))
ARCHIVE_DIR = LOG.parent / "guild-activity.archive"

# 主檔門檻:超過就輪替(改善計畫建議 5 MB)
MAX_BYTES = int(os.environ.get("GUILD_ACTIVITY_MAX_BYTES", str(5 * 1024 * 1024)))
# 主檔保留「最近 N 天」,更舊的搬去歸檔
KEEP_DAYS = int(os.environ.get("GUILD_ACTIVITY_KEEP_DAYS", "7"))


def _month_of(line: str) -> str:
    """從一行 JSON 取 ts 的 YYYY-MM;取不到就歸到 unknown 桶。"""
    try:
        ts = json.loads(line).get("ts") or ""
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m")
    except Exception:
        return "unknown"


def _ts_of(line: str) -> str:
    try:
        return json.loads(line).get("ts") or ""
    except Exception:
        return ""


def archive(lines: list[str]) -> int:
    """把多行活動按 ts 月份 append 到 archive/<YYYY-MM>.jsonl。回傳成功寫入行數。"""
    lines = [l for l in lines if l.strip()]
    if not lines:
        return 0
    buckets: dict[str, list[str]] = {}
    for l in lines:
        buckets.setdefault(_month_of(l), []).append(l)
    written = 0
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for month, ls in buckets.items():
            with (ARCHIVE_DIR / f"{month}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("\n".join(ls) + "\n")
            written += len(ls)
    except OSError as exc:
        log.warning("寫入歸檔失敗: %s", exc)
    return written


def rotate(max_bytes: int = MAX_BYTES, keep_days: int = KEEP_DAYS) -> dict:
    """主檔超過門檻時,把「早於 keep_days」的行按月搬進 archive/,主檔只留近期。

    回傳統計 dict。任何錯誤都吞掉並回報 rotated=False,絕不阻斷呼叫端。
    """
    try:
        if not LOG.exists() or LOG.stat().st_size <= max_bytes:
            return {"rotated": False, "archived": 0, "kept": 0}
        lines = [l for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError as exc:
        log.warning("讀取主檔失敗: %s", exc)
        return {"rotated": False, "archived": 0, "kept": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    old = [l for l in lines if _ts_of(l) < cutoff]
    keep = [l for l in lines if _ts_of(l) >= cutoff]

    # 極端情況:近 keep_days 的資料本身就超過門檻 → 至少搬走一半,避免主檔無限膨脹
    if not old and keep:
        half = len(keep) // 2
        old, keep = keep[:half], keep[half:]

    archived = archive(old)
    try:
        LOG.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    except OSError as exc:
        log.warning("寫回主檔失敗(歸檔已完成,主檔維持原狀): %s", exc)
        return {"rotated": False, "archived": archived, "kept": len(lines)}

    log.info("活動 log 輪替:歸檔 %d 行,主檔保留 %d 行", archived, len(keep))
    return {"rotated": True, "archived": archived, "kept": len(keep)}
