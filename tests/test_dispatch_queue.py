"""派遣佇列:驗證 FIFO 抽取、worker 依序執行、取消移出佇列。

不觸發真正的 claude —— 把 dispatch._loop 換成標記完成的假實作。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import db, dispatch


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """每個測試用獨立暫存 DB —— 絕不碰正式的 guild.db(以前會誤刪使用者委託)。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_guild.db")
    monkeypatch.setattr(db, "_conn", None)
    db.init_db()
    # 派遣的模組層狀態(執行中位子 / worker)每測試重置,避免互相污染
    monkeypatch.setattr(dispatch, "_running", {})
    monkeypatch.setattr(dispatch, "_worker_task", None)
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


# ── 並行派遣:不同資料夾同時跑、同資料夾互斥、並行上限 ─────────────

def _run_queue_tracking(monkeypatch, parallel: int) -> tuple[list[int], int]:
    """以假 _loop 跑佇列,回傳 (開跑順序, 同時在跑的最大數)。"""
    started: list[int] = []
    live = {"now": 0, "peak": 0}

    async def fake_loop(cid: int) -> None:
        started.append(cid)
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.05)  # 模擬執行一段時間,讓並行有機會重疊
        live["now"] -= 1
        db.update_commission(cid, status="done")

    monkeypatch.setattr(dispatch, "_loop", fake_loop)
    monkeypatch.setattr(dispatch, "PARALLEL", parallel)

    async def go() -> None:
        dispatch.ensure_worker()
        for _ in range(200):
            if not dispatch.queue_running():
                break
            await asyncio.sleep(0.02)

    asyncio.run(go())
    return started, live["peak"]


def test_parallel_different_folders_overlap(clean_commissions, monkeypatch, tmp_path):
    ids = [db.add_commission("g", f"P{i}", None) for i in range(3)]
    for i, cid in enumerate(ids):
        db.update_commission(cid, status="queued", cwd=str(tmp_path / f"f{i}"))
    started, peak = _run_queue_tracking(monkeypatch, parallel=2)
    assert sorted(started) == ids
    assert peak == 2  # 不同資料夾並行,但不超過上限
    assert db.next_queued() is None


def test_same_folder_is_serialized(clean_commissions, monkeypatch, tmp_path):
    ids = [db.add_commission("g", f"S{i}", None) for i in range(3)]
    for cid in ids:
        db.update_commission(cid, status="queued", cwd=str(tmp_path))
    started, peak = _run_queue_tracking(monkeypatch, parallel=3)
    assert started == ids  # 同資料夾依 FIFO 一張一張
    assert peak == 1


def test_self_folder_lets_same_cwd_run_parallel(clean_commissions, monkeypatch, tmp_path):
    ids = [db.add_commission("g", f"T{i}", None) for i in range(2)]
    for cid in ids:
        db.update_commission(cid, status="queued", cwd=str(tmp_path), self_folder=1)
    _started, peak = _run_queue_tracking(monkeypatch, parallel=2)
    assert peak == 2  # 各自的任務資料夾 → 不互卡


def test_block_reason_folder_and_capacity(clean_commissions, monkeypatch, tmp_path):
    a = db.add_commission("g", "A", None)
    b = db.add_commission("g", "B", None)
    c = db.add_commission("g", "C", None)
    db.update_commission(a, cwd=str(tmp_path / "x"))
    db.update_commission(b, cwd=str(tmp_path / "x"))
    db.update_commission(c, cwd=str(tmp_path / "y"))
    monkeypatch.setattr(dispatch, "PARALLEL", 2)
    dispatch._running[a] = dispatch._folder_key(db.get_commission(a))
    assert "同一資料夾" in dispatch.block_reason(db.get_commission(b))
    assert dispatch.block_reason(db.get_commission(c)) is None
    dispatch._running[c] = dispatch._folder_key(db.get_commission(c))
    assert "並行派遣已滿" in dispatch.block_reason(db.get_commission(b))


# ── 任務記憶(記憶特化)────────────────────────────────

def test_done_appends_task_memory_and_skips_wiki(clean_commissions, monkeypatch):
    cid = db.add_commission("g", "記憶任務", None)
    db.update_commission(cid, cwd="x", status="working", task_memory=1)
    monkeypatch.setattr(dispatch, "_run_round", lambda c, w, p, cid=None, rnd=None: {
        "ok": True, "text": "STATUS: DONE — 收工\nREPORT: 做完了\nLESSONS: 先重現再修",
        "session_id": "sid", "cost": 0})
    monkeypatch.setattr(dispatch, "ACCEPTANCE", False)

    asyncio.run(dispatch._loop(cid))

    mem = db.get_commission(cid)["memory"]
    assert "收工" in mem and "做完了" in mem and "先重現再修" in mem
    rep = db.list_reports(1)[0]
    assert rep["to_wiki"] == 0  # 記憶特化:不寫 wiki

    from app import reception
    status, result = reception.process_report(rep)
    assert status == "done" and "任務記憶" in result


def test_task_memory_injected_into_first_prompt(clean_commissions):
    cid = db.add_commission("g", "續做", None)
    db.update_commission(cid, task_memory=1)
    db.append_task_memory(cid, "[2026-01-01] ✅ 上次做到第二步")
    block = dispatch._task_memory_block(db.get_commission(cid))
    assert "上次做到第二步" in block
    assert "不要去讀寫全域 llm wiki" in block


def test_task_memory_is_capped(clean_commissions):
    cid = db.add_commission("g", "長", None)
    for i in range(50):
        db.append_task_memory(cid, f"第{i}筆 " + "字" * 200)
    mem = db.get_commission(cid)["memory"]
    assert len(mem) <= db._TASK_MEMORY_MAX + 1
    assert "第49筆" in mem  # 留最新的


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


# ── 結案回報解析(純函式,無副作用,決定 DONE/BLOCKED 與歸檔內容)──────

def test_parse_status_takes_last_line():
    """多個 STATUS 行時取最後一個 —— 前幾回合 WORKING 不該蓋掉最終 DONE。"""
    text = (
        "STATUS: WORKING — 第一回合做了一半\n"
        "中間一堆輸出\n"
        "STATUS: DONE — 全部完成"
    )
    status, msg = dispatch._parse_status(text)
    assert status == "DONE"
    assert msg == "全部完成"


def test_parse_status_normalizes_case_and_strips():
    """小寫 status 統一大寫;訊息頭尾殘留符號(破折號/星號)清掉。"""
    status, msg = dispatch._parse_status("status: done — **收工** ")
    assert status == "DONE"
    assert msg == "收工"


def test_parse_status_no_marker_is_working():
    """找不到 STATUS 行→視為 WORKING,訊息取最後一行。"""
    status, msg = dispatch._parse_status("隨便講點什麼\n最後一行")
    assert status == "WORKING"
    assert msg == "最後一行"


def test_parse_status_empty_text():
    """空輸出也要優雅處理,不可爆 IndexError。"""
    status, msg = dispatch._parse_status("   \n  ")
    assert status == "WORKING"
    assert msg == "(無輸出)"


def test_files_from_report_basic():
    text = "REPORT: 做了事\nFILES: app/a.py, app/b.py"
    assert dispatch._files_from_report(text) == ["app/a.py", "app/b.py"]


def test_files_from_report_no_value_returns_empty():
    """FILES: 無 → 回空清單(純分析類委託不應誤報改檔)。"""
    assert dispatch._files_from_report("FILES: 無") == []
    assert dispatch._files_from_report("FILES: 無檔案改動") == []
    assert dispatch._files_from_report("FILES: none") == []


def test_files_from_report_backtick_wrapped():
    """模型常把整段包成 `FILES: ...` code span;尾端反引號要清掉。"""
    text = "`FILES: app/a.py, app/b.py`"
    assert dispatch._files_from_report(text) == ["app/a.py", "app/b.py"]


def test_files_from_report_strips_backtick_and_bullet_per_item():
    """逐項去反引號與列點符號(模型常寫成 `- `app/a.py``)。"""
    text = "FILES: `app/a.py`、- app/b.py"
    assert dispatch._files_from_report(text) == ["app/a.py", "app/b.py"]


def test_files_from_report_missing_section():
    assert dispatch._files_from_report("REPORT: 沒有 FILES 段") == []


def test_parse_lessons_basic():
    assert dispatch._parse_lessons("LESSONS: 記得先重現 bug") == "記得先重現 bug"


def test_parse_lessons_no_value_returns_none():
    assert dispatch._parse_lessons("LESSONS: 無") is None
    assert dispatch._parse_lessons("LESSONS: none") is None


def test_parse_lessons_stops_at_card():
    """LESSONS 段邊界停在 CARD: 前,不應把 CARD 吃進來。"""
    text = "LESSONS: 心得內容\nCARD: {\"headline\":\"x\"}"
    assert dispatch._parse_lessons(text) == "心得內容"


def test_parse_lessons_missing_section():
    assert dispatch._parse_lessons("REPORT: 無 lessons") is None


def test_parse_card_basic():
    text = 'CARD: {"headline":"做了 X","why":{"a.py":"因為"}}'
    card = dispatch._parse_card(text)
    assert card["headline"] == "做了 X"
    assert card["why"] == {"a.py": "因為"}


def test_parse_card_backtick_code_span():
    """容忍 `CARD:` {…} 這種 code span(CARD 與 { 之間夾反引號/空白)。"""
    text = '`CARD:` {"headline":"ok"}'
    assert dispatch._parse_card(text) == {"headline": "ok"}


def test_parse_card_broken_json_returns_empty():
    """壞掉的 JSON 要優雅回 {},不可讓整個結案流程爆掉。"""
    assert dispatch._parse_card('CARD: {這不是 JSON}') == {}


def test_parse_card_missing_section():
    assert dispatch._parse_card("REPORT: 沒有 card 段") == {}


def test_parse_card_none_input():
    assert dispatch._parse_card(None) == {}


# ── 解決問題分類(_norm_category + coding 卡帶出 category/solved)──────

def test_norm_category_known_slug():
    assert dispatch._norm_category("bugfix") == "bugfix"
    assert dispatch._norm_category("FEATURE") == "feature"  # 大小寫不敏感


def test_norm_category_chinese_alias():
    assert dispatch._norm_category("修 bug") == "bugfix"
    assert dispatch._norm_category("重構") == "refactor"
    assert dispatch._norm_category("優化") == "perf"


def test_norm_category_unknown_falls_back_to_other():
    assert dispatch._norm_category("不知道是啥") == "other"
    assert dispatch._norm_category("") == "other"
    assert dispatch._norm_category(None) == "other"


def test_build_coding_card_carries_category_and_solved():
    """coding 卡要帶出 category(正規化後 slug)與 solved(解決了哪個問題)。"""
    text = ('STATUS: DONE — 完成\n'
            'CARD: {"headline":"修好派遣中斷","category":"修 bug",'
            '"solved":"uvicorn --reload 自噬導致反覆中斷","why":{"a.py":"拿掉 reload"}}')
    hunks = {"app/a.py": [{"before": "old", "after": "new"}]}
    card = json.loads(dispatch._build_coding_card(text, "備援標題", hunks))
    assert card["category"] == "bugfix"
    assert "uvicorn" in card["solved"]
    assert card["headline"] == "修好派遣中斷"


def test_build_coding_card_defaults_category_when_missing():
    """舊式/未附 category 的 CARD → 分類退回 other,solved 為空字串(不炸)。"""
    text = 'CARD: {"headline":"做了 X","why":{"a.py":"因為"}}'
    hunks = {"app/a.py": [{"before": "", "after": "x"}]}
    card = json.loads(dispatch._build_coding_card(text, "fb", hunks))
    assert card["category"] == "other"
    assert card["solved"] == ""


# ── 機關師 ops 卡(指令/服務皮膚)──────────────────────────

def test_collect_cmd_records_command_and_desc():
    cmds = []
    dispatch._collect_cmd(cmds, {"command": "docker compose up -d", "description": "起服務"})
    dispatch._collect_cmd(cmds, {"command": "", "description": "空指令略過"})
    assert cmds == [{"cmd": "docker compose up -d", "desc": "起服務"}]


def test_collect_cmd_respects_cap():
    cmds = [{"cmd": "x", "desc": ""} for _ in range(dispatch._MAX_CMDS)]
    dispatch._collect_cmd(cmds, {"command": "overflow"})
    assert len(cmds) == dispatch._MAX_CMDS  # 不超過上限


def test_build_ops_card_shows_steps_services_and_files():
    """機關師卡:帶出跑過的指令、動到的服務、設定檔異動 + category/solved。"""
    text = ('CARD: {"headline":"起好 homelab","category":"config",'
            '"solved":"qBittorrent 沒自動啟動","services":["qbittorrent","docker"],'
            '"why":{"docker-compose.yml":"補上 restart 策略"}}')
    cmds = [{"cmd": "docker compose up -d", "desc": "起服務"}]
    hunks = {"homelab/docker-compose.yml": [{"before": "a", "after": "b\nc"}]}
    card = json.loads(dispatch._build_ops_card(text, "fb", cmds, hunks))
    assert card["kind"] == "ops"
    assert card["category"] == "config"
    assert "qBittorrent" in card["solved"]
    assert card["steps"][0]["cmd"] == "docker compose up -d"
    assert "qbittorrent" in card["services"]
    assert card["files"][0]["path"] == "homelab/docker-compose.yml"
    assert card["files"][0]["why"] == "補上 restart 策略"


def test_build_ops_card_none_when_no_action():
    """純查看(沒跑指令、沒改設定檔)→ None,前端退回 markdown。"""
    assert dispatch._build_ops_card('CARD: {"headline":"看了一下"}', "fb", [], {}) is None


def test_skin_for_distinguishes_engineer_and_mechanic():
    """工程師→coding、機關師→ops、鑑定師→review:各職業皮膚互不相同。"""
    assert dispatch._skin_for("工程師", True) == "coding"
    assert dispatch._skin_for("機關師", True) == "ops"
    assert dispatch._skin_for("鑑定師", True) == "review"


# ── 鑑定師 review 卡(查驗清單/裁決皮膚)──────────────────────

def test_norm_verdict():
    assert dispatch._norm_verdict("pass") == "pass"
    assert dispatch._norm_verdict("通過") == "pass"
    assert dispatch._norm_verdict("FAIL") == "fail"
    assert dispatch._norm_verdict("未過") == "fail"
    assert dispatch._norm_verdict("亂寫") == "warn"  # 認不得 → 保守 warn


def test_build_review_card_checks_and_verdict():
    text = ('CARD: {"headline":"查驗登入流程","category":"test","verdict":"warn",'
            '"solved":"主流程OK但缺邊界測試",'
            '"checks":[{"item":"正常登入","ok":true},'
            '{"item":"錯誤密碼擋下","ok":false,"note":"沒測到"}]}')
    cmds = [{"cmd": "pytest -q", "desc": "跑測試"}]
    card = json.loads(dispatch._build_review_card(text, "fb", cmds))
    assert card["kind"] == "review"
    assert card["verdict"] == "warn"
    assert card["checks"][0]["ok"] is True
    assert card["checks"][1]["ok"] is False
    assert card["steps"][0]["cmd"] == "pytest -q"


def test_build_review_card_accepts_pass_alias_key():
    """checks 項目用 'pass' 鍵(非 'ok')也要認得。"""
    text = 'CARD: {"checks":[{"item":"X","pass":true}]}'
    card = json.loads(dispatch._build_review_card(text, "fb", []))
    assert card["checks"][0]["ok"] is True


def test_build_review_card_none_when_nothing():
    """沒查驗項目也沒跑指令(純瀏覽)→ None。"""
    assert dispatch._build_review_card('CARD: {"headline":"看了看"}', "fb", []) is None


# ── 斥候 ship 卡(出貨:commit/推送/部署皮膚)──────────────────

def test_skin_for_scout_is_ship():
    assert dispatch._skin_for("斥候", True) == "ship"


def test_build_ship_card_commits_and_filtered_cmds():
    """ship 卡:帶出 commit/目標,且只收「出貨型」指令(git/部署),過濾掉無關指令。"""
    text = ('CARD: {"headline":"發布 v1.2","category":"feature","target":"origin/main",'
            '"commits":["feat: 加派遣佇列","fix: 修接續"],"solved":"上線新版"}')
    cmds = [
        {"cmd": "git push origin main", "desc": "推上線"},
        {"cmd": "ls -la", "desc": "看檔案"},          # 非出貨型 → 過濾掉
        {"cmd": "docker push myimg:1.2", "desc": "推映像"},
    ]
    card = json.loads(dispatch._build_ship_card(text, "fb", cmds))
    assert card["kind"] == "ship"
    assert card["target"] == "origin/main"
    assert card["commits"] == ["feat: 加派遣佇列", "fix: 修接續"]
    cmdtexts = [s["cmd"] for s in card["steps"]]
    assert "git push origin main" in cmdtexts
    assert "docker push myimg:1.2" in cmdtexts
    assert "ls -la" not in cmdtexts  # 無關指令不入 ship 卡


def test_build_ship_card_none_when_no_shipment():
    """沒 commit、也沒出貨指令 → None(前端退回 markdown)。"""
    cmds = [{"cmd": "cat README.md", "desc": "看說明"}]
    assert dispatch._build_ship_card('CARD: {"headline":"看了看"}', "fb", cmds) is None
