"""回報潤稿 —— 把使用者潦草的草稿(常夾帶對冒險者產出的批評)整理成通順回報。

定位:降低「自己寫回報」的門檻 —— 你只要想到什麼寫什麼、順手挑毛病,
按一顆按鈕讓便宜模型幫你整理成結構清楚的回報,忠實保留你的批評。

護欄:純文字轉換,唯讀(plan 模式)、不動任何檔案、便宜模型、逾時保護。永不 raise。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

from . import config

log = logging.getLogger("guild.polish")

CLAUDE = shutil.which("claude") or "claude"
MODEL = config.get("GUILD_POLISH_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = int(config.get("GUILD_POLISH_TIMEOUT", "120"))  # 秒

PROMPT = """你是公會櫃台的文書助手。使用者寫了一份草稿結案回報,可能潦草、條列不整,\
也可能夾帶對冒險者(AI)產出的批評(例如「code 細節寫太少」「錯誤處理沒寫」)。\
請把它整理成一份通順、結構清楚的回報,用繁體中文。原則:
- 忠實保留使用者的原意與批評,**不要杜撰**草稿沒提到的事實。
- 若草稿含批評,務必在 summary 明確保留成一句「⚠️ 使用者指出:…」。
- 不要動任何檔案、不要反問。

## 草稿
標題:{title}
內容:{summary}
心得:{lessons}

## 只輸出一段 JSON(不要有任何其他文字)
{{"title": "一句話成果(20 字內)", "summary": "整理後的成果敘述(可條列),保留使用者批評", "lessons": "值得記住的心得,沒有就空字串"}}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def available() -> bool:
    return shutil.which("claude") is not None


def _extract_json(text: str) -> dict | None:
    m = _JSON_RE.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def polish(title: str, summary: str | None, lessons: str | None) -> dict:
    """回傳 {ok, title, summary, lessons, cost} 或 {ok:False, error}。永不 raise。"""
    if not available():
        return {"ok": False, "error": "找不到 claude CLI(請確認已安裝且在 PATH)"}
    prompt = PROMPT.format(
        title=(title or "(無)"),
        summary=(summary or "(無)"),
        lessons=(lessons or "(無)"),
    )
    # prompt 走 stdin:Windows claude.CMD 會把含換行的 argv 參數打壞(旗標失效)
    cmd = [CLAUDE, "-p", "--output-format", "json", "--model", MODEL,
           "--max-turns", "2", "--permission-mode", "plan"]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              input=prompt.encode("utf-8"), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"潤稿逾時({TIMEOUT}s)"}
    except OSError as exc:
        return {"ok": False, "error": f"無法啟動 claude:{exc}"}

    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:300]
        return {"ok": False, "error": msg or f"claude 結束碼 {proc.returncode}"}

    raw = proc.stdout.decode("utf-8", "replace").strip()
    text, cost = raw, None
    try:
        data = json.loads(raw)
        text = (data.get("result") or data.get("text") or raw).strip()
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        pass

    obj = _extract_json(text)
    if not obj:
        return {"ok": False, "error": "潤稿結果無法解析,請重試", "cost": cost}
    return {
        "ok": True,
        "title": (obj.get("title") or title or "").strip()[:80],
        "summary": (obj.get("summary") or summary or "").strip(),
        "lessons": (obj.get("lessons") or "").strip() or None,
        "cost": cost,
    }
