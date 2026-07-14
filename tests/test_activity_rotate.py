"""activity_log.rotate 的輪替 / 歸檔驗收 —— 確保零資料遺失、舊資料按月歸檔、歸檔可讀。"""
import importlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone


def _line(now, days_ago, i):
    ts = (now - timedelta(days=days_ago)).isoformat()
    return json.dumps(
        {"ts": ts, "session_id": "s", "cwd": r"D:\projects\x",
         "tool": "Bash", "summary": f"act {i}"},
        ensure_ascii=False,
    )


def test_rotate_lossless_monthly_archive():
    tmp = tempfile.mkdtemp()
    logp = os.path.join(tmp, "guild-activity.jsonl")
    os.environ["GUILD_ACTIVITY_LOG"] = logp
    os.environ["GUILD_ACTIVITY_MAX_BYTES"] = "500"   # 很小,強制觸發輪替
    os.environ["GUILD_ACTIVITY_KEEP_DAYS"] = "7"

    import app.activity_log as al
    importlib.reload(al)

    now = datetime.now(timezone.utc)
    old_lines = [_line(now, 40, i) for i in range(30)]   # 舊 → 應歸檔
    recent = [_line(now, 1, i) for i in range(30)]       # 近期 → 應保留
    with open(logp, "w", encoding="utf-8") as f:
        f.write("\n".join(old_lines + recent) + "\n")

    before = len([l for l in open(logp, encoding="utf-8").read().splitlines() if l.strip()])
    stats = al.rotate()

    kept = [l for l in open(logp, encoding="utf-8").read().splitlines() if l.strip()]
    arch_files = os.listdir(al.ARCHIVE_DIR)
    arch_lines = []
    for fn in arch_files:
        p = os.path.join(al.ARCHIVE_DIR, fn)
        arch_lines += [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]

    assert stats["rotated"] is True
    assert len(kept) == 30, f"kept 應為 30 近期,得到 {len(kept)}"
    assert len(arch_lines) == 30, f"歸檔應為 30 舊行,得到 {len(arch_lines)}"
    assert len(kept) + len(arch_lines) == before, "零資料遺失"
    for l in arch_lines:
        json.loads(l)  # 歸檔每行都是合法 JSON
    assert os.path.getsize(logp) > 0
    print(f"OK stats={stats} arch_files={arch_files}")


if __name__ == "__main__":
    test_rotate_lossless_monthly_archive()
    print("ALL ASSERTIONS PASSED")
