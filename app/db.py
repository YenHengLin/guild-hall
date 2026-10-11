"""SQLite 佇列資料層 —— 冒險者回報單的持久化與領取。

回報寫入的是「共享的 LLM Wiki 檔」(log.md 等),多位冒險者可同時回報,
因此用這張佇列表 + 單一消費者 worker(見 reception.py)把寫入序列化,避免競寫。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "guild.db"

# 用 RLock(可重入):init_db()/_migrate() 等會在持鎖狀態下呼叫 get_conn(),
# 而 get_conn() 內初始化也要上同一把鎖 —— 可重入才不會自我死鎖。
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    adventurer_id TEXT,
    guild         TEXT,
    title         TEXT,
    summary       TEXT,
    files_changed TEXT,           -- JSON 陣列字串
    lessons       TEXT,
    outcome       TEXT DEFAULT 'success',
    status        TEXT DEFAULT 'pending',   -- pending/processing/done/failed
    result        TEXT,           -- 存檔路徑或錯誤訊息
    created_at    TEXT,
    processed_at  TEXT,
    card          TEXT            -- 職業結果卡(JSON;如 coding 卡的檔案 diff),供前端依職業渲染
);

CREATE TABLE IF NOT EXISTS hidden_adventurers (
    adventurer_id TEXT PRIMARY KEY,
    hidden_at     TEXT
);

CREATE TABLE IF NOT EXISTS commissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild       TEXT,
    title       TEXT,
    note        TEXT,
    status      TEXT DEFAULT 'open',   -- open / done
    created_at  TEXT,
    done_at     TEXT,
    klass       TEXT                   -- 指定職業/呈現皮膚(coding diff / 教學字卡…)
);

CREATE TABLE IF NOT EXISTS evolutions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    adventurer_id TEXT,
    guild         TEXT,
    route         TEXT,   -- memory / skill / tool / script
    name          TEXT,   -- 習得的能力 / skill 名稱
    detail        TEXT,   -- 說明 / 產出位置
    based_on      INTEGER, -- 進化當下累積的完成任務數
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS abilities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    adventurer_id TEXT,   -- 綁到 session
    name          TEXT,   -- 使用者自訂的能力標籤
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS commission_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    commission_id INTEGER,
    type          TEXT,   -- steer(使用者中途指示)/ progress(回合進度)/ system(派遣狀態)
    content       TEXT,
    round         INTEGER,
    consumed      INTEGER DEFAULT 0,  -- steer 是否已注入某回合
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT,    -- dispatch / recall / training / synthesis
    commission_id INTEGER, -- 派遣才有;其餘為 NULL
    adventurer_id TEXT,
    guild         TEXT,
    model         TEXT,
    cost          REAL,
    detail        TEXT,    -- 例:回合 3
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS evolution_proposals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    adventurer_id    TEXT,    -- distill(全公會提煉)時為 NULL
    guild            TEXT,
    route            TEXT,    -- memory / skill / tool / script
    name             TEXT,
    detail           TEXT,    -- 為什麼值得固化(提案理由)
    artifact_path    TEXT,    -- 批准後要寫到哪
    artifact_content TEXT,    -- 批准後要寫入的完整內容
    status           TEXT DEFAULT 'pending',  -- pending / approved / rejected
    decided_note     TEXT,
    created_at       TEXT,
    decided_at       TEXT
);

-- 學習迴路 A:使用者退回報告時說的「哪裡不清楚」→ 櫃台學到的敘事偏好
-- (注入深度合成講評 prompt,讓之後的報告照使用者喜歡的方式寫)
CREATE TABLE IF NOT EXISTS reception_prefs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild      TEXT,
    note       TEXT,
    created_at TEXT
);

-- 學習迴路 B:使用者叫冒險者繼續做時給的指示 → 冒險者學到的做事方式
-- (注入派遣 prompt 的能力段,並作為訓練所提煉 skill 的素材)
CREATE TABLE IF NOT EXISTS work_prefs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild         TEXT,
    adventurer_id TEXT,
    note          TEXT,
    created_at    TEXT
);

-- 公會夜巡:NPC 冒險者在預算/時段內自主巡視各專案,產出的「改善靈感卡」
CREATE TABLE IF NOT EXISTS muse_ideas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild      TEXT,
    cwd        TEXT,     -- 產這則靈感時巡的資料夾(採納成委託時當工作目錄)
    kind       TEXT,     -- improve / bug / refactor / idea / test / doc …
    title      TEXT,
    body       TEXT,     -- markdown:為什麼、怎麼做、預期效益
    effort     TEXT,     -- S / M / L(粗估工作量)
    auto_safe  INTEGER,  -- 1/0/NULL:模型判定是否可安全自動執行
    status     TEXT DEFAULT 'new',  -- new / promoted(已轉委託)/ dismissed
    created_at TEXT
);

-- 通用鍵值設定(目前存夜巡設定 JSON)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- 冒險者身份覆蓋:使用者手動取的名字 / 指定的職業(依 session id)
CREATE TABLE IF NOT EXISTS session_overrides (
    session_id TEXT PRIMARY KEY,
    name       TEXT,
    klass      TEXT,
    updated_at TEXT
);
"""

# 每完成 N 次任務可進化一次
EVOLVE_EVERY = 5

ROUTE_LABEL = {
    "memory": "🧠 記住通用能力",
    "skill": "📜 進化 / 新建 skill",
    "tool": "🔧 採用更好的工具",
    "script": "⚙️ 自己寫工具",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    global _conn
    # 初始化必須在 _lock 內:FastAPI 多執行緒跑路由時,兩條執行緒可同時通過
    # 「_conn is None」判斷並各自 sqlite3.connect(),後者覆蓋前者 → 連線洩漏、
    # WAL PRAGMA 也只設到被丟棄的那條。鎖內再判一次(double-checked)確保只建一次。
    if _conn is None:
        with _lock:
            if _conn is None:
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                _conn = conn
    return _conn


def init_db() -> None:
    with _lock:
        get_conn().executescript(SCHEMA)
        get_conn().commit()
    _migrate()


def _migrate() -> None:
    """向後相容:替既有 reports 表補上審核欄位(舊 DB 沒有這些欄位)。"""
    with _lock:
        c = get_conn()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(reports)")}
        # review: pending / approved / rejected(使用者幕後審核結果)
        if "review" not in cols:
            c.execute("ALTER TABLE reports ADD COLUMN review TEXT DEFAULT 'pending'")
        if "review_note" not in cols:
            c.execute("ALTER TABLE reports ADD COLUMN review_note TEXT")
        # 職業結果卡(JSON):讓結果依冒險者職業渲染成專屬介面(如 coding diff 卡)
        if "card" not in cols:
            c.execute("ALTER TABLE reports ADD COLUMN card TEXT")
        # 記憶特化:0 = 只留任務記憶,不寫入 llm wiki(減少 wiki 雜訊)
        if "to_wiki" not in cols:
            c.execute("ALTER TABLE reports ADD COLUMN to_wiki INTEGER DEFAULT 1")
        # 派遣系統:替既有 commissions 表補欄位
        ccols = {r["name"] for r in c.execute("PRAGMA table_info(commissions)")}
        for col, ddl in [
            ("goal", "ALTER TABLE commissions ADD COLUMN goal TEXT"),
            ("cwd", "ALTER TABLE commissions ADD COLUMN cwd TEXT"),
            ("session_id", "ALTER TABLE commissions ADD COLUMN session_id TEXT"),
            ("rounds", "ALTER TABLE commissions ADD COLUMN rounds INTEGER DEFAULT 0"),
            ("stop_requested",
             "ALTER TABLE commissions ADD COLUMN stop_requested INTEGER DEFAULT 0"),
            # 冒險者位階:派遣時用的模型(空 = 跟隨 CLI 預設)
            ("model", "ALTER TABLE commissions ADD COLUMN model TEXT"),
            # 成本帳本:此委託累計花費(USD,跨多次派遣累加)
            ("cost", "ALTER TABLE commissions ADD COLUMN cost REAL DEFAULT 0"),
            # 附圖:JSON 陣列,存 /uploads/... URL(供顯示與派遣時注入圖片路徑)
            ("images", "ALTER TABLE commissions ADD COLUMN images TEXT"),
            # 指定職業/呈現皮膚(決定派遣結果卡的種類:coding diff / 教學字卡)
            ("klass", "ALTER TABLE commissions ADD COLUMN klass TEXT"),
            # 領域公會(以「做的事」分類,如「金融公會」;疊加在資料夾公會之上)
            ("domain", "ALTER TABLE commissions ADD COLUMN domain TEXT"),
            # 自建任務資料夾:派遣時在專屬子資料夾執行,避免並行任務共用資料夾互卡
            ("self_folder",
             "ALTER TABLE commissions ADD COLUMN self_folder INTEGER DEFAULT 0"),
            # 任務記憶:此委託自己累積的成果/心得(續派注入,不必撈全域 wiki)
            ("memory", "ALTER TABLE commissions ADD COLUMN memory TEXT"),
            # 1 = 記憶特化在任務內,結案回報不寫入 llm wiki
            ("task_memory",
             "ALTER TABLE commissions ADD COLUMN task_memory INTEGER DEFAULT 0"),
        ]:
            if col not in ccols:
                c.execute(ddl)
        # 夜巡靈感:補 auto_safe 欄(舊 DB 沒有)
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(muse_ideas)")}
        if "muse_ideas" in {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")} \
                and "auto_safe" not in mcols:
            c.execute("ALTER TABLE muse_ideas ADD COLUMN auto_safe INTEGER")
        c.commit()


def enqueue(
    adventurer_id: str | None,
    guild: str | None,
    title: str,
    summary: str | None,
    files_changed: str,
    lessons: str | None,
    outcome: str,
    card: str | None = None,
    to_wiki: bool = True,
) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            """INSERT INTO reports
               (adventurer_id, guild, title, summary, files_changed, lessons,
                outcome, status, created_at, card, to_wiki)
               VALUES (?,?,?,?,?,?,?, 'pending', ?, ?, ?)""",
            (adventurer_id, guild, title, summary, files_changed, lessons,
             outcome, _now(), card, 1 if to_wiki else 0),
        )
        c.commit()
        return int(cur.lastrowid)


def claim_next() -> sqlite3.Row | None:
    """原子地領取最舊的一筆 pending,標記為 processing。單一 worker 呼叫。"""
    with _lock:
        c = get_conn()
        row = c.execute(
            "SELECT * FROM reports WHERE status='pending' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        c.execute("UPDATE reports SET status='processing' WHERE id=?", (row["id"],))
        c.commit()
        # 回傳更新後的視圖
        return c.execute("SELECT * FROM reports WHERE id=?", (row["id"],)).fetchone()


def finish(report_id: int, status: str, result: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE reports SET status=?, result=?, processed_at=? WHERE id=?",
            (status, result, _now(), report_id),
        )
        c.commit()


def list_reports(limit: int = 30) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def get_report(report_id: int) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM reports WHERE id=?", (report_id,)
        ).fetchone()


def set_review(report_id: int, review: str, note: str | None) -> None:
    """記錄使用者(幕後審核)對報告的裁決:approved / rejected。"""
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE reports SET review=?, review_note=? WHERE id=?",
            (review, note, report_id),
        )
        c.commit()


def counts() -> dict[str, int]:
    with _lock:
        rows = get_conn().execute(
            "SELECT status, COUNT(*) n FROM reports GROUP BY status"
        ).fetchall()
    out = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for r in rows:
        out[r["status"]] = r["n"]
    return out


# ── 退隱冒險者(從面板隱藏,不刪 jsonl)────────────────────

def hide_adventurer(adventurer_id: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT OR IGNORE INTO hidden_adventurers (adventurer_id, hidden_at) "
            "VALUES (?, ?)",
            (adventurer_id, _now()),
        )
        c.commit()


def unhide_adventurer(adventurer_id: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM hidden_adventurers WHERE adventurer_id=?",
                  (adventurer_id,))
        c.commit()


def hidden_ids() -> set[str]:
    with _lock:
        try:
            rows = get_conn().execute(
                "SELECT adventurer_id FROM hidden_adventurers"
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
    return {r["adventurer_id"] for r in rows}


# ── 委託布告欄(使用者自己貼的任務)────────────────────────

def add_commission(guild: str | None, title: str, note: str | None,
                   images: str | None = None) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO commissions (guild, title, note, images, status, created_at) "
            "VALUES (?,?,?,?, 'open', ?)",
            (guild, title, note, images, _now()),
        )
        c.commit()
        return int(cur.lastrowid)


def list_commissions(include_done: bool = True) -> list[sqlite3.Row]:
    with _lock:
        c = get_conn()
        if include_done:
            return c.execute(
                "SELECT * FROM commissions ORDER BY status='done', id DESC"
            ).fetchall()
        return c.execute(
            "SELECT * FROM commissions WHERE status='open' ORDER BY id DESC"
        ).fetchall()


def list_domains() -> list[str]:
    """既有「領域公會」名稱(distinct,非空),供前端 datalist 建議。"""
    with _lock:
        c = get_conn()
        rows = c.execute(
            "SELECT domain, COUNT(*) n FROM commissions "
            "WHERE domain IS NOT NULL AND domain != '' "
            "GROUP BY domain ORDER BY n DESC"
        ).fetchall()
        return [r["domain"] for r in rows]


def set_commission_status(cid: int, status: str) -> None:
    done_at = _now() if status == "done" else None
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE commissions SET status=?, done_at=? WHERE id=?",
            (status, done_at, cid),
        )
        c.commit()


def delete_commission(cid: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM commissions WHERE id=?", (cid,))
        c.commit()


# ── 進化系統 ─────────────────────────────────────────────

def update_report_card(report_id: int, card_str: str) -> None:
    """覆寫某回報的結果卡 JSON(供學習書架重新歸類 / 補延伸主題)。"""
    with _lock:
        c = get_conn()
        c.execute("UPDATE reports SET card=? WHERE id=?", (card_str, report_id))
        c.commit()


def reports_with_card() -> list[sqlite3.Row]:
    """所有帶結果卡的回報(新→舊),供學習書架 / 分類彙整。"""
    with _lock:
        try:
            return get_conn().execute(
                "SELECT * FROM reports WHERE card IS NOT NULL ORDER BY id DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def completed_reports(adventurer_id: str) -> list[sqlite3.Row]:
    """某冒險者已成功歸檔的回報(最新在前),供進化分析。"""
    with _lock:
        return get_conn().execute(
            """SELECT * FROM reports
               WHERE adventurer_id=? AND status='done'
               ORDER BY id DESC""",
            (adventurer_id,),
        ).fetchall()


def completed_count_map() -> dict[str, int]:
    """各冒險者已完成任務數(status=done 且有 adventurer_id)。"""
    with _lock:
        rows = get_conn().execute(
            """SELECT adventurer_id, COUNT(*) n FROM reports
               WHERE status='done' AND adventurer_id IS NOT NULL
               GROUP BY adventurer_id"""
        ).fetchall()
    return {r["adventurer_id"]: r["n"] for r in rows}


def list_evolutions(adventurer_id: str | None = None) -> list[sqlite3.Row]:
    with _lock:
        c = get_conn()
        if adventurer_id:
            return c.execute(
                "SELECT * FROM evolutions WHERE adventurer_id=? ORDER BY id DESC",
                (adventurer_id,),
            ).fetchall()
        return c.execute("SELECT * FROM evolutions ORDER BY id DESC").fetchall()


def evolutions_for_guild(guild: str | None) -> list[sqlite3.Row]:
    """某公會已習得的能力(進化紀錄,最新在前),供派遣時注入 prompt。

    含該公會下所有冒險者的進化,以及 distill(全公會提煉)登記在此公會的通則。
    """
    if not guild:
        return []
    with _lock:
        try:
            return get_conn().execute(
                "SELECT * FROM evolutions WHERE guild=? ORDER BY id DESC",
                (guild,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def evolution_count_map() -> dict[str, int]:
    with _lock:
        rows = get_conn().execute(
            """SELECT adventurer_id, COUNT(*) n FROM evolutions
               WHERE adventurer_id IS NOT NULL GROUP BY adventurer_id"""
        ).fetchall()
    return {r["adventurer_id"]: r["n"] for r in rows}


# ── 自訂能力(使用者手動編輯冒險者能力）─────────────────────

def add_ability(adventurer_id: str, name: str) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO abilities (adventurer_id, name, created_at) VALUES (?,?,?)",
            (adventurer_id, name, _now()),
        )
        c.commit()
        return int(cur.lastrowid)


def list_abilities(adventurer_id: str) -> list[sqlite3.Row]:
    with _lock:
        try:
            return get_conn().execute(
                "SELECT * FROM abilities WHERE adventurer_id=? ORDER BY id ASC",
                (adventurer_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def delete_ability(ability_id: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM abilities WHERE id=?", (ability_id,))
        c.commit()


# ── 冒險者身份覆蓋(手動取名 / 指定職業)────────────────────

def set_override(session_id: str, name: str | None, klass: str | None) -> None:
    """設定(或更新)某 session 的取名 / 職業。空字串一律存 None。"""
    with _lock:
        c = get_conn()
        c.execute(
            """INSERT INTO session_overrides (session_id, name, klass, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 name=excluded.name, klass=excluded.klass, updated_at=excluded.updated_at""",
            (session_id, (name or None), (klass or None), _now()),
        )
        c.commit()


def get_override(session_id: str) -> sqlite3.Row | None:
    with _lock:
        try:
            return get_conn().execute(
                "SELECT * FROM session_overrides WHERE session_id=?", (session_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None


def all_overrides() -> dict[str, dict]:
    """session_id → {name, klass}(供 scanner 一次載入套用)。"""
    with _lock:
        try:
            rows = get_conn().execute("SELECT * FROM session_overrides").fetchall()
        except sqlite3.OperationalError:
            return {}
    return {r["session_id"]: {"name": r["name"], "klass": r["klass"]} for r in rows}


# ── 派遣系統(委託 → headless 冒險者回合制執行)──────────────

def get_commission(cid: int) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM commissions WHERE id=?", (cid,)
        ).fetchone()


def next_queued() -> int | None:
    """佇列中最舊的一張待派遣委託(FIFO by id)。worker 逐一抽取。"""
    with _lock:
        row = get_conn().execute(
            "SELECT id FROM commissions WHERE status='queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
    return int(row["id"]) if row else None


def queued_ids() -> list[int]:
    """目前排隊中的委託 id(FIFO 順序),供前端顯示佇列位置。"""
    with _lock:
        rows = get_conn().execute(
            "SELECT id FROM commissions WHERE status='queued' ORDER BY id ASC"
        ).fetchall()
    return [int(r["id"]) for r in rows]


def reset_working_to_blocked() -> list[int]:
    """把殘留的 working 委託重設為 blocked。啟動時呼叫:後端重啟會殺掉記憶體裡的
    派遣任務,但 DB 列仍停在 working 而無人續跑 —— 轉 blocked 讓使用者可重新派遣。"""
    with _lock:
        c = get_conn()
        ids = [r["id"] for r in
               c.execute("SELECT id FROM commissions WHERE status='working'")]
        if ids:
            c.execute("UPDATE commissions SET status='blocked' WHERE status='working'")
            c.commit()
    return ids


# 可被 update_commission 動態更新的 commissions 欄位白名單。
# 欄位名會直接拼進 SQL 字串,必須擋掉名單外的 key 以杜絕 SQL 注入
# (呼叫端如 routers/commissions.py、muse.py 的 key 可能源自 HTTP 請求)。
# 內容對應 commissions 表的全部非主鍵欄位(見本檔 SCHEMA 與 migration)。
_COMMISSION_COLS = {
    "guild", "title", "note", "status", "created_at", "done_at", "klass",
    "goal", "cwd", "session_id", "rounds", "stop_requested", "model",
    "cost", "images", "domain", "self_folder", "memory", "task_memory",
}

# 任務記憶上限:只留最近這麼多字(舊的從頭截掉),避免續派 prompt 被灌爆
_TASK_MEMORY_MAX = 4000


def append_task_memory(cid: int, entry: str) -> None:
    """把一段成果/心得追加進委託自己的任務記憶(超過上限丟最舊的)。"""
    entry = (entry or "").strip()
    if not entry:
        return
    with _lock:
        c = get_conn()
        row = c.execute("SELECT memory FROM commissions WHERE id=?", (cid,)).fetchone()
        if row is None:
            return
        mem = ((row["memory"] or "") + "\n\n" + entry).strip()
        if len(mem) > _TASK_MEMORY_MAX:
            mem = "…" + mem[-_TASK_MEMORY_MAX:]
        c.execute("UPDATE commissions SET memory=? WHERE id=?", (mem, cid))
        c.commit()


def update_commission(cid: int, **fields) -> None:
    """更新委託任意欄位(goal / status / session_id / rounds / stop_requested…)。

    只接受 ``_COMMISSION_COLS`` 白名單內的欄位名;其餘一律拋 ``ValueError``。
    """
    if not fields:
        return
    bad = set(fields) - _COMMISSION_COLS
    if bad:
        raise ValueError(f"update_commission: 不允許的欄位名 {sorted(bad)}")
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        c = get_conn()
        c.execute(f"UPDATE commissions SET {cols} WHERE id=?",
                  (*fields.values(), cid))
        c.commit()


def add_event(cid: int, type_: str, content: str, round_: int | None = None) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO commission_events (commission_id, type, content, round, created_at) "
            "VALUES (?,?,?,?,?)",
            (cid, type_, content, round_, _now()),
        )
        c.commit()
        return int(cur.lastrowid)


def list_events(cid: int, limit: int = 100) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM commission_events WHERE commission_id=? "
            "ORDER BY id DESC LIMIT ?",
            (cid, limit),
        ).fetchall()


def take_steers(cid: int) -> list[str]:
    """取出尚未注入的使用者中途指示,並標記已消費(下一回合生效)。"""
    with _lock:
        c = get_conn()
        rows = c.execute(
            "SELECT id, content FROM commission_events "
            "WHERE commission_id=? AND type='steer' AND consumed=0 ORDER BY id ASC",
            (cid,),
        ).fetchall()
        if rows:
            c.executemany(
                "UPDATE commission_events SET consumed=1 WHERE id=?",
                [(r["id"],) for r in rows],
            )
            c.commit()
    return [r["content"] for r in rows]


# ── 進化提案(訓練所自動化:先提案、批准才落地)────────────

def add_proposal(
    adventurer_id: str | None, guild: str | None, route: str, name: str,
    detail: str | None, artifact_path: str | None, artifact_content: str | None,
) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            """INSERT INTO evolution_proposals
               (adventurer_id, guild, route, name, detail,
                artifact_path, artifact_content, status, created_at)
               VALUES (?,?,?,?,?,?,?, 'pending', ?)""",
            (adventurer_id, guild, route, name, detail,
             artifact_path, artifact_content, _now()),
        )
        c.commit()
        return int(cur.lastrowid)


def list_proposals(status: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    with _lock:
        c = get_conn()
        if status:
            return c.execute(
                "SELECT * FROM evolution_proposals WHERE status=? "
                "ORDER BY id DESC LIMIT ?", (status, limit),
            ).fetchall()
        return c.execute(
            "SELECT * FROM evolution_proposals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def get_proposal(pid: int) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM evolution_proposals WHERE id=?", (pid,)
        ).fetchone()


def decide_proposal(pid: int, status: str, note: str | None = None) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "UPDATE evolution_proposals SET status=?, decided_note=?, decided_at=? "
            "WHERE id=?",
            (status, note, _now(), pid),
        )
        c.commit()


# ── 成本帳本(每筆 headless 花費記帳;派遣累計進委託)──────────

def add_cost(
    source: str, cost: float, *,
    commission_id: int | None = None, adventurer_id: str | None = None,
    guild: str | None = None, model: str | None = None,
    detail: str | None = None,
) -> None:
    """記一筆花費。若屬某委託,同步累加進 commissions.cost(布告欄直接顯示)。"""
    if not cost:
        return
    with _lock:
        c = get_conn()
        c.execute(
            """INSERT INTO cost_ledger
               (source, commission_id, adventurer_id, guild, model, cost, detail, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (source, commission_id, adventurer_id, guild, model, cost, detail, _now()),
        )
        if commission_id is not None:
            c.execute(
                "UPDATE commissions SET cost=COALESCE(cost,0)+? WHERE id=?",
                (cost, commission_id),
            )
        c.commit()


def commission_cost(cid: int) -> float:
    with _lock:
        row = get_conn().execute(
            "SELECT cost FROM commissions WHERE id=?", (cid,)
        ).fetchone()
    return float(row["cost"] or 0) if row else 0.0


def cost_summary() -> dict:
    """月報表 + 各來源小計 + 總計,供成本面板顯示。"""
    with _lock:
        c = get_conn()
        try:
            by_month = c.execute(
                """SELECT substr(created_at,1,7) AS month, source,
                          SUM(cost) AS total, COUNT(*) AS n
                   FROM cost_ledger GROUP BY month, source
                   ORDER BY month DESC, source"""
            ).fetchall()
            total = c.execute("SELECT COALESCE(SUM(cost),0) t FROM cost_ledger").fetchone()["t"]
        except sqlite3.OperationalError:
            return {"total": 0.0, "months": []}
    months: dict[str, dict] = {}
    for r in by_month:
        m = months.setdefault(r["month"], {"month": r["month"], "total": 0.0, "sources": {}})
        m["sources"][r["source"]] = {"cost": round(r["total"] or 0, 4), "n": r["n"]}
        m["total"] = round(m["total"] + (r["total"] or 0), 4)
    return {"total": round(total or 0, 4), "months": list(months.values())}


def spent_since(iso_ts: str) -> float:
    """自某 UTC ISO 時間點以來的總花費(供夜巡每日預算護欄)。"""
    with _lock:
        try:
            row = get_conn().execute(
                "SELECT COALESCE(SUM(cost),0) t FROM cost_ledger WHERE created_at >= ?",
                (iso_ts,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0.0
    return float(row["t"] or 0)


# ── 通用設定(鍵值)──────────────────────────────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    with _lock:
        row = get_conn().execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        c.commit()


# ── 公會夜巡靈感卡 ───────────────────────────────────────────────

def add_idea(guild: str | None, cwd: str | None, kind: str,
             title: str, body: str | None, effort: str | None,
             auto_safe: bool | None = None) -> int:
    safe = None if auto_safe is None else (1 if auto_safe else 0)
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO muse_ideas (guild, cwd, kind, title, body, effort, "
            "auto_safe, status, created_at) VALUES (?,?,?,?,?,?,?, 'new', ?)",
            (guild, cwd, kind, title, body, effort, safe, _now()),
        )
        c.commit()
        return int(cur.lastrowid)


def new_ideas() -> list[sqlite3.Row]:
    """所有待處理(new)靈感,舊到新(供自動執行從既有清單挑)。"""
    with _lock:
        return get_conn().execute(
            "SELECT * FROM muse_ideas WHERE status='new' ORDER BY id ASC"
        ).fetchall()


def list_ideas(limit: int = 60) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM muse_ideas ORDER BY status='dismissed', "
            "status!='new', id DESC LIMIT ?", (limit,),
        ).fetchall()


def get_idea(iid: int) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM muse_ideas WHERE id=?", (iid,)
        ).fetchone()


def set_idea_status(iid: int, status: str) -> None:
    with _lock:
        c = get_conn()
        c.execute("UPDATE muse_ideas SET status=? WHERE id=?", (status, iid))
        c.commit()


def delete_idea(iid: int) -> None:
    with _lock:
        c = get_conn()
        c.execute("DELETE FROM muse_ideas WHERE id=?", (iid,))
        c.commit()


def idea_titles_for_guild(guild: str | None, limit: int = 60) -> list[str]:
    """該公會現有(未 dismissed)靈感的標題 —— 餵進夜巡 prompt,叫它別重複產同一則。"""
    with _lock:
        rows = get_conn().execute(
            "SELECT title FROM muse_ideas WHERE guild IS ? AND status!='dismissed' "
            "ORDER BY id DESC LIMIT ?", (guild, limit),
        ).fetchall()
    return [r["title"] for r in rows if r["title"]]


def idea_title_exists(guild: str | None, title: str) -> bool:
    """同公會是否已有同標題的靈感(去重,避免夜巡反覆產同一則)。"""
    with _lock:
        row = get_conn().execute(
            "SELECT 1 FROM muse_ideas WHERE guild IS ? AND title=? LIMIT 1",
            (guild, title),
        ).fetchone()
    return row is not None


# ── 學習迴路:敘事偏好(櫃台)/ 做事方式偏好(冒險者)──────────────

def add_reception_pref(note: str | None, guild: str | None = None) -> None:
    """記一條櫃台敘事偏好(來自使用者退回報告時的說明)。空白則略過。"""
    if not note or not note.strip():
        return
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO reception_prefs (guild, note, created_at) VALUES (?,?,?)",
            (guild, note.strip()[:500], _now()),
        )
        c.commit()


def list_reception_prefs(limit: int = 8) -> list[sqlite3.Row]:
    """最近的櫃台敘事偏好(新→舊),供合成講評 prompt 注入。"""
    with _lock:
        try:
            return get_conn().execute(
                "SELECT * FROM reception_prefs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def add_work_pref(note: str | None, guild: str | None = None,
                  adventurer_id: str | None = None) -> None:
    """記一條做事方式偏好(來自使用者叫冒險者繼續做時的指示)。空白則略過。"""
    if not note or not note.strip():
        return
    with _lock:
        c = get_conn()
        c.execute(
            "INSERT INTO work_prefs (guild, adventurer_id, note, created_at) "
            "VALUES (?,?,?,?)",
            (guild, adventurer_id, note.strip()[:500], _now()),
        )
        c.commit()


def list_work_prefs(guild: str | None = None, limit: int = 8) -> list[sqlite3.Row]:
    """做事方式偏好(新→舊)。給了 guild 就只取該公會的,供派遣 prompt 注入。"""
    with _lock:
        try:
            c = get_conn()
            if guild:
                return c.execute(
                    "SELECT * FROM work_prefs WHERE guild=? ORDER BY id DESC LIMIT ?",
                    (guild, limit),
                ).fetchall()
            return c.execute(
                "SELECT * FROM work_prefs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def add_evolution(
    adventurer_id: str | None, guild: str | None, route: str,
    name: str, detail: str | None, based_on: int,
) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            """INSERT INTO evolutions
               (adventurer_id, guild, route, name, detail, based_on, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (adventurer_id, guild, route, name, detail, based_on, _now()),
        )
        c.commit()
        return int(cur.lastrowid)
