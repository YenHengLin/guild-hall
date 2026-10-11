"""學習書架整理員 —— 給一份教學卡定「學習分類」+ 補上延伸主題。

用途:舊教學卡(加分類/延伸功能前生的)歸在「未分類」、也沒有延伸主題。
這支用便宜模型幫它重新歸類(沿用現有分類,相似的併同類)並產生可延續學習的主題。

護欄:純文字轉換,唯讀(plan 模式)、不動任何檔案、便宜模型、逾時保護。永不 raise。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

from . import config

log = logging.getLogger("guild.classify")

CLAUDE = shutil.which("claude") or "claude"
MODEL = config.get("GUILD_CLASSIFY_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT = int(config.get("GUILD_CLASSIFY_TIMEOUT", "120"))

PROMPT = """你是學習書架的整理員。以下是一份教學卡,請做兩件事:
1. category:把它歸到一個「廣義學習分類」(例如「財經學習」「程式學習」「語言學習」)。{cats_hint}
2. next:給 2-3 個「可以再深入學的主題」,讓使用者一鍵繼續往下學。
用繁體中文,只輸出一行 JSON,不要有任何其他文字:
{{"category":"<學習分類>","next":["<延伸主題1>","<延伸主題2>"]}}

## 教學卡
標題:{headline}
涵蓋的問題:
{fronts}
"""

_JSON_DECODER = json.JSONDecoder()


def _extract_obj(text: str) -> dict | None:
    """撈第一個能解析的 JSON 物件。容忍 ```json 包裹、以及物件前夾帶含 '{' 的
    雜訊——用 raw_decode 對每個候選起點嘗試,雜訊會被跳過而非像貪婪 `\\{.*\\}`
    那樣從第一個 '{' 吞到最後一個 '}' 整段解析失敗。找不到回 None。"""
    candidates = [m.group(1).strip()
                  for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)]
    candidates += [text[i:] for i, ch in enumerate(text) if ch == "{"]
    for c in candidates:
        try:
            obj, _ = _JSON_DECODER.raw_decode(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def available() -> bool:
    return shutil.which("claude") is not None


def classify(headline: str | None, fronts: list[str], categories: list[str]) -> dict:
    """回傳 {ok, category, next, cost} 或 {ok:False, error}。永不 raise。"""
    if not available():
        return {"ok": False, "error": "找不到 claude CLI"}
    cats_hint = ("\n   現有分類:" + "、".join(categories)
                 + "。若屬於其中一類就用該類原本的名字(別自創同義詞);不屬於才新增。"
                 ) if categories else ""
    prompt = PROMPT.format(
        cats_hint=cats_hint,
        headline=(headline or "(無)"),
        fronts=("\n".join(f"- {f}" for f in (fronts or [])[:8]) or "(無)"),
    )
    cmd = [CLAUDE, "-p", "--output-format", "json", "--model", MODEL,
           "--max-turns", "2", "--permission-mode", "plan"]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              input=prompt.encode("utf-8"), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"逾時({TIMEOUT}s)"}
    except OSError as exc:
        return {"ok": False, "error": f"無法啟動 claude:{exc}"}
    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:200]
        return {"ok": False, "error": msg or f"claude 結束碼 {proc.returncode}"}

    raw = proc.stdout.decode("utf-8", "replace").strip()
    text, cost = raw, None
    try:
        d = json.loads(raw)
        text = (d.get("result") or d.get("text") or raw).strip()
        cost = d.get("total_cost_usd")
    except json.JSONDecodeError:
        pass

    obj = _extract_obj(text)
    if obj is None:
        return {"ok": False, "error": "無法解析整理結果", "cost": cost}

    nxt = obj.get("next")
    clean_next = ([str(t).strip()[:40] for t in nxt if isinstance(t, str) and t.strip()][:3]
                  if isinstance(nxt, list) else [])
    return {
        "ok": True,
        "category": str(obj.get("category") or "").strip()[:20],
        "next": clean_next,
        "cost": cost,
    }
