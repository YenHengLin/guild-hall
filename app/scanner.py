"""核心掃描器 —— 把 ~/.claude/projects 的 session jsonl 轉成公會 / 冒險者模型。

MVP1 為唯讀:只讀取 Claude Code 已寫下的 jsonl,不需要 hooks、不改任何設定。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import cache, db
from .models import (
    Achievement,
    Adventurer,
    AdventurerDetail,
    DayCount,
    Deeds,
    Guild,
    GuildActivity,
    LogEntry,
    Persona,
    Quest,
    Report,
    SkillStat,
    TodayStat,
)

log = logging.getLogger("guild.scanner")

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# 雜訊目錄:系統路徑類的公會不顯示(可依需求調整)
DIR_BLOCKLIST = {"C--WINDOWS-system32"}

# 狀態門檻(秒)
ADVENTURING_WINDOW = 10 * 60  # 10 分鐘內 = 冒險中
RESTING_WINDOW = 24 * 60 * 60  # 24 小時內 = 休息中

# 熱力圖回溯天數(12 週)
HEATMAP_DAYS = 12 * 7

# ── 戰功制等級(取代舊 token 制)──────────────────────────────
# 等級來自「實際做的事」的加權分,而非 token 消耗。與前端 labels.js 一致。
XP_PER_LEVEL = 100

# 各類戰功權重:動手/施展技能最值錢,調查較輕
_HANDS_TOOLS = {"Edit", "Write", "NotebookEdit"}
_RUN_TOOLS = {"Bash", "PowerShell"}
XP_W_HANDS = 3
XP_W_RUNS = 2
XP_W_SKILLS = 4
XP_W_INVEST = 1
XP_W_PROMPTS = 2


def level_from_xp(xp: int) -> int:
    return max(1, xp // XP_PER_LEVEL + 1)


def _deeds_and_xp(acc: "_Accum") -> tuple[Deeds, int]:
    """把一個 session 的實際動作換算成戰功明細 + 加權經驗值。"""
    hands = runs = invest = 0
    for name, n in acc.tools.items():
        if name in _HANDS_TOOLS:
            hands += n
        elif name in _RUN_TOOLS:
            runs += n
        else:
            invest += n
    skills = sum(acc.skills.values())
    prompts = acc.prompt_count
    xp = (hands * XP_W_HANDS + runs * XP_W_RUNS + skills * XP_W_SKILLS
          + invest * XP_W_INVEST + prompts * XP_W_PROMPTS)
    return Deeds(hands=hands, runs=runs, skills=skills,
                 investigations=invest, prompts=prompts), xp


def _merge_deeds(items) -> Deeds:
    """把多個 Deeds 加總(persona / guild 用)。"""
    d = Deeds()
    for x in items:
        d.hands += x.hands
        d.runs += x.runs
        d.skills += x.skills
        d.investigations += x.investigations
        d.prompts += x.prompts
    return d


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _derive_status(last: datetime | None) -> str:
    if last is None:
        return "long_gone"
    delta = (datetime.now(timezone.utc) - last).total_seconds()
    if delta <= ADVENTURING_WINDOW:
        return "adventuring"
    if delta <= RESTING_WINDOW:
        return "resting"
    return "long_gone"


class _Accum:
    """單一 session 的累加狀態。"""

    def __init__(self) -> None:
        self.title: str | None = None
        self.last_prompt: str | None = None
        self.branch: str | None = None
        self.version: str | None = None
        self.cwd: str | None = None
        self.launch_cwd: str | None = None  # 第一個記錄的 cwd = 啟動目錄(resume 用,精確)
        self.last_ts: datetime | None = None
        self.tools: Counter[str] = Counter()
        self.skills: Counter[str] = Counter()
        self.message_count = 0
        self.prompt_count = 0  # 使用者提問次數(承接的委託數)
        self.total_tokens = 0
        self.log: list[LogEntry] = []
        # 每日活躍桶(本地日期字串 -> 次數),供熱力圖 / 戰報
        self.daily_events: Counter[str] = Counter()
        self.daily_prompts: Counter[str] = Counter()
        self.daily_tokens: Counter[str] = Counter()


def _consume_line(acc: _Accum, d: dict) -> None:
    t = d.get("type")

    ts = _parse_ts(d.get("timestamp"))
    if ts and (acc.last_ts is None or ts > acc.last_ts):
        acc.last_ts = ts
    day = ts.astimezone().strftime("%Y-%m-%d") if ts else None

    if d.get("cwd"):
        acc.cwd = d["cwd"]
        if acc.launch_cwd is None:
            acc.launch_cwd = d["cwd"]  # 只記第一個 = 啟動目錄,不隨途中 cd 漂移
    if d.get("gitBranch"):
        acc.branch = d["gitBranch"]
    if d.get("version"):
        acc.version = d["version"]
    if d.get("attributionSkill"):
        acc.skills[d["attributionSkill"]] += 1

    if t == "custom-title" and d.get("customTitle"):
        acc.title = d["customTitle"]
        acc.log.append(LogEntry(kind="title", text=d["customTitle"]))
    elif t == "last-prompt" and isinstance(d.get("lastPrompt"), str):
        acc.last_prompt = d["lastPrompt"]
    elif t == "user":
        acc.message_count += 1
        text = _extract_user_text(d)
        if text:
            acc.prompt_count += 1
            acc.log.append(LogEntry(kind="prompt", text=text[:400],
                                    timestamp=d.get("timestamp")))
            if day:
                acc.daily_prompts[day] += 1
                acc.daily_events[day] += 1
    elif t == "assistant":
        acc.message_count += 1
        msg = d.get("message") or {}
        usage = msg.get("usage") or {}
        tok = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        acc.total_tokens += tok
        if day and tok:
            acc.daily_tokens[day] += tok
        for blk in (msg.get("content") or []):
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                name = blk.get("name", "?")
                acc.tools[name] += 1
                summary = _tool_summary(name, blk.get("input"))
                text = f"{name} · {summary}" if summary else name
                acc.log.append(LogEntry(kind="tool", text=text,
                                        timestamp=d.get("timestamp")))
                if day:
                    acc.daily_events[day] += 1


def _tool_summary(name: str, inp: dict | None) -> str:
    """把工具 + 參數濃縮成一句白話,讓冒險日誌看得懂(對齊 hook 的 summarize)。"""
    inp = inp or {}
    try:
        if name in ("Edit", "Write", "NotebookEdit", "Read"):
            p = inp.get("file_path") or inp.get("notebook_path") or ""
            return Path(str(p)).name if p else ""
        if name in ("Bash", "PowerShell"):
            return (inp.get("description") or inp.get("command") or "")[:60]
        if name in ("Grep", "Glob"):
            return inp.get("pattern") or inp.get("query") or ""
        if name == "Skill":
            return inp.get("skill") or inp.get("command") or ""
        if name in ("Task", "Agent"):
            return inp.get("description") or inp.get("subagent_type") or ""
        if name == "WebSearch":
            return (inp.get("query") or "")[:60]
        if name == "WebFetch":
            return inp.get("url") or ""
        if name in ("TaskCreate", "TaskUpdate"):
            return inp.get("description") or ""
    except Exception:  # noqa: BLE001 — 摘要失敗不可影響解析
        pass
    return ""


def _extract_user_text(d: dict) -> str | None:
    msg = d.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                return blk.get("text")
            if isinstance(blk, str):
                return blk
    return None


def _skill_stats(acc: _Accum, top: int | None) -> list[SkillStat]:
    combined: list[SkillStat] = []
    for name, count in acc.skills.most_common():
        combined.append(SkillStat(name=name, count=count, kind="skill"))
    for name, count in acc.tools.most_common():
        combined.append(SkillStat(name=name, count=count, kind="tool"))
    # skill 優先排前,再依次數
    combined.sort(key=lambda s: (s.kind != "skill", -s.count))
    return combined[:top] if top else combined


def parse_session(path: Path) -> _Accum:
    """逐行串流解析一個 session jsonl。大檔友善,不整檔載入。"""
    acc = _Accum()
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _consume_line(acc, d)
    except OSError as exc:
        log.warning("讀取 session 失敗 %s: %s", path, exc)
    return acc


def _accum_to_adventurer(session_id: str, acc: _Accum) -> Adventurer:
    deeds, xp = _deeds_and_xp(acc)
    return Adventurer(
        id=session_id,
        title=acc.title,
        last_prompt=acc.last_prompt,
        branch=acc.branch,
        version=acc.version,
        status=_derive_status(acc.last_ts),  # type: ignore[arg-type]
        last_active=acc.last_ts.isoformat() if acc.last_ts else None,
        message_count=acc.message_count,
        top_skills=_skill_stats(acc, top=3),
        total_tokens=acc.total_tokens,
        deeds=deeds,
        xp=xp,
        level=level_from_xp(xp),
    )


# ── 職業分類(與前端 labels.js 的 CLASS_RULES 一致)────────────────
# 同公會內,依冒險者最擅長的技能把 session 收斂成「職業角色」(persona)。
_CLASS_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"rust|cargo|clippy", re.I), "⚙️", "工程師"),
    (re.compile(r"web|front|react|vue|playwright|css|design", re.I), "🎨", "術士"),
    (re.compile(r"test|qa|verify|review", re.I), "🧪", "鑑定師"),
    (re.compile(r"pdf|docx|xlsx|doc|wiki|note", re.I), "📚", "書記官"),
    (re.compile(r"gpu|homelab|passthrough|proxmox|docker|n8n", re.I), "🔧", "機關師"),
    (re.compile(r"git|deploy|push", re.I), "🌿", "斥候"),
    (re.compile(r"stock|predict|api|data", re.I), "🔮", "預言家"),
]
_DEFAULT_CLASS = ("🗡️", "冒險者")  # 無明確專精者一律收斂為通用冒險者
# 職業 → 圖示(供手動指定職業時查圖示;與前端 labels.js 的 CLASSES 一致)
_ICON_BY_KLASS = {klass: icon for _re, icon, klass in _CLASS_RULES}
_ICON_BY_KLASS["冒險者"] = "🗡️"


def _class_of(adv: Adventurer) -> tuple[str, str, str]:
    """回傳 (class_key, icon, klass)。class_key 目前即等於 klass。"""
    for s in adv.top_skills:
        for rule, icon, klass in _CLASS_RULES:
            if rule.search(s.name):
                return klass, icon, klass
    icon, klass = _DEFAULT_CLASS
    return klass, icon, klass


def _pick_status(members: list[Adventurer]) -> str:
    """取最活躍成員的狀態代表這位角色。"""
    order = {"adventuring": 0, "resting": 1, "long_gone": 2}
    return min((m.status for m in members), key=lambda s: order.get(s, 3))


def _make_persona(dir_key: str, guild_name: str, key: str, icon: str, klass: str,
                  members: list[Adventurer], name: str | None = None) -> Persona:
    """由一組成員 session 組出一位 persona 角色(取名的 persona 只含 1 個 session)。"""
    members = sorted(members, key=lambda a: a.last_active or "", reverse=True)
    lead = members[0]
    merged: Counter[str] = Counter()
    kinds: dict[str, str] = {}
    for a in members:
        for s in a.top_skills:
            merged[s.name] += s.count
            kinds[s.name] = s.kind
    top_skills = [
        SkillStat(name=n, count=c, kind=kinds.get(n, "tool"))  # type: ignore[arg-type]
        for n, c in merged.most_common(3)
    ]
    return Persona(
        id=f"{dir_key}::{key}",
        guild=guild_name,
        class_key=key,
        klass=klass,
        icon=icon,
        name=name,
        status=_pick_status(members),  # type: ignore[arg-type]
        last_active=lead.last_active,
        total_tokens=sum(a.total_tokens for a in members),
        deeds=_merge_deeds(a.deeds for a in members),
        xp=sum(a.xp for a in members),
        level=level_from_xp(sum(a.xp for a in members)),
        quest_count=len(members),
        active_quest_count=sum(1 for a in members if a.status == "adventuring"),
        lead_id=lead.id,
        title=lead.title,
        last_prompt=lead.last_prompt,
        top_skills=top_skills,
        quests=members,
    )


def _group_personas(dir_key: str, guild_name: str,
                    advs: list[Adventurer]) -> list[Persona]:
    """把一個公會的 session 收斂成 persona 角色。

    套用使用者身份覆蓋:
    - 指定職業 → 該 session 改歸到指定職業(影響合併分組與圖示)。
    - 取名 → 該 session **獨立成單獨一位角色**(不與同職者合併),大廳顯示取的名字。
    """
    overrides = db.all_overrides()
    groups: dict[str, list[Adventurer]] = {}
    meta: dict[str, tuple[str, str]] = {}  # class_key -> (icon, klass)
    personas: list[Persona] = []

    for a in advs:
        ov = overrides.get(a.id) or {}
        ov_name = (ov.get("name") or "").strip()
        ov_class = (ov.get("klass") or "").strip()
        key, icon, klass = _class_of(a)
        if ov_class:
            klass = ov_class
            icon = _ICON_BY_KLASS.get(ov_class, icon)
            key = ov_class
        if ov_name:
            # 取名 → 獨立成一位(以 session id 當 key,穩定且不與他人合併)
            personas.append(_make_persona(
                dir_key, guild_name, f"named:{a.id}", icon, klass, [a], name=ov_name))
        else:
            groups.setdefault(key, []).append(a)
            meta.setdefault(key, (icon, klass))

    for key, members in groups.items():
        icon, klass = meta[key]
        personas.append(_make_persona(dir_key, guild_name, key, icon, klass, members))

    personas.sort(key=lambda p: (-p.active_quest_count, -p.quest_count))
    return personas


def _encode_dir_key(path: str) -> str:
    """把一個 cwd 編成 claude 的 project 目錄名(`:` `\\` `/` 皆轉 `-`)。

    用來驗證 session 內記錄的 cwd 是否就是它的啟動目錄:編碼結果等於實際資料夾名,
    才代表沒在途中漂移過。注意此編碼有損(真連字號與路徑分隔都成 `-`),故僅用於
    「正向驗證」,不用於反解。
    """
    return re.sub(r"[:/\\]", "-", path)


def _guild_name_and_path(dir_key: str, cwd: str | None) -> tuple[str, str]:
    """優先用 session 內的 cwd 當真實路徑;否則由目錄名粗略反解。"""
    if cwd:
        path = cwd
    else:
        path = dir_key.replace("--", ":\\", 1).replace("-", "\\")
    name = Path(path).name or path
    return name, path


def scan_guilds() -> list[Guild]:
    """掃描所有公會與其冒險者(摘要層級)。"""
    guilds: list[Guild] = []
    if not PROJECTS_DIR.exists():
        log.warning("找不到 %s", PROJECTS_DIR)
        return guilds

    hidden = db.hidden_ids()
    for sub in sorted(PROJECTS_DIR.iterdir()):
        if not sub.is_dir() or sub.name in DIR_BLOCKLIST:
            continue
        adventurers: list[Adventurer] = []
        cwd_hint: str | None = None
        for jsonl in sub.glob("*.jsonl"):
            if jsonl.stat().st_size == 0 or jsonl.stem in hidden:
                continue
            acc = cache.get_or_parse(jsonl, parse_session)
            if acc.message_count == 0 and acc.last_ts is None:
                continue
            cwd_hint = cwd_hint or acc.cwd
            adventurers.append(_accum_to_adventurer(jsonl.stem, acc))

        if not adventurers:
            continue

        adventurers.sort(key=lambda a: a.last_active or "", reverse=True)
        name, path = _guild_name_and_path(sub.name, cwd_hint)
        guild_tokens = sum(a.total_tokens for a in adventurers)
        guild_xp = sum(a.xp for a in adventurers)
        guilds.append(Guild(
            name=name,
            path=path,
            dir_key=sub.name,
            adventurer_count=len(adventurers),
            active_count=sum(1 for a in adventurers if a.status == "adventuring"),
            total_tokens=guild_tokens,
            xp=guild_xp,
            level=level_from_xp(guild_xp),
            adventurers=adventurers,
            personas=_group_personas(sub.name, name, adventurers),
        ))

    # 有冒險中的公會排前,其次依冒險者數
    guilds.sort(key=lambda g: (-g.active_count, -g.adventurer_count))
    # 全量掃描已走過所有現存檔案,順手清掉快取裡已刪除的 session 路徑
    cache.prune_missing()
    return guilds


def _iter_sessions():
    """走訪所有非空 session,回傳 (guild_name, session_id, acc)。使用快取。"""
    if not PROJECTS_DIR.exists():
        return
    hidden = db.hidden_ids()
    for sub in sorted(PROJECTS_DIR.iterdir()):
        if not sub.is_dir() or sub.name in DIR_BLOCKLIST:
            continue
        cwd_hint: str | None = None
        rows = []
        for jsonl in sub.glob("*.jsonl"):
            try:
                if jsonl.stat().st_size == 0 or jsonl.stem in hidden:
                    continue
            except OSError:
                continue
            acc = cache.get_or_parse(jsonl, parse_session)
            if acc.message_count == 0 and acc.last_ts is None:
                continue
            cwd_hint = cwd_hint or acc.cwd
            rows.append((jsonl.stem, acc))
        if not rows:
            continue
        name, _ = _guild_name_and_path(sub.name, cwd_hint)
        for session_id, acc in rows:
            yield name, session_id, acc


def _heat_level(count: int) -> int:
    """把單日次數映射成 0-4 的熱力色階。"""
    if count <= 0:
        return 0
    if count < 5:
        return 1
    if count < 20:
        return 2
    if count < 50:
        return 3
    return 4


def _compute_streak(active_days: set[str]) -> int:
    """從今天(或昨天)往回數連續有活動的天數。"""
    today = date.today()
    # 允許「昨天還在動、今天剛開機還沒動」也算延續
    if today.strftime("%Y-%m-%d") in active_days:
        cursor = today
    elif (today - timedelta(days=1)).strftime("%Y-%m-%d") in active_days:
        cursor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while cursor.strftime("%Y-%m-%d") in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _build_achievements(
    streak: int,
    guild_count: int,
    total_tokens: int,
    skill_counts: Counter[str],
    active_days: set[str],
) -> list[Achievement]:
    """依全域統計產出成就清單(含未解鎖 + 進度),目標導向、看得到差距。"""
    top_skill, top_skill_n = ("", 0)
    if skill_counts:
        top_skill, top_skill_n = skill_counts.most_common(1)[0]

    def ach(id, name, desc, icon, ok, prog=None):
        return Achievement(id=id, name=name, desc=desc, icon=icon,
                           unlocked=ok, progress=prog)

    items = [
        ach("streak3", "連續冒險者", "連續 3 天有冒險紀錄", "🔥",
            streak >= 3, None if streak >= 3 else f"{streak}/3 天"),
        ach("streak7", "不眠的公會", "連續 7 天有冒險紀錄", "🌟",
            streak >= 7, None if streak >= 7 else f"{streak}/7 天"),
        ach("guilds5", "公會網絡", "同時經營 5 個以上公會", "🏰",
            guild_count >= 5, None if guild_count >= 5 else f"{guild_count}/5 座"),
        ach("busy30", "勤奮不懈", "累積 30 天有活動", "📅",
            len(active_days) >= 30,
            None if len(active_days) >= 30 else f"{len(active_days)}/30 天"),
        ach("tokens1m", "百萬委託", "全公會累積 100 萬 token", "💰",
            total_tokens >= 1_000_000,
            None if total_tokens >= 1_000_000 else f"{total_tokens // 10000 / 100:.2f}M / 1M"),
        ach("tokens5m", "傳說委託", "全公會累積 500 萬 token", "👑",
            total_tokens >= 5_000_000,
            None if total_tokens >= 5_000_000 else f"{total_tokens // 10000 / 100:.2f}M / 5M"),
    ]
    if top_skill:
        items.append(ach(
            "mastery", f"精通 {top_skill}", f"單一技能使用達 30 次(最熟:{top_skill})", "🎓",
            top_skill_n >= 30,
            None if top_skill_n >= 30 else f"{top_skill_n}/30 次",
        ))
    return items


def scan_report() -> Report:
    """聚合全公會戰報:今日/本週、熱力圖、成就、排行、布告欄任務。"""
    today_str = date.today().strftime("%Y-%m-%d")
    week_days = {(date.today() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)}

    global_daily: Counter[str] = Counter()
    skill_counts: Counter[str] = Counter()
    today_guild_events: Counter[str] = Counter()
    today = TodayStat()
    week = TodayStat()
    total_tokens = 0
    guild_names: set[str] = set()
    today_active_advs: set[str] = set()
    quests: list[Quest] = []

    for guild_name, session_id, acc in _iter_sessions():
        guild_names.add(guild_name)
        total_tokens += acc.total_tokens
        for name, n in acc.skills.items():
            skill_counts[name] += n
        for d, n in acc.daily_events.items():
            global_daily[d] += n
        # 今日
        if acc.daily_events.get(today_str):
            today_guild_events[guild_name] += acc.daily_events[today_str]
            today.actions += acc.daily_events[today_str] - acc.daily_prompts.get(today_str, 0)
            today.prompts += acc.daily_prompts.get(today_str, 0)
            today.tokens += acc.daily_tokens.get(today_str, 0)
            today_active_advs.add(session_id)
        # 本週
        for d in week_days:
            if acc.daily_events.get(d):
                week.actions += acc.daily_events[d] - acc.daily_prompts.get(d, 0)
                week.prompts += acc.daily_prompts.get(d, 0)
                week.tokens += acc.daily_tokens.get(d, 0)
        # 布告欄:進行中(冒險/休息)的任務單
        status = _derive_status(acc.last_ts)
        if status in ("adventuring", "resting"):
            title = acc.title or (acc.last_prompt[:28] if acc.last_prompt else "無名委託")
            quests.append(Quest(
                adventurer_id=session_id, title=title, guild=guild_name,
                status=status,  # type: ignore[arg-type]
                last_active=acc.last_ts.isoformat() if acc.last_ts else None,
            ))

    today.active_guilds = len(today_guild_events)
    today.active_adventurers = len(today_active_advs)

    active_days = set(global_daily.keys())
    streak = _compute_streak(active_days)

    # 熱力圖:固定回溯 HEATMAP_DAYS 天(含今天),缺的日子補 0
    heatmap: list[DayCount] = []
    for i in range(HEATMAP_DAYS - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        c = global_daily.get(d, 0)
        heatmap.append(DayCount(date=d, count=c, level=_heat_level(c)))

    top_guilds = [GuildActivity(name=n, events=e)
                  for n, e in today_guild_events.most_common(5)]

    quests.sort(key=lambda q: q.last_active or "", reverse=True)

    return Report(
        today=today,
        week=week,
        heatmap=heatmap,
        top_guilds_today=top_guilds,
        achievements=_build_achievements(
            streak, len(guild_names), total_tokens, skill_counts, active_days),
        streak=streak,
        quests=quests[:12],
    )


def session_location(session_id: str) -> tuple[str | None, str | None]:
    """找到 session 的『啟動目錄』與公會名(給召回時當 claude --resume 的 cwd)。

    關鍵:`claude --resume <id>` 是用「當前 cwd 換算出的 project 目錄名」去找 jsonl,
    而那個目錄名就是 jsonl 實際所在的資料夾(sub.name)。因此必須用 sub.name 反解出
    啟動目錄,**不能**用 session 內部記錄的 cwd(acc.cwd)—— 後者可能在工作途中被
    cd 到別處(例如去改 .claude/ 底下的檔),與啟動目錄不符就會讓 resume 找不到
    session(claude 回「No conversation found」),所有召回/接續入口跟著失效。
    """
    if not PROJECTS_DIR.exists():
        return None, None
    for sub in PROJECTS_DIR.iterdir():
        if not sub.is_dir() or sub.name in DIR_BLOCKLIST:
            continue
        jsonl = sub / f"{session_id}.jsonl"
        if jsonl.exists():
            acc = cache.get_or_parse(jsonl, parse_session)
            # 用「第一個記錄的 cwd」= 啟動目錄:它精確等於 claude 據以定位 session 的
            # project 目錄(不像途中被 cd 漂移的 acc.cwd)。驗證它編碼回去確實等於資料夾
            # 名,確認無誤後採用(精確、不受連字號路徑如 guild-hall 反解有損之害)。
            # 萬一 launch_cwd 缺失或不符(舊快取/異常),才退回用資料夾名粗略反解。
            launch = acc.launch_cwd
            if launch and _encode_dir_key(launch) == sub.name:
                return launch, Path(launch).name or launch
            name, path = _guild_name_and_path(sub.name, None)
            return path, name
    return None, None


def get_adventurer(session_id: str) -> AdventurerDetail | None:
    """找到單一冒險者並回傳含冒險日誌的細節。"""
    if not PROJECTS_DIR.exists():
        return None
    for sub in PROJECTS_DIR.iterdir():
        if not sub.is_dir() or sub.name in DIR_BLOCKLIST:
            continue
        jsonl = sub / f"{session_id}.jsonl"
        if not jsonl.exists():
            continue
        acc = cache.get_or_parse(jsonl, parse_session)
        base = _accum_to_adventurer(session_id, acc)
        guild_name, _ = _guild_name_and_path(sub.name, acc.cwd)
        ov = db.get_override(session_id)
        return AdventurerDetail(
            **base.model_dump(),
            guild=guild_name,
            recent_log=acc.log[-80:][::-1],
            all_skills=_skill_stats(acc, top=None),
            custom_name=(ov["name"] if ov else None),
            custom_class=(ov["klass"] if ov else None),
        )
    return None
