"""派遣佇列:驗證 FIFO 抽取、worker 依序執行、取消移出佇列。

不觸發真正的 claude —— 把 dispatch._loop 換成標記完成的假實作。
"""
from __future__ import annotations

import asyncio

import pytest

from app import db, dispatch


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """每個測試用獨立暫存 DB —— 絕不碰正式的 guild.db(以前會誤刪使用者委託)。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_guild.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    yield
    if db._conn is not None:
        db._conn.close()
    monkeypatch.setattr(db, "_conn", None)


@pytest.fixture()
def clean_commissions():
    # isolated_db 已保證每測試都是全新 DB;此 fixture 保留只為相容既有測試簽名
    yield


def test_next_queued_fifo(clean_commissions):
    db.init_db()
    a = db.add_commission("g", "A", None)
    b = db.add_commission("g", "B", None)
    db.update_commission(a, status="queued", cwd="x")
    db.update_commission(b, status="queued", cwd="x")
    assert db.next_queued() == a  # FIFO by id
    assert db.queued_ids() == [a, b]


def test_worker_runs_queue_in_order(clean_commissions, monkeypatch):
    db.init_db()
    ids = [db.add_commission("g", f"C{i}", None) for i in range(3)]
    for cid in ids:
        db.update_commission(cid, status="queued", cwd="x")

    ran: list[int] = []

    async def fake_loop(cid: int) -> None:
        ran.append(cid)
        db.update_commission(cid, status="done")

    monkeypatch.setattr(dispatch, "_loop", fake_loop)

    async def go() -> None:
        dispatch.ensure_worker()
        # 等 worker 抽完佇列
        for _ in range(50):
            if not dispatch.queue_running():
                break
            await asyncio.sleep(0.02)

    asyncio.run(go())
    assert ran == ids  # 依序跑完
    assert db.next_queued() is None


def test_cancel_queued_skips(clean_commissions, monkeypatch):
    db.init_db()
    a = db.add_commission("g", "A", None)
    b = db.add_commission("g", "B", None)
    db.update_commission(a, status="queued", cwd="x")
    db.update_commission(b, status="queued", cwd="x")
    # 取消 a(回到 open)→ worker 應跳過 a、只跑 b
    db.update_commission(a, status="open")

    ran: list[int] = []

    async def fake_loop(cid: int) -> None:
        ran.append(cid)
        db.update_commission(cid, status="done")

    monkeypatch.setattr(dispatch, "_loop", fake_loop)

    async def go() -> None:
        dispatch.ensure_worker()
        for _ in range(50):
            if not dispatch.queue_running():
                break
            await asyncio.sleep(0.02)

    asyncio.run(go())
    assert ran == [b]


# ── 櫃台驗收 / 自動退回重做 ──────────────────────────────

def _events_text(cid: int) -> str:
    return "\n".join(e["content"] for e in db.list_events(cid))


def test_acceptance_rework_then_pass(clean_commissions, monkeypatch):
    """驗收第一次未過→自動退回→第二次通過→委託完成。"""
    db.init_db()
    cid = db.add_commission("g", "做一個功能", None)
    db.update_commission(cid, cwd="x", status="working")

    def fake_round(cmd, cwd, prompt, cid=None, rnd=None):
        return {"ok": True, "text": "STATUS: DONE — 完成\nREPORT: 做了東西",
                "session_id": "sid-1", "cost": 0.01}

    verdicts = [
        {"passed": False, "reason": "少了測試", "cost": 0.001},
        {"passed": True, "reason": "達標", "cost": 0.001},
    ]

    def fake_review(row, done_text, cwd):
        return verdicts.pop(0)

    monkeypatch.setattr(dispatch, "_run_round", fake_round)
    monkeypatch.setattr(dispatch, "_acceptance_review", fake_review)
    monkeypatch.setattr(dispatch, "MAX_REWORK", 1)

    asyncio.run(dispatch._loop(cid))

    row = db.get_commission(cid)
    assert row["status"] == "done"
    txt = _events_text(cid)
    assert "驗收未過" in txt and "驗收通過" in txt


def test_acceptance_fail_exhausts_to_blocked(clean_commissions, monkeypatch):
    """驗收持續未過且用盡重做上限→轉人工(blocked)。"""
    db.init_db()
    cid = db.add_commission("g", "做一個功能", None)
    db.update_commission(cid, cwd="x", status="working")

    monkeypatch.setattr(dispatch, "_run_round", lambda c, w, p, cid=None, rnd=None: {
        "ok": True, "text": "STATUS: DONE — 完成\nREPORT: x",
        "session_id": "sid", "cost": 0.01})
    monkeypatch.setattr(dispatch, "_acceptance_review",
                        lambda r, t, w: {"passed": False, "reason": "還是不行", "cost": 0})
    monkeypatch.setattr(dispatch, "MAX_REWORK", 1)

    asyncio.run(dispatch._loop(cid))

    assert db.get_commission(cid)["status"] == "blocked"
    assert "轉人工審核" in _events_text(cid)


def test_verdict_regex():
    m = dispatch._VERDICT_RE.search("看了一下\nVERDICT: FAIL — 缺少錯誤處理")
    assert m and m.group(1).upper() == "FAIL"
    assert "缺少錯誤處理" in m.group(2)
    m2 = dispatch._VERDICT_RE.search("VERDICT: PASS — 一切就緒")
    assert m2 and m2.group(1).upper() == "PASS"
