"""公會夜巡(muse)護欄與 JSON 擷取測試。

聚焦四塊過去零覆蓋的邏輯:
- `_within_window`:跨午夜時段(hour_start > hour_end)的邊界判斷。
- `_extract_ideas`:markdown ```json 包裹的解析、以及壞輸出的優雅降級。
- `spent_today` / `budget_left`:每日預算計算。
- `run_patrol_once`:預算耗盡不巡、時段外跳過(全程 mock,不觸發真正的 claude)。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app import db, muse, scanner
from app.models import MuseConfig


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """每個測試用獨立暫存 DB —— 絕不碰正式的 guild.db。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_guild.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    monkeypatch.setattr(db, "_conn", None)


# ── _within_window:時段判斷(含跨夜) ─────────────────────────

def _at(hour: int) -> datetime:
    return datetime(2026, 9, 8, hour, 30, 0)


def test_window_normal_daytime():
    """一般不跨夜時段 9–17:區間內為真,邊界 end 為假(半開區間)。"""
    cfg = MuseConfig(hour_start=9, hour_end=17)
    assert muse._within_window(cfg, _at(9)) is True
    assert muse._within_window(cfg, _at(16)) is True
    assert muse._within_window(cfg, _at(17)) is False   # end 不含
    assert muse._within_window(cfg, _at(8)) is False
    assert muse._within_window(cfg, _at(20)) is False


def test_window_cross_midnight():
    """跨夜時段 22–6:午夜前後皆為真,白天為假。"""
    cfg = MuseConfig(hour_start=22, hour_end=6)
    assert muse._within_window(cfg, _at(22)) is True    # 起點含
    assert muse._within_window(cfg, _at(23)) is True
    assert muse._within_window(cfg, _at(0)) is True
    assert muse._within_window(cfg, _at(5)) is True
    assert muse._within_window(cfg, _at(6)) is False    # 迄點不含
    assert muse._within_window(cfg, _at(12)) is False
    assert muse._within_window(cfg, _at(21)) is False


def test_window_default_deep_night():
    """預設 1–6 點:凌晨為真、日間為假。"""
    cfg = MuseConfig()  # hour_start=1, hour_end=6
    assert muse._within_window(cfg, _at(1)) is True
    assert muse._within_window(cfg, _at(5)) is True
    assert muse._within_window(cfg, _at(6)) is False
    assert muse._within_window(cfg, _at(0)) is False
    assert muse._within_window(cfg, _at(13)) is False


def test_window_all_day_when_equal():
    """start == end 視為整天皆可巡。"""
    cfg = MuseConfig(hour_start=3, hour_end=3)
    for h in (0, 3, 12, 23):
        assert muse._within_window(cfg, _at(h)) is True


# ── _extract_ideas:JSON 擷取 ────────────────────────────────

def test_extract_plain_array():
    text = '[{"title": "甲", "kind": "bug"}, {"title": "乙"}]'
    out = muse._extract_ideas(text)
    assert [d["title"] for d in out] == ["甲", "乙"]


def test_extract_markdown_fenced_json():
    """Claude 常把 JSON 包在 ```json ... ``` 裡 —— 必須仍能撈出。"""
    text = '這是我的巡邏結果:\n```json\n[{"title": "改善 X", "effort": "S"}]\n```\n以上。'
    out = muse._extract_ideas(text)
    assert len(out) == 1
    assert out[0]["title"] == "改善 X"


def test_extract_empty_array():
    assert muse._extract_ideas("專案很乾淨\n[]") == []


def test_extract_no_json_returns_empty():
    """沒有任何陣列 → 優雅降級為空,不拋例外。"""
    assert muse._extract_ideas("抱歉我看不出可改進處。") == []


def test_extract_broken_json_returns_empty():
    """壞掉的 JSON(缺右括號等)→ 優雅降級為空。"""
    assert muse._extract_ideas('[{"title": "壞掉}') == []


def test_extract_filters_non_dict_items():
    """陣列裡混入非物件元素時,只保留 dict。"""
    out = muse._extract_ideas('[{"title": "留下"}, "雜訊", 42]')
    assert out == [{"title": "留下"}]


# ── spent_today / budget_left ───────────────────────────────

def test_budget_left_reflects_spend():
    cfg = MuseConfig(daily_budget_usd=1.0)
    assert muse.budget_left(cfg) == 1.0     # 尚無花費
    db.add_cost("muse", 0.3, guild="g", model="m", detail="測試")
    assert muse.budget_left(cfg) == pytest.approx(0.7)
    assert muse.spent_today() == pytest.approx(0.3)


# ── run_patrol_once:護欄(全程 mock,不啟動 claude) ──────────

def _patch_common(monkeypatch, *, available=True):
    monkeypatch.setattr(muse, "available", lambda: available)


def test_patrol_skips_when_disabled(monkeypatch):
    _patch_common(monkeypatch)
    muse.save_config(MuseConfig(enabled=False))
    res = muse.run_patrol_once(manual=False)
    assert res["skipped"] is True and "未啟用" in res["reason"]


def test_patrol_skips_out_of_window(monkeypatch):
    """啟用但非巡邏時段 → 跳過,且完全不呼叫 subprocess。"""
    _patch_common(monkeypatch)
    muse.save_config(MuseConfig(enabled=True, hour_start=1, hour_end=6))

    called = {"survey": False}
    monkeypatch.setattr(muse, "_run_survey",
                        lambda *a, **k: called.__setitem__("survey", True))
    # 強制「現在」落在時段外(中午)
    monkeypatch.setattr(muse, "_within_window", lambda cfg, now: False)

    res = muse.run_patrol_once(manual=False)
    assert res["skipped"] is True and "非巡邏時段" in res["reason"]
    assert called["survey"] is False


def test_patrol_skips_when_budget_exhausted(monkeypatch):
    """當日花費達上限 → 不巡,且不啟動巡視。"""
    _patch_common(monkeypatch)
    muse.save_config(MuseConfig(enabled=True, daily_budget_usd=1.0))
    monkeypatch.setattr(muse, "_within_window", lambda cfg, now: True)
    db.add_cost("muse", 1.0, guild="g", model="m", detail="用光")

    called = {"survey": False}
    monkeypatch.setattr(muse, "_run_survey",
                        lambda *a, **k: called.__setitem__("survey", True))

    res = muse.run_patrol_once(manual=False)
    assert res["skipped"] is True and "預算已用罄" in res["reason"]
    assert called["survey"] is False


def test_patrol_errors_when_claude_missing(monkeypatch):
    _patch_common(monkeypatch, available=False)
    muse.save_config(MuseConfig(enabled=True))
    res = muse.run_patrol_once(manual=True)
    assert res["ok"] is False and "claude" in res["error"]


def test_patrol_stores_ideas_from_survey(monkeypatch):
    """完整快樂路徑:mock 巡視回傳 markdown 包裹 JSON → 靈感卡入庫。"""
    _patch_common(monkeypatch)
    muse.save_config(MuseConfig(enabled=True, ideas_per_run=3))
    monkeypatch.setattr(muse, "_within_window", lambda cfg, now: True)
    monkeypatch.setattr(muse, "_pick_guild", lambda cfg: ("demo", "/tmp/demo"))

    survey_text = '```json\n[{"kind": "bug", "title": "空指標風險", "effort": "M"}]\n```'
    monkeypatch.setattr(
        muse, "_run_survey",
        lambda prompt, cwd, model: {"ok": True, "text": survey_text, "cost": 0.02})

    res = muse.run_patrol_once(manual=True)
    assert res["ok"] is True and res["guild"] == "demo" and res["added"] == 1
    assert db.idea_title_exists("demo", "空指標風險") is True
    # round-robin 記錄已更新
    assert db.get_setting(muse._LAST_GUILD_KEY) == "demo"


def test_patrol_survey_failure_bubbles_error(monkeypatch):
    """巡視失敗(如逾時)→ 回報錯誤,仍輪替公會避免卡死。"""
    _patch_common(monkeypatch)
    muse.save_config(MuseConfig(enabled=True))
    monkeypatch.setattr(muse, "_within_window", lambda cfg, now: True)
    monkeypatch.setattr(muse, "_pick_guild", lambda cfg: ("demo", "/tmp/demo"))
    monkeypatch.setattr(
        muse, "_run_survey",
        lambda *a, **k: {"ok": False, "error": "夜巡逾時(420s)"})

    res = muse.run_patrol_once(manual=True)
    assert res["ok"] is False and "逾時" in res["error"]
    assert db.get_setting(muse._LAST_GUILD_KEY) == "demo"
