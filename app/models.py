"""Pydantic 模型 —— 冒險者公會的資料形狀。

RPG 對映:
- Guild(公會)= 一個資料夾(Claude 專案目錄)
- Adventurer(冒險者)= 一個 session(.jsonl 檔)
- SkillStat(技能)= 工具 / skill 的使用統計
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# 冒險狀態:依最後活動時間推導
Status = Literal["adventuring", "resting", "long_gone"]


class SkillStat(BaseModel):
    """技能使用統計。name 可能是內建工具(Edit/Bash…)或 skill 名。"""

    name: str
    count: int
    kind: Literal["tool", "skill"] = "tool"


class Deeds(BaseModel):
    """戰功明細 —— 從 session 實際做的事(工具/技能/委託)累計。

    這是等級 / 經驗的真正來源(取代舊的 token 累計):token 高只代表讀了很多
    context,不等於做了多少事。戰功才反映實際產出。
    """

    hands: int = 0          # ⚔️ 動手:Edit / Write / NotebookEdit
    runs: int = 0           # ⚙️ 執行:Bash / PowerShell
    skills: int = 0         # 📜 施展技能:attributionSkill 次數
    investigations: int = 0  # 🔍 調查:Read / Grep / Glob / Web…
    prompts: int = 0        # 📋 承接委託:使用者提問次數


class Adventurer(BaseModel):
    """一位冒險者 = 一個 session。"""

    id: str  # sessionId
    title: str | None = None  # customTitle,人類可讀任務名
    last_prompt: str | None = None  # 使用者最後委託
    branch: str | None = None  # gitBranch
    version: str | None = None  # Claude Code 版本(等級感)
    status: Status = "long_gone"
    last_active: str | None = None  # ISO timestamp
    message_count: int = 0
    top_skills: list[SkillStat] = []
    total_tokens: int = 0  # 消耗成本(僅顯示,不再拿來換等級)
    # 戰功 / 經驗:由實際做事累計 → 換算等級
    deeds: Deeds = Deeds()
    xp: int = 0  # 戰功加權總分
    level: int = 1  # 由 xp 換算
    achievements: list[str] = []


class Persona(BaseModel):
    """一位「角色」= 同公會內同職業的多個 session 收斂而成。

    RPG 對映:persona = 有持久身分、跨 session 累積經驗的冒險者本人;
    其底下的每個 session 是他的一趟冒險(quest)。召回 / 回報 / 進化 / 退隱
    仍以 quest(session_id)為原子單位,persona 只是聚合顯示層。
    """

    id: str  # f"{dir_key}::{class_key}",跨掃描穩定
    guild: str
    class_key: str  # 職業鍵(同 klass)
    klass: str  # 職業顯示名(工程師 / 術士 …)
    icon: str  # 職業圖示
    status: Status = "long_gone"  # 取最活躍成員
    last_active: str | None = None
    total_tokens: int = 0  # 全體消耗(僅顯示)
    deeds: Deeds = Deeds()  # 戰功加總
    xp: int = 0  # 戰功總分 → 等級來源
    level: int = 1
    quest_count: int = 0  # 冒險趟數(成員 session 數)
    active_quest_count: int = 0
    lead_id: str  # 代表 session(最近活動),點角色預設開這趟
    title: str | None = None  # 代表 session 的任務名(最近在做什麼)
    name: str | None = None  # 使用者手動取的角色名(有則大廳顯示此名,取代職業名)
    last_prompt: str | None = None
    top_skills: list[SkillStat] = []
    quests: list[Adventurer] = []  # 冒險日誌:同職所有 session


class Guild(BaseModel):
    """一個公會 = 一個資料夾。"""

    name: str  # 顯示名(資料夾最後一段)
    path: str  # 反解後的真實路徑
    dir_key: str  # 原始編碼目錄名(如 D--projects-stock-predictor)
    adventurer_count: int = 0
    active_count: int = 0
    total_tokens: int = 0  # 公會全體消耗(僅顯示)
    xp: int = 0  # 公會全體戰功 → 等級來源
    level: int = 1
    adventurers: list[Adventurer] = []  # 原子層:每個 session 一位(保留給 per-session 功能)
    personas: list[Persona] = []  # 聚合層:大廳實際渲染的職業角色


# ── 戰報 / 成長系統模型 ──────────────────────────────────────────


class ActivityEntry(BaseModel):
    """即時動態一則:某 session 剛用了什麼工具做什麼(來自 PostToolUse hook)。"""

    ts: str | None = None
    session_id: str | None = None
    guild: str | None = None
    tool: str = "?"
    summary: str = ""


class DayCount(BaseModel):
    """熱力圖一格:某一天的活躍量。"""

    date: str  # YYYY-MM-DD(本地時區)
    count: int = 0
    level: int = 0  # 0-4,對映熱力圖色階


class TodayStat(BaseModel):
    """今日/本週戰報數字。"""

    prompts: int = 0  # 委託數(使用者訊息)
    actions: int = 0  # 行動數(工具呼叫)
    tokens: int = 0
    active_guilds: int = 0
    active_adventurers: int = 0


class GuildActivity(BaseModel):
    """今日各公會活躍度排行。"""

    name: str
    events: int = 0


class Achievement(BaseModel):
    """成就 / 徽章。未解鎖時 unlocked=False 並附進度文字。"""

    id: str
    name: str
    desc: str
    icon: str
    unlocked: bool = False
    progress: str | None = None  # 未解鎖時顯示,如 "3/7 天"


class Report(BaseModel):
    """公會總戰報 —— 給櫃檯小姐播報用。"""

    today: TodayStat = TodayStat()
    week: TodayStat = TodayStat()
    heatmap: list[DayCount] = []
    top_guilds_today: list[GuildActivity] = []
    achievements: list[Achievement] = []
    streak: int = 0  # 連續冒險天數(至今日或昨日)
    quests: list["Quest"] = []  # 布告欄:近期進行中的任務


class Quest(BaseModel):
    """布告欄上的一張委託單 = 某冒險者的近期任務。"""

    adventurer_id: str
    title: str
    guild: str
    status: Status
    last_active: str | None = None


# ── 任務回報 / 櫃台佇列模型 ──────────────────────────────────────


class ReportSubmit(BaseModel):
    """冒險者向櫃台提交的回報單(POST body)。"""

    adventurer_id: str | None = None
    guild: str | None = None
    title: str  # 任務名 / 一句話成果
    summary: str | None = None  # 做了什麼
    files_changed: list[str] = []  # 改了哪些檔
    lessons: str | None = None  # 學到 / 心得
    outcome: Literal["success", "partial", "failed"] = "success"


class ReportRecord(BaseModel):
    """佇列中一筆回報單的完整狀態。"""

    id: int
    adventurer_id: str | None = None
    guild: str | None = None
    title: str
    summary: str | None = None
    files_changed: list[str] = []
    lessons: str | None = None
    outcome: str = "success"
    status: Literal["pending", "processing", "done", "failed"] = "pending"
    result: str | None = None
    created_at: str | None = None
    processed_at: str | None = None
    # 使用者幕後審核裁決:pending(待審)/ approved(認可)/ rejected(退回)
    review: Literal["pending", "approved", "rejected"] = "pending"
    review_note: str | None = None
    # 職業結果卡:依冒險者職業渲染的結構化結果(如 coding 卡含檔案 diff)。None = 用 markdown 保底
    card: dict | None = None


class ReviewReject(BaseModel):
    """退回一份報告(退回櫃台重寫)。"""

    note: str | None = None


class PolishReq(BaseModel):
    """把使用者自己寫的草稿回報送 AI 潤稿(不歸檔,只回傳整理後的文字)。"""

    title: str | None = None
    summary: str | None = None
    lessons: str | None = None
    guild: str | None = None


class ReviewSkill(BaseModel):
    """櫃台的審查技能(隨處理報告數解鎖)。"""

    key: str
    name: str
    desc: str
    at: int  # 解鎖門檻(累積處理報告數)
    unlocked: bool = False


class QueueStatus(BaseModel):
    """櫃台佇列總覽:各狀態計數 + 近期回報 + 櫃台成長。"""

    pending: int = 0
    processing: int = 0
    done: int = 0
    failed: int = 0
    recent: list[ReportRecord] = []
    # 櫃台小姐的成長
    reception_level: int = 1
    reception_next_at: int | None = None  # 下一個審查技能的門檻
    review_skills: list[ReviewSkill] = []
    # 學習迴路(退回 → 櫃台學敘事;繼續 → 冒險者學做事方式)
    reception_prefs: list[str] = []  # A:櫃台學到的敘事偏好
    work_prefs: list[str] = []       # B:冒險者學到的做事方式


# ── 委託布告欄模型(使用者自己貼的任務)──────────────────────────


class CommissionPost(BaseModel):
    """貼一張新委託到布告欄。"""

    guild: str | None = None
    title: str
    note: str | None = None
    goal: str | None = None  # 明確目標(可中途修改);未填時派遣以 title 為目標
    cwd: str | None = None  # 派遣時的工作資料夾(公會路徑)
    model: str | None = None  # 冒險者位階(模型 id;空 = 跟隨 CLI 預設)
    klass: str | None = None  # 指定職業/呈現皮膚(決定結果卡:coding diff / 教學字卡)
    images: list[str] = []  # 附圖 URL(/uploads/...);派遣時注入路徑供冒險者讀圖


# open(招募中)/ queued(排隊等派遣)/ working(執行中)/ blocked(受阻等指示)/ failed / done
CommissionStatus = Literal["open", "queued", "working", "blocked", "failed", "done"]


class Commission(BaseModel):
    """布告欄上一張使用者委託。"""

    id: int
    guild: str | None = None
    title: str
    note: str | None = None
    status: CommissionStatus = "open"
    created_at: str | None = None
    done_at: str | None = None
    # 派遣系統
    goal: str | None = None
    cwd: str | None = None
    session_id: str | None = None  # headless 冒險者的 session(可續冒險)
    rounds: int = 0
    model: str | None = None  # 冒險者位階(派遣模型)
    klass: str | None = None  # 指定職業/呈現皮膚
    cost: float = 0.0  # 此委託累計花費(USD,跨多次派遣)
    images: list[str] = []  # 附圖 URL(/uploads/...)


class CommissionPatch(BaseModel):
    """中途調整委託:改目標 / 標題 / 備註 / 工作資料夾 / 位階。"""

    goal: str | None = None
    title: str | None = None
    note: str | None = None
    cwd: str | None = None
    model: str | None = None


class DispatchReq(BaseModel):
    """派遣一位 headless 冒險者去執行委託。"""

    cwd: str | None = None  # 未填則用委託上的 cwd
    model: str | None = None  # 本次派遣的位階;未填則用委託上的 model


class SteerPost(BaseModel):
    """對執行中的委託下中途指示(下一回合注入)。"""

    content: str


class CommissionEvent(BaseModel):
    """委託進度時間線一則。"""

    id: int
    type: Literal["steer", "progress", "system", "step"]
    content: str
    round: int | None = None
    created_at: str | None = None


# ── 冒險者進化系統模型 ──────────────────────────────────────────

class RecallReq(BaseModel):
    """召回冒險者(回報 / 接續)。"""

    adventurer_id: str


class RecallAsk(BaseModel):
    """向被召回的冒險者提問。"""

    adventurer_id: str
    question: str


Route = Literal["memory", "skill", "tool", "script"]


class EvolutionRecord(BaseModel):
    """一次已完成的進化 = 冒險者習得的一項能力。"""

    id: int
    adventurer_id: str | None = None
    guild: str | None = None
    route: Route
    name: str
    detail: str | None = None
    based_on: int = 0
    created_at: str | None = None


class EvolutionSubmit(BaseModel):
    """登記一次進化(通常由 `訓練所` skill 執行完後 POST)。"""

    adventurer_id: str | None = None
    guild: str | None = None
    route: Route
    name: str  # 習得的能力 / skill 名稱
    detail: str | None = None  # 產出位置 / 說明


class ProposalRecord(BaseModel):
    """一筆進化提案(訓練所自動化產出,批准後才落地)。"""

    id: int
    adventurer_id: str | None = None  # None = 全公會提煉(distill)
    guild: str | None = None
    route: Route
    name: str
    detail: str | None = None
    artifact_path: str | None = None
    artifact_content: str | None = None
    status: Literal["pending", "approved", "rejected"] = "pending"
    decided_note: str | None = None
    created_at: str | None = None
    decided_at: str | None = None


class TrainReq(BaseModel):
    """送一位冒險者進訓練所(headless 分析產提案)。"""

    adventurer_id: str


class ProposalDecision(BaseModel):
    note: str | None = None


class EvolutionCandidate(BaseModel):
    """可進化的冒險者 + 供 LLM 分析用的素材。"""

    adventurer_id: str
    guild: str | None = None
    name: str  # 冒險者顯示名
    completed: int = 0  # 已完成任務數
    earned: int = 0  # 可獲得的進化次數(completed // 5)
    claimed: int = 0  # 已進化次數
    available: int = 0  # 尚可進化次數
    suggested_route: Route = "memory"
    recent_titles: list[str] = []  # 近期解決的任務
    recent_lessons: list[str] = []  # 近期心得
    learned: list[EvolutionRecord] = []  # 已習得能力


class Ability(BaseModel):
    """使用者自訂的冒險者能力標籤(可增刪)。"""

    id: int
    name: str
    created_at: str | None = None


class AbilityAdd(BaseModel):
    name: str


# ── 公會夜巡(NPC 自主靈感)模型 ────────────────────────────────

class MuseIdea(BaseModel):
    """夜巡產出的一張改善靈感卡。"""

    id: int
    guild: str | None = None
    cwd: str | None = None
    kind: str = "idea"  # improve / bug / refactor / idea / test / doc
    title: str
    headline: str | None = None   # 一句白話:發現了什麼問題或機會(主人一眼掃)
    next_step: str | None = None  # 一句白話:建議的下一步該做什麼
    impact: str | None = None     # 影響/價值提示:高 / 中 / 低
    body: str | None = None
    effort: str | None = None  # S / M / L
    status: Literal["new", "promoted", "dismissed"] = "new"
    created_at: str | None = None


class MuseConfig(BaseModel):
    """夜巡設定(存 settings.muse,可由面板調整)。"""

    enabled: bool = False           # 總開關
    daily_budget_usd: float = 1.0   # 每日花費上限(達標即當日停巡)
    hour_start: int = 1             # 允許巡邏的時段(本地 24h)起
    hour_end: int = 6              # 時段迄(start<=hour<end;跨夜自動處理)
    interval_min: int = 45          # 每輪間隔(分鐘)
    ideas_per_run: int = 3          # 單輪最多產幾張靈感卡
    model: str = "claude-sonnet-4-6"  # 巡邏用模型
    include_guilds: list[str] = []  # 只巡這些公會(空 = 全部)
    # 自主執行:夜巡自己挑「安全、自足、低風險」的靈感,直接排入派遣佇列做掉(免人工同意)
    auto_execute: bool = False      # 自主執行總開關(預設關,開了會自動改檔+花 token)
    auto_per_run: int = 1           # 每輪最多自動接幾張(其餘仍留給你人工挑)
    auto_max_effort: str = "S"      # 只自動做這個工作量以下(S / M);L 一律留人工


class AdventurerDetail(Adventurer):
    """冒險者細節 —— 含近期冒險日誌。"""

    guild: str | None = None
    recent_log: list[LogEntry] = []
    all_skills: list[SkillStat] = []
    custom_abilities: list[Ability] = []  # 使用者手動編輯的能力
    custom_name: str | None = None   # 使用者手動取的名字(None = 未取名)
    custom_class: str | None = None  # 使用者手動指定的職業(None = 自動判定)


class IdentitySet(BaseModel):
    """設定冒險者身份:取名 / 指定職業(空字串 = 清除,回自動)。"""

    name: str | None = None
    klass: str | None = None


class LogEntry(BaseModel):
    """冒險日誌一條:一次委託或一次工具動作。"""

    kind: Literal["prompt", "tool", "title"]
    text: str
    timestamp: str | None = None


AdventurerDetail.model_rebuild()
Report.model_rebuild()
