// 狀態 / 顯示文字對照

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

// 依冒險者最擅長的技能推派「職業」圖示
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

// 冒險者位階:派遣時用的模型(強弱 vs 花費的取捨)
export const RANKS = [
  { value: '', label: '⭐ 預設位階(跟隨 CLI 設定)', short: '⭐' },
  { value: 'claude-haiku-4-5-20251001', label: '🥉 銅牌冒險者(haiku,快又省)', short: '🥉' },
  { value: 'claude-sonnet-4-6', label: '🥈 銀牌冒險者(sonnet,均衡)', short: '🥈' },
  { value: 'claude-opus-4-8', label: '🥇 金牌冒險者(opus,最強最貴)', short: '🥇' },
]
export function rankOf(model) {
  return RANKS.find((r) => r.value === (model || '')) ||
    { value: model, label: model, short: '❓' }
}
