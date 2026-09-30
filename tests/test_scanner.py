"""scanner 純函式(無 I/O)的邊界驗收。

聚焦最容易寫錯邊界、卻過去零覆蓋的三塊:
- `level_from_xp`:每 100 xp 升一級,xp=0 也是 1 級(不會有 0 級)。
- `_heat_level`:單日次數 → 0-4 色階,邊界 0/5/20/50 的落點。
- `_compute_streak`:今天/昨天延續、斷點歸零的連續天數。
"""
from __future__ import annotations

from datetime import date, timedelta

from app import scanner


def _day(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ── level_from_xp:每 100 xp 一級,永不低於 1 ─────────────────
def test_level_zero_xp_is_level_one():
    assert scanner.level_from_xp(0) == 1


def test_level_just_below_threshold_stays():
    assert scanner.level_from_xp(99) == 1


def test_level_at_threshold_bumps():
    assert scanner.level_from_xp(100) == 2


def test_level_scales_linearly():
    assert scanner.level_from_xp(199) == 2
    assert scanner.level_from_xp(250) == 3


# ── _heat_level:單日次數 → 0-4 色階 ─────────────────────────
def test_heat_zero_is_empty():
    assert scanner._heat_level(0) == 0
    assert scanner._heat_level(-3) == 0     # 防呆:負數也視為無活動


def test_heat_low_band():
    assert scanner._heat_level(1) == 1
    assert scanner._heat_level(4) == 1      # < 5


def test_heat_mid_band():
    assert scanner._heat_level(5) == 2      # 邊界進位
    assert scanner._heat_level(19) == 2     # < 20


def test_heat_high_band():
    assert scanner._heat_level(20) == 3
    assert scanner._heat_level(49) == 3     # < 50


def test_heat_top_band():
    assert scanner._heat_level(50) == 4
    assert scanner._heat_level(999) == 4


# ── _compute_streak:連續有活動的天數 ───────────────────────
def test_streak_empty_is_zero():
    assert scanner._compute_streak(set()) == 0


def test_streak_today_only():
    assert scanner._compute_streak({_day(0)}) == 1


def test_streak_from_yesterday_when_today_idle():
    # 昨天有動、今天剛開機還沒動,仍算延續
    assert scanner._compute_streak({_day(1)}) == 1


def test_streak_consecutive_run():
    assert scanner._compute_streak({_day(0), _day(1), _day(2)}) == 3


def test_streak_breaks_on_gap():
    # 今天、昨天連著,但前天斷 → 只數到 2
    assert scanner._compute_streak({_day(0), _day(1), _day(3)}) == 2


def test_streak_zero_when_stale():
    # 最近一次活動在前天(今天、昨天皆無)→ 斷線歸零
    assert scanner._compute_streak({_day(2), _day(3)}) == 0
