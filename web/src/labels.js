// 狀態 / 顯示文字對照

// 主 Claude Code session「主動回報公會」的成果,一律掛在這個固定的「櫃台書記」身分下,
// 而非某個被掃描到的 session —— 才不會混進使用者真正派遣的冒險者名冊 / 完成數。
// 值需與「公會報告」skill POST 的 adventurer_id 一致。
export const RECEPTION_ID = '櫃台書記'
export const RECEPTION_PERSONA = { icon: '🧝‍♀️', klass: '櫃台書記', guild: '櫃台' }

export const STATUS_META = {
  adventuring: { label: '冒險中', emoji: '⚔️', color: '#3f9142', dot: '#4caf50' },
  resting: { label: '休息中', emoji: '🍺', color: '#b8860b', dot: '#e0a815' },
  long_gone: { label: '久未歸來', emoji: '💤', color: '#8a7f6a', dot: '#b0a692' },
}

export function statusMeta(status) {
  return STATUS_META[status] || STATUS_META.long_gone
}

// 相對時間:上次現身
export function relativeTime(iso) {
  if (!iso) return '從未現身'
  const then = new Date(iso).getTime()
  const diff = Math.max(0, Date.now() - then)
  const s = Math.floor(diff / 1000)
  if (s < 60) return '剛剛'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分鐘前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小時前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} 天前`
  return `${Math.floor(d / 30)} 個月前`
}

// 戰功制等級:每 XP_PER_LEVEL 點戰功 ≈ 1 級(與後端 scanner.py 一致)
// 戰功來自實際做事(動手/執行/技能/調查/委託),不再用 token(讀 context 也會漲)。
const XP_PER_LEVEL = 100

export function levelFromXp(xp) {
  return Math.max(1, Math.floor((xp || 0) / XP_PER_LEVEL) + 1)
}

// 等級進度條(吃 xp):回傳 { level, into, span, pct }
export function xpProgress(xp) {
  const t = xp || 0
  const level = levelFromXp(t)
  const into = t % XP_PER_LEVEL
  return {
    level,
    into,
    span: XP_PER_LEVEL,
    pct: Math.round((into / XP_PER_LEVEL) * 100),
  }
}

// 戰功明細 → 顯示用(圖示 + 標籤)
export const DEED_META = [
  ['hands', '⚔️', '動手'],
  ['runs', '⚙️', '執行'],
  ['skills', '📜', '技能'],
  ['investigations', '🔍', '調查'],
  ['prompts', '📋', '委託'],
]

export function shortTokens(tokens) {
  if (!tokens) return '0'
  if (tokens >= 1e6) return (tokens / 1e6).toFixed(1) + 'M'
  if (tokens >= 1e3) return (tokens / 1e3).toFixed(0) + 'k'
  return String(tokens)
}

// 由字串決定一個穩定色相(角色服裝顏色)
export function hashHue(str) {
  let h = 0
  for (let i = 0; i < (str || '').length; i++) {
    h = (h * 31 + str.charCodeAt(i)) % 360
  }
  return h
}

// 依冒險者最擅長的技能推派「職業」圖示。
// 工程師 vs 機關師的分野:工程師寫「程式碼」(原始碼 / 演算法 / 函式庫),
// 機關師顧「機關」(基礎設施 / 服務 / 容器 / 硬體透傳 —— 動的是環境與系統,不是 app 原始碼)。
const CLASS_RULES = [
  [/rust|cargo|clippy/i, { icon: '⚙️', klass: '工程師' }],
  [/web|front|react|vue|playwright|css|design|frontend/i, { icon: '🎨', klass: '術士' }],
  [/test|qa|verify|review/i, { icon: '🧪', klass: '鑑定師' }],
  [/pdf|docx|xlsx|doc|wiki|note/i, { icon: '📚', klass: '書記官' }],
  [/gpu|homelab|passthrough|proxmox|docker|n8n/i, { icon: '🔧', klass: '機關師' }],
  [/git|deploy|push/i, { icon: '🌿', klass: '斥候' }],
  [/stock|predict|api|data/i, { icon: '🔮', klass: '預言家' }],
]

const TOOL_ICON = {
  Bash: '⚔️', PowerShell: '⚔️', Edit: '🖋️', Write: '🖋️',
  Read: '🔍', Grep: '🔍', Glob: '🔍', Agent: '🧙', Task: '📋',
}

export function classOf(adventurer) {
  const skills = adventurer?.top_skills || adventurer?.all_skills || []
  for (const s of skills) {
    for (const [re, meta] of CLASS_RULES) {
      if (re.test(s.name)) return meta
    }
  }
  // 退而用最常用工具
  for (const s of skills) {
    if (TOOL_ICON[s.name]) return { icon: TOOL_ICON[s.name], klass: '冒險者' }
  }
  return { icon: '🗡️', klass: '冒險者' }
}

// 所有可手動指定的職業(供冒險者頁的職業選單)。
// desc 一句話講清楚「這職業在幹嘛」—— 解決使用者「工程師跟機關師到底差在哪」的困惑。
export const CLASSES = [
  { klass: '工程師', icon: '⚙️', desc: '寫程式碼:原始碼、演算法、函式庫' },
  { klass: '術士', icon: '🎨', desc: '前端畫面:網頁、元件、樣式設計' },
  { klass: '鑑定師', icon: '🧪', desc: '品質把關:測試、驗證、程式審查' },
  { klass: '書記官', icon: '📚', desc: '文件教學:整理知識、產教學字卡' },
  { klass: '機關師', icon: '🔧', desc: '顧機關:基礎設施、容器、服務、硬體' },
  { klass: '斥候', icon: '🌿', desc: '出貨:git、部署、推送上線' },
  { klass: '預言家', icon: '🔮', desc: '資料預測:API、數據、股價模型' },
  { klass: '冒險者', icon: '🗡️', desc: '通用:什麼都能接的萬用職' },
]
export function iconForClass(klass) {
  return (CLASSES.find((c) => c.klass === klass) || { icon: '🗡️' }).icon
}

// ── 解決的問題分類 ──────────────────────────────────────
// 完成卡上標「這到底解決了哪一類問題」,讓已完成的項目一眼看出類型、可被歸類。
// slug 為單一來源;後端 dispatch.py 的 _CODING_CATEGORIES 需與這份 key 一致。
export const PROBLEM_KINDS = {
  bugfix: { icon: '🐛', label: '修 Bug', color: '#d2544b' },
  feature: { icon: '✨', label: '新功能', color: '#3f9142' },
  refactor: { icon: '♻️', label: '重構', color: '#2f8fb3' },
  perf: { icon: '⚡', label: '效能', color: '#d99b16' },
  config: { icon: '⚙️', label: '設定/環境', color: '#8a7f6a' },
  docs: { icon: '📝', label: '文件', color: '#7a6cc4' },
  test: { icon: '🧪', label: '測試', color: '#4a9b8e' },
  ui: { icon: '🎨', label: '介面', color: '#c45fa0' },
  chore: { icon: '🧹', label: '雜務', color: '#9a9488' },
  other: { icon: '📦', label: '其他', color: '#8a8a8a' },
}
// 取分類的顯示資料;認不得的 slug / 空值 → 回 null(呼叫端不渲染徽章)
export function problemKind(slug) {
  return (slug && PROBLEM_KINDS[slug]) || null
}

// 鑑定師查驗總裁決(與後端 dispatch.py 的 _VERDICT_WORDS 值一致)
export const VERDICT_META = {
  pass: { icon: '✅', label: '通過', color: '#3f9142' },
  warn: { icon: '⚠️', label: '有疑慮', color: '#d99b16' },
  fail: { icon: '❌', label: '未過', color: '#d2544b' },
}
export function verdictMeta(slug) {
  return VERDICT_META[slug] || VERDICT_META.warn
}

// 職業 → 結果皮膚(result card kind)。沒對應的職業就用 markdown 保底(回 null)。
const CLASS_SKIN = {
  工程師: 'coding',
  機關師: 'ops', // 機關師:指令/服務皮膚(與工程師的 code diff 區隔)
  鑑定師: 'review', // 鑑定師:查驗清單 + 裁決皮膚
  斥候: 'ship', // 斥候:出貨(commit/推送/部署)皮膚
  書記官: 'teaching',
}
export function skinForClass(klass) {
  return CLASS_SKIN[klass] || null
}

// 冒險者位階:派遣時用的模型(強弱 vs 花費的取捨)。
// 這是「位階 → 模型」的單一來源:布告欄三處派遣選單 + 夜巡設定都吃這份,
// 改這裡就全站同步(別再另開重複清單)。值就是 claude CLI `--model` 吃的字串
// (完整名或 alias 皆可);rankOf 對清單外的任意模型字串也能優雅顯示。
export const CUSTOM_RANK = '__custom__' // 自訂模型哨兵值(選它會跳出輸入框打任意模型)
export const RANKS = [
  { value: '', label: '⭐ 預設位階(跟隨 CLI 設定)', short: '⭐' },
  { value: 'claude-haiku-4-5-20251001', label: '🥉 銅牌冒險者(haiku,快又省)', short: '🥉' },
  { value: 'claude-sonnet-4-6', label: '🥈 銀牌冒險者(sonnet,均衡)', short: '🥈' },
  { value: 'claude-opus-4-8', label: '🥇 金牌冒險者(opus,最強)', short: '🥇' },
  { value: 'claude-fable-5', label: '📖 說書人(fable,敘事特化)', short: '📖' },
  { value: CUSTOM_RANK, label: '✍️ 自訂模型…(填任何 cloud 模型名)', short: '✍️' },
]
export function rankOf(model) {
  return RANKS.find((r) => r.value === (model || '')) ||
    { value: model, label: model, short: '🎖' }
}

// 位階自動建議:依委託關鍵字 + 篇幅,粗略推薦一個派遣位階(使用者可覆寫)。
// 高難度詞優先(即使夾雜小改字眼);其次輕量詞;最後看描述長短。
const GOLD_KW = ['重構', 'refactor', '設計', 'design', '架構', 'architect',
  '演算法', 'algorithm', '優化', 'optimi', '效能', 'performance', '除錯', 'debug',
  '安全', 'security', '遷移', 'migrat', '複雜', '整個系統', '實作', '重寫', 'rewrite']
const BRONZE_KW = ['改個字', '錯字', 'typo', '重新命名', 'rename', '改名', '加註解',
  '註解', '格式化', 'format', '排版', '小改', '版號', 'bump', '拼字', '標點', '換行']

export function suggestRank(text) {
  const t = (text || '').toLowerCase().trim()
  if (!t) return { value: '', reason: '' }
  for (const k of GOLD_KW) {
    if (t.includes(k.toLowerCase())) {
      return { value: 'claude-opus-4-8', reason: `偵測到「${k}」,屬高難度工作` }
    }
  }
  for (const k of BRONZE_KW) {
    if (t.includes(k.toLowerCase())) {
      return { value: 'claude-haiku-4-5-20251001', reason: `偵測到「${k}」,屬輕量改動` }
    }
  }
  if (t.length > 120) return { value: 'claude-opus-4-8', reason: '委託描述長、範圍大' }
  if (t.length < 20) return { value: 'claude-haiku-4-5-20251001', reason: '委託簡短' }
  return { value: 'claude-sonnet-4-6', reason: '一般難度,均衡位階' }
}
