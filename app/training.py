"""訓練所自動化 —— 面板一鍵把冒險者送進訓練所,headless Claude 產「進化提案」。

設計原則(承 ROADMAP「進化要有人把關」):
1. **分析是唯讀的**:headless Claude 只給 Read/Glob/Grep,看得到既有 skills 與
   memory(避免重複造輪子),但**不能寫任何檔**;產出是一份 JSON 提案。
2. **落地要批准**:提案存進 evolution_proposals,面板上批准後才由後端把
   artifact 寫到白名單路徑(~/.claude、projects_dir、wiki 根目錄,見 config.yaml),並登記進化。
3. 兩種模式:
   - `propose(adventurer_id)`:單一冒險者,依其解決過的任務擇一路線(訓練所 skill 的四路線)。
   - `distill()`:全公會提煉 —— 掃全部已完成回報的心得,依 llm-wiki-manager 的
     Prune 精神「去雜訊、提取通則」,把跨任務反覆出現的模式凝成一項能力。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import config, db

log = logging.getLogger("guild.training")

CLAUDE = shutil.which("claude") or "claude"
MODEL = config.get("GUILD_TRAIN_MODEL", "claude-sonnet-4-6")
TIMEOUT = int(config.get("GUILD_TRAIN_TIMEOUT", "300"))
MAX_TURNS = "12"

SKILLS_DIR = Path.home() / ".claude" / "skills"
# memory 路線的落地目錄(預設 ~/.claude/memory;通常設成某個專案的 Claude 記憶目錄)
MEMORY_DIR = Path(config.get("GUILD_MEMORY_DIR", str(Path.home() / ".claude" / "memory")))
# script 路線的落地根目錄:<projects_dir>/<公會>/scripts/
PROJECTS_DIR = Path(config.get("GUILD_PROJECTS_DIR", str(Path.home() / "projects")))
_WIKI = config.get("GUILD_WIKI_DIR", "").strip()
# 批准後允許寫入的根目錄(路徑白名單):~/.claude、專案根、wiki 所在根,加上自訂額外根目錄
ALLOWED_ROOTS = [
    Path.home() / ".claude",
    MEMORY_DIR,
    PROJECTS_DIR,
    *([Path(_WIKI).parent] if _WIKI else []),
    *(Path(p) for p in config.get_list("GUILD_EXTRA_WRITE_ROOTS")),
]

PROPOSE_PROMPT = """你是冒險者公會的訓練所教官。一位冒險者累積了足夠戰功,請依他「實際解決過的問題」\
提煉**一項**最值得固化的能力,輸出一份進化提案(只提案,不落地;使用者批准後由系統寫檔)。

## 冒險者素材
- 公會(專案):{guild}
- 已完成任務數:{completed}
- 近期解決的任務:
{titles}
- 近期心得/教訓:
{lessons}
- 使用者反覆給的做事指示(最直接的「使用者想要的做事方式」訊號,優先考慮固化成 skill/memory):
{work_prefs}

## 你可以先偵察(唯讀)
- `~/.claude/skills/` 下已有哪些 skill(避免重複;若已有相近 skill,提案改良它)
- `~/.claude/projects/D--/memory/MEMORY.md` 已記了什麼

## 四條路線(擇一)
- `memory`:反覆的通用原則/偏好 → artifact 是一個 memory 檔(含 frontmatter:name/description/metadata.type)
- `skill`:反覆的多步驟流程 → artifact 是 SKILL.md(frontmatter 必含 name 與 description,\
description 要寫清楚觸發時機;內文給步驟與範例,精簡實用)
- `tool`:現有工具太慢/太手動 → artifact_content 留空,detail 寫建議採用的工具與方式
- `script`:可自動化的重複動作 → artifact 是一支腳本,路徑放在該專案 scripts/ 下

## 輸出(嚴格)
只輸出一個 JSON 物件,不要其他文字:
{{
  "route": "memory|skill|tool|script",
  "name": "<能力/skill 名稱,kebab-case 或中文皆可>",
  "detail": "<為什麼值得固化:哪些任務反覆出現這個模式(2-3 句)>",
  "artifact_path": "<批准後要寫入的絕對路徑;tool 路線可為空字串>",
  "artifact_content": "<檔案完整內容;tool 路線可為空字串>"
}}
若素材太薄、沒有值得固化的模式,輸出 {{"route": "skip", "detail": "<原因>"}}。
"""

DISTILL_PROMPT = """你是冒險者公會的訓練所教官,現在執行「全公會技能提煉」(海馬迴夜間修剪)。\
以下是全公會近期已完成任務的回報摘要。依 llm-wiki-manager 的 Prune 準則:\
**去雜訊(忽略一次性的細節與失敗嘗試)、提取通則(找跨任務反覆出現的模式)**,\
凝成**一項**最有價值的通用能力提案。

## 全公會近期回報
{reports}

## 你可以先偵察(唯讀)
- `~/.claude/skills/` 既有 skill(若通則已被某 skill 涵蓋,提案改良它而非新建)
- `~/.claude/projects/D--/memory/MEMORY.md`

## 輸出(嚴格)
與單人訓練相同的 JSON 格式(route/name/detail/artifact_path/artifact_content),\
route 從 memory/skill/tool/script 擇一;沒有值得提煉的通則就輸出 \
{{"route": "skip", "detail": "<原因>"}}。只輸出 JSON,不要其他文字。
"""


def available() -> bool:
    return shutil.which("claude") is not None


def _run_claude(prompt: str) -> dict:
    """唯讀分析:只給 Read/Glob/Grep。回傳 {ok, text/error}。阻塞,於執行緒呼叫。"""
    # prompt 走 stdin:Windows claude.CMD 會把含換行的 argv 參數打壞(旗標失效)
    cmd = [
        CLAUDE, "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--max-turns", MAX_TURNS,
        "--allowedTools", "Read,Glob,Grep",
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(Path.home()), capture_output=True,
                              input=prompt.encode("utf-8"), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"訓練逾時({TIMEOUT}s)"}
    except OSError as exc:
        return {"ok": False, "error": f"無法啟動 claude:{exc}"}
    if proc.returncode != 0:
        msg = (proc.stderr.decode("utf-8", "replace") or "").strip()[:400]
        return {"ok": False, "error": msg or f"claude 結束碼 {proc.returncode}"}
    raw = proc.stdout.decode("utf-8", "replace").strip()
    cost = None
    try:
        data = json.loads(raw)
        text = (data.get("result") or data.get("text") or raw).strip()
        cost = data.get("total_cost_usd")
    except json.JSONDecodeError:
        text = raw
    return {"ok": True, "text": text, "cost": cost}


def _extract_json(text: str) -> dict | None:
    """從模型輸出撈出第一個完整 JSON 物件(模型偶爾會包 ```json 或夾雜說明文字)。

    不用貪心 `\\{.*\\}`:那會從第一個 `{` 吃到最後一個 `}`,一旦輸出裡有多個
    JSON 片段或前後說明文字,就會抓到整段非法字串而靜默丟棄結果。改用
    `raw_decode()` 從每個 `{` 起點嘗試解析,回傳第一個能成功解析的 JSON 物件。
    """
    decoder = json.JSONDecoder()
    for idx in range(len(text)):
        if text[idx] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _default_artifact_path(route: str, name: str, guild: str | None) -> str:
    safe = re.sub(r'[<>:"/\\|?*\s]+', "-", name).strip("-") or "unnamed"
    if route == "skill":
        return str(SKILLS_DIR / safe / "SKILL.md")
    if route == "memory":
        return str(MEMORY_DIR / f"evolved_{safe}.md")
    if route == "script":
        return str(PROJECTS_DIR / (guild or "guild-hall") / "scripts" / safe)
    return ""


def propose_for(adventurer_id: str) -> dict:
    """單一冒險者訓練:分析素材 → 產一筆 pending 提案。回傳 {ok, proposal_id/error}。"""
    reports = db.completed_reports(adventurer_id)
    if not reports:
        return {"ok": False, "error": "這位冒險者沒有已完成的回報,無素材可訓練"}
    guild = reports[0]["guild"]
    titles = "\n".join(f"  - {r['title']}" for r in reports[:8] if r["title"])
    lessons = "\n".join(f"  - {r['lessons']}" for r in reports[:8] if r["lessons"]) \
        or "  (無)"
    work_prefs = "\n".join(
        f"  - {w['note']}" for w in db.list_work_prefs(guild, 10) if w["note"]) \
        or "  (無)"
    prompt = PROPOSE_PROMPT.format(
        guild=guild or "未知公會", completed=len(reports),
        titles=titles or "  (無)", lessons=lessons, work_prefs=work_prefs)
    return _propose(prompt, adventurer_id, guild)


def distill() -> dict:
    """全公會提煉:掃全部 done 回報 → 產一筆 pending 提案。"""
    rows = db.list_reports(limit=40)
    done = [r for r in rows if r["status"] == "done"]
    if len(done) < 3:
        return {"ok": False, "error": "已完成回報不足 3 筆,還不夠提煉通則"}
    blob = "\n".join(
        f"- [{r['guild'] or '?'}] {r['title']}"
        + (f" — 心得:{r['lessons']}" if r["lessons"] else "")
        for r in done[:30]
    )
    return _propose(DISTILL_PROMPT.format(reports=blob), None, None)


def _propose(prompt: str, adventurer_id: str | None, guild: str | None) -> dict:
    res = _run_claude(prompt)
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    if res.get("cost"):
        db.add_cost("training", float(res["cost"]), adventurer_id=adventurer_id,
                    guild=guild, model=MODEL,
                    detail="全公會提煉" if adventurer_id is None else "訓練分析")
    data = _extract_json(res["text"])
    if data is None:
        return {"ok": False, "error": f"提案解析失敗:{res['text'][:200]}"}
    route = (data.get("route") or "").strip()
    if route == "skip":
        return {"ok": False, "error": f"教官評估暫不進化:{data.get('detail', '素材不足')}"}
    if route not in ("memory", "skill", "tool", "script"):
        return {"ok": False, "error": f"未知路線:{route}"}
    name = (data.get("name") or "未命名能力").strip()
    path = (data.get("artifact_path") or "").strip() or \
        _default_artifact_path(route, name, guild)
    pid = db.add_proposal(
        adventurer_id=adventurer_id, guild=guild, route=route, name=name,
        detail=(data.get("detail") or "").strip() or None,
        artifact_path=path or None,
        artifact_content=(data.get("artifact_content") or "").strip() or None,
    )
    log.info("進化提案 #%s: route=%s name=%s", pid, route, name)
    return {"ok": True, "proposal_id": pid}


def _path_allowed(p: Path) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    return any(rp.is_relative_to(root) for root in ALLOWED_ROOTS)


def approve(pid: int) -> dict:
    """批准提案:寫 artifact(白名單路徑)+ 登記進化。回傳 {ok, detail/error}。"""
    row = db.get_proposal(pid)
    if row is None:
        return {"ok": False, "error": "找不到這筆提案"}
    if row["status"] != "pending":
        return {"ok": False, "error": f"提案已是 {row['status']},不能重複裁決"}

    route, name = row["route"], row["name"]
    written = "(無檔案產出)"
    if row["artifact_content"] and row["artifact_path"]:
        path = Path(row["artifact_path"])
        if not _path_allowed(path):
            return {"ok": False, "error": f"路徑不在白名單內:{path}"}
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():  # 不靜默覆蓋:留 .bak 可救回
            path.with_suffix(path.suffix + ".bak").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(row["artifact_content"], encoding="utf-8")
        written = str(path)
        # memory 路線:同步補 MEMORY.md 索引(索引只放一行指標)
        if route == "memory" and path.parent == MEMORY_DIR:
            idx = MEMORY_DIR / "MEMORY.md"
            if idx.exists():
                hook = (row["detail"] or name)[:40]
                with idx.open("a", encoding="utf-8") as fh:
                    fh.write(f"- [{name}]({path.name}) — {hook}(訓練所進化 "
                             f"{datetime.now():%Y-%m-%d})\n")

    db.add_evolution(
        adventurer_id=row["adventurer_id"], guild=row["guild"], route=route,
        name=name, detail=f"{row['detail'] or ''} → {written}".strip(" →"),
        based_on=db.completed_count_map().get(row["adventurer_id"] or "", 0),
    )
    db.decide_proposal(pid, "approved", written)
    return {"ok": True, "detail": written}


def reject(pid: int, note: str | None = None) -> dict:
    row = db.get_proposal(pid)
    if row is None:
        return {"ok": False, "error": "找不到這筆提案"}
    db.decide_proposal(pid, "rejected", note)
    return {"ok": True}
