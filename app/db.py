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

_lock = threading.Lock()
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
    processed_at  TEXT
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
    done_at     TEXT
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
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
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
        ]:
            if col not in ccols:
                c.execute(ddl)
        c.commit()


def enqueue(
    adventurer_id: str | None,
    guild: str | None,
    title: str,
    summary: str | None,
    files_changed: str,
    lessons: str | None,
    outcome: str,
) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            """INSERT INTO reports
               (adventurer_id, guild, title, summary, files_changed, lessons,
                outcome, status, created_at)
               VALUES (?,?,?,?,?,?,?, 'pending', ?)""",
            (adventurer_id, guild, title, summary, files_changed, lessons,
             outcome, _now()),
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

def add_commission(guild: str | None, title: str, note: str | None) -> int:
    with _lock:
        c = get_conn()
        cur = c.execute(
            "INSERT INTO commissions (guild, title, note, status, created_at) "
            "VALUES (?,?,?, 'open', ?)",
            (guild, title, note, _now()),
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


# ── 派遣系統(委託 → headless 冒險者回合制執行)──────────────

def get_commission(cid: int) -> sqlite3.Row | None:
    with _lock:
        return get_conn().execute(
            "SELECT * FROM commissions WHERE id=?", (cid,)
        ).fetchone()


def update_commission(cid: int, **fields) -> None:
    """更新委託任意欄位(goal / status / session_id / rounds / stop_requested…)。"""
    if not fields:
        return
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
