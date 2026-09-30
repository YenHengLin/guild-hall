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
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(os.environ.get(
    "GUILD_ACTIVITY_LOG", str(Path.home() / ".claude" / "guild-activity.jsonl")))
ARCHIVE_DIR = LOG.parent / "guild-activity.archive"
MAX_LINES = 800  # 超過就把舊的搬去歸檔,主檔不無限長

# 跨進程鎖:等待上限刻意設短 —— 寧可偶爾放棄 trim,也絕不阻塞 Claude 的 PostToolUse。
LOCK_TIMEOUT = 2.0    # 取鎖總等待上限(秒),逾時就放棄,不無限等
LOCK_POLL = 0.02      # 自旋重試間隔(秒)

# 依平台選鎖原語:Linux 用 fcntl.flock,Windows 用 msvcrt.locking。
# 兩者都用「非阻塞」模式,由 _log_lock() 自行控制重試與逾時。
try:
    import fcntl  # POSIX

    def _try_lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
except ImportError:
    import msvcrt  # Windows

    def _try_lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _log_lock(timeout=LOCK_TIMEOUT):
    """Best-effort 跨進程排他鎖,取得回傳 True、逾時回傳 False。

    刻意鎖「獨立的 .lock 檔」而非 LOG 本身 —— 這樣 _trim() 內部的 read_text /
    write_text(會各自開新 handle)不會與鎖用的 handle 打架(Windows msvcrt 是強制鎖,
    鎖住 LOG 再改寫 LOG 會失敗)。

    設計上絕不阻塞 Claude:非阻塞取鎖 + LOCK_TIMEOUT 上限自旋,逾時就放棄;
    鎖由 OS 在 fd 關閉 / 行程結束時自動釋放,不會殘留死鎖。任何錯誤都當成「沒取到」。
    """
    lock_path = LOG.parent / (LOG.name + ".lock")
    fh = None
    acquired = False
    try:
        try:
            fh = lock_path.open("a+")
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _try_lock(fh)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(LOCK_POLL)
        except Exception:
            acquired = False
        yield acquired
    finally:
        if fh is not None:
            if acquired:
                try:
                    _unlock(fh)
                except Exception:
                    pass
            try:
                fh.close()
            except Exception:
                pass


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

    這裡是唯一的「讀-改-寫」序列 —— 多個 session 同時 PostToolUse 時,
    Process A 的 read 與 Process B 的 write 會互相覆蓋而靜默丟行。因此整段
    read→trim→write 用 _log_lock() 保護。注意:
      • append(main() 內)刻意「不」進鎖 —— 單行 append 在 O_APPEND 下本就原子,
        且那是「絕不能擋住 Claude」的關鍵路徑,碰鎖只會多一份風險。
      • 只有這個非關鍵的安全網才取鎖;取不到鎖(逾時)就直接放棄本次 trim,
        由下一次 hook 再修剪 —— 寧可主檔多撐一會,也絕不阻塞或死鎖。
    """
    try:
        # 先廉價探測是否可能需要 trim,避免每次都去搶鎖(絕大多數呼叫不需要 trim)。
        if LOG.stat().st_size < (MAX_LINES + 200) * 40:
            return
    except Exception:
        pass
    with _log_lock() as locked:
        if not locked:
            return  # 沒搶到鎖:別人正在 trim 或系統忙,放棄本次,交給下次
        try:
            lines = LOG.read_text(encoding="utf-8").splitlines()
            if len(lines) > MAX_LINES + 200:
                # 兩側都濾掉空行:空行不是合法 JSONL 紀錄,保留只會讓輪替後格式損壞
                kept = [l for l in lines[-MAX_LINES:] if l.strip()]
                _archive([l for l in lines[:-MAX_LINES] if l.strip()])
                LOG.write_text("\n".join(kept) + "\n", encoding="utf-8")
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
