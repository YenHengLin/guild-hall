"""Claude Code PostToolUse hook —— 把每次工具呼叫寫成一行白話紀錄。

給冒險者公會面板讀取,做「零 token 即時觀測」:面板顯示每個 session 當下在做什麼,
完全不經過模型 → 不花任何 token。

掛法(settings.json):
  "hooks": { "PostToolUse": [ { "matcher": "*", "hooks": [
    { "type": "command", "command": "python D:\\\\projects\\\\guild-hall\\\\hooks\\\\activity_logger.py" }
  ] } ] }

原則:必須快、必須永不擋住 Claude → 任何錯誤都吞掉並 exit 0。
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(os.environ.get(
    "GUILD_ACTIVITY_LOG", str(Path.home() / ".claude" / "guild-activity.jsonl")))
ARCHIVE_DIR = LOG.parent / "guild-activity.archive"
MAX_LINES = 800  # 超過就把舊的搬去歸檔,主檔不無限長


def _basename(p):
    try:
        return Path(str(p)).name or str(p)
    except Exception:
        return str(p)


def summarize(tool, ti):
    """把工具 + 參數 濃縮成一句白話。"""
    ti = ti or {}
    try:
        if tool in ("Edit", "Write", "NotebookEdit", "Read"):
            return _basename(ti.get("file_path") or ti.get("notebook_path") or "")
        if tool in ("Bash", "PowerShell"):
            # Bash 的 description 欄是現成的白話說明,最好用
            desc = ti.get("description")
            if desc:
                return desc[:80]
            return (ti.get("command") or "")[:80]
        if tool in ("Grep", "Glob"):
            return ti.get("pattern") or ti.get("query") or ""
        if tool == "Skill":
            return ti.get("skill") or ti.get("command") or ""
        if tool in ("Task", "Agent"):
            return ti.get("description") or ti.get("subagent_type") or ""
        if tool in ("WebSearch",):
            return (ti.get("query") or "")[:80]
        if tool == "WebFetch":
            return ti.get("url") or ""
        if tool in ("TaskCreate", "TaskUpdate"):
            return ti.get("description") or ti.get("prompt", "")[:60]
    except Exception:
        pass
    return ""


def main():
    try:
        # 一定要以 UTF-8 讀 stdin —— Windows 預設 cp950 會把中文與 JSON 讀壞
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return

    tool = data.get("tool_name") or "?"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": data.get("session_id"),
        "cwd": data.get("cwd"),
        "tool": tool,
        "summary": summarize(tool, data.get("tool_input")),
    }

    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim()
    except Exception:
        pass


def _trim():
    """檔案太長時只保留最後 MAX_LINES 行,被裁掉的舊行搬去歸檔(不丟棄,存檔是保底)。

    後端啟動時會做更完整的按大小 / 按天輪替;這裡只是 hook 端的安全網,
    避免後端長時間沒開時主檔無限膨脹。
    """
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES + 200:
            _archive([l for l in lines[:-MAX_LINES] if l.strip()])
            LOG.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _archive(dropped):
    """把被裁掉的行按 ts 月份 append 到 guild-activity.archive/<YYYY-MM>.jsonl。"""
    if not dropped:
        return
    buckets = {}
    for l in dropped:
        try:
            month = datetime.fromisoformat(
                json.loads(l)["ts"].replace("Z", "+00:00")).strftime("%Y-%m")
        except Exception:
            month = "unknown"
        buckets.setdefault(month, []).append(l)
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for month, ls in buckets.items():
            with (ARCHIVE_DIR / f"{month}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("\n".join(ls) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
