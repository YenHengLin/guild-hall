<script setup>
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useGuildStore } from '../stores/guild'
import { statusMeta, relativeTime, rankOf, suggestRank, problemKind } from '../labels'
import { uploadImage } from '../api'
import CommissionReport from './CommissionReport.vue'
import Markdown from './Markdown.vue'
import ModelPicker from './ModelPicker.vue'
import { useFloatingPanel } from '../composables/useFloatingPanel'

// 把展開的任務/完成詳情「彈出」成可拖曳+可拉寬的浮動視窗(同時只彈一張)
const { onDragStart: popDragStart, panelStyle: popStyle, setFloating } = useFloatingPanel()
const poppedId = ref(null)
function togglePop(id) {
  if (poppedId.value === id) { poppedId.value = null; setFloating(false) }
  else { poppedId.value = id; setFloating(true, 160, 90) }
}

const props = defineProps({
  quests: { type: Array, default: () => [] }, // session 自動衍生(進行中)
  commissions: { type: Array, default: () => [] }, // 使用者自己貼
  queue: { type: Object, default: null }, // 櫃台回報佇列(用來把委託與其回報顯示在一起)
})
const emit = defineEmits(['open'])
const store = useGuildStore()

// 過濾掉已被委託系統追蹤的 session(避免與 runningList 重複顯示)
const commissionSessionIds = computed(() =>
  new Set(props.commissions.map((c) => c.session_id).filter(Boolean)))
const sessionQuests = computed(() =>
  props.quests.filter((q) => !commissionSessionIds.value.has(q.adventurer_id)).slice(0, 5))
// 領域公會篩選:null = 全部;選某領域即只看該領域的委託(含「未分領域」)
const domainFilter = ref(null)
function matchDomain(c) {
  if (domainFilter.value === null) return true
  if (domainFilter.value === '__none__') return !c.domain
  return c.domain === domainFilter.value
}
const scoped = computed(() => props.commissions.filter(matchDomain))
// 各領域公會的委託數(供布告欄頂端的領域切換條)
const domainSummary = computed(() => {
  const counts = {}
  let none = 0
  for (const c of props.commissions) {
    if (c.domain) counts[c.domain] = (counts[c.domain] || 0) + 1
    else none += 1
  }
  const list = Object.entries(counts).map(([name, n]) => ({ name, n }))
    .sort((a, b) => b.n - a.n)
  return { list, none }
})
const openList = computed(() => scoped.value.filter((c) => c.status === 'open'))
const runningList = computed(() =>
  scoped.value.filter((c) => ['working', 'queued', 'blocked', 'failed'].includes(c.status)))
const doneList = computed(() => scoped.value.filter((c) => c.status === 'done'))
// 已完成會累積,預設收合保持布告欄清爽;點分區標題展開
const showDone = ref(false)
// 其他進行中的冒險(非委託系統):有 running commissions 時預設折疊
const showSessions = ref(false)
// 本次剛完成(working→done)的委託:留在原地展開、報告就地顯示,不必去已完成分區翻。
// 使用者按「收起」後才移進下方收合的歷史。
const justDone = ref(new Set())
const freshDone = computed(() => doneList.value.filter((c) => justDone.value.has(c.id)))
// 已完成依「問題類型」篩選:null=全部;點分類統計條上的某類即只看那類(模組化已完成項目)
const catFilter = ref(null)
function toggleCatFilter(slug) {
  catFilter.value = catFilter.value === slug ? null : slug
}
const oldDoneAll = computed(() => doneList.value.filter((c) => !justDone.value.has(c.id)))
const oldDone = computed(() => oldDoneAll.value.filter((c) =>
  !catFilter.value || catSlugOf(c) === catFilter.value))
// 已完成依「完成時間」分區:今天 / 近一週 / 更早 —— 讓最近完成的不被舊項目淹沒
function doneBucket(c) {
  const d = c.done_at ? new Date(c.done_at) : null
  if (!d || isNaN(d.getTime())) return 'older'
  const now = new Date()
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (d >= startToday) return 'today'
  const weekAgo = new Date(startToday); weekAgo.setDate(weekAgo.getDate() - 6)
  return d >= weekAgo ? 'week' : 'older'
}
const DONE_BUCKETS = [
  { key: 'today', icon: '📅', label: '今天' },
  { key: 'week', icon: '🗓', label: '近一週' },
  { key: 'older', icon: '📦', label: '更早' },
]
const doneGroups = computed(() => {
  const g = { today: [], week: [], older: [] }
  for (const c of oldDone.value) g[doneBucket(c)].push(c)
  return g
})
// 預設展開「今天 / 近一週」,「更早」收合,專注在新內容
const openBuckets = ref(new Set(['today', 'week']))
function toggleBucket(k) {
  const s = new Set(openBuckets.value)
  s.has(k) ? s.delete(k) : s.add(k)
  openBuckets.value = s
}
// 已完成項目依問題類型歸類統計:一眼看出「解決了哪些類型的問題、各幾件」
const doneCategorySummary = computed(() => {
  const counts = {}
  for (const c of doneList.value) {
    if (justDone.value.has(c.id)) continue
    const slug = catSlugOf(c)
    if (slug) counts[slug] = (counts[slug] || 0) + 1
  }
  return Object.entries(counts)
    .map(([slug, n]) => ({ slug, n, meta: problemKind(slug) }))
    .filter((x) => x.meta)
    .sort((a, b) => b.n - a.n)
})
// 有回報卡但沒分類(舊卡 / 教學卡)的已完成件數 —— 統計條顯示「未分類」把帳對齊
const uncategorizedDone = computed(() =>
  oldDoneAll.value.filter((c) => !catSlugOf(c)).length)
function dismissDone(c) {
  justDone.value.delete(c.id)
  if (expandedId.value === c.id) expandedId.value = null
}

const COMMISSION_META = {
  queued: { emoji: '🎫', label: '排隊中' },
  working: { emoji: '🏃', label: '執行中' },
  blocked: { emoji: '🧱', label: '待你續做' },
  failed: { emoji: '💥', label: '失敗' },
}

// 把櫃台回報建成索引(recent 為新→舊,首見即最新):
// session_id → 回報、標題 → 回報。委託與其派遣回報就靠這兩把鑰匙對上。
const reportIndex = computed(() => {
  const bySid = {}
  const byTitle = {}
  for (const r of props.queue?.recent || []) {
    if (r.adventurer_id && !(r.adventurer_id in bySid)) bySid[r.adventurer_id] = r
    if (!(r.title in byTitle)) byTitle[r.title] = r
  }
  return { bySid, byTitle }
})
// 找出這張委託派遣後產生的櫃台回報:優先用 session_id 對應
//(派遣完成時 report.adventurer_id = 委託 session_id),退而用「[派遣] 標題」對應。
function reportFor(c) {
  const { bySid, byTitle } = reportIndex.value
  if (c.session_id && bySid[c.session_id]) return bySid[c.session_id]
  return byTitle[`[派遣] ${c.title}`] || null
}

// 這張委託「誰做的」:反查派遣 session 的冒險者職業身份(反查不到回 null)
function doerOf(c) {
  return c.session_id ? store.personaBySession[c.session_id] : null
}

// 這張委託「解決了哪類問題」:從它的櫃台回報結果卡取 category(coding/ops 卡才有)
function catSlugOf(c) {
  return reportFor(c)?.card?.category || null
}
function catOf(c) {
  const slug = catSlugOf(c)
  return slug ? problemKind(slug) : null
}

// 花費格式化:$0.35;不足 1 分錢顯示 <$0.01;0 或無則不顯示
function fmtCost(c) {
  const v = Number(c) || 0
  if (v <= 0) return ''
  return v < 0.01 ? '<$0.01' : '$' + v.toFixed(2)
}

// 時間線每則的左側標記
function evTag(e) {
  if (e.type === 'steer') return '🗣'
  if (e.type === 'system') return '📯'
  if (e.type === 'question') return '❓'
  if (e.type === 'step') return '›' // 冒險者動作(內容已含工具圖示)
  return `R${e.round || '?'}` // progress:回合結尾摘要
}
// 冒險者的完整提問/受阻說明(type='question')。從時間線抽出來,
// 用 Markdown 醒目呈現,解決「AI 反問卻只看到被截斷的一句話」的痛點。
const questionEvents = computed(() => events.value.filter((e) => e.type === 'question'))
const latestQuestion = computed(() => {
  const qs = questionEvents.value
  return qs.length ? qs[qs.length - 1] : null
})
// 時間線本身不再塞入長篇提問(改在下方獨立區塊完整顯示)
const timelineEvents = computed(() => events.value.filter((e) => e.type !== 'question'))

// ── 貼委託表單(兼「整張重新編輯」)──
const showForm = ref(false)
const editingId = ref(null) // null = 貼新委託;有值 = 編輯該委託(整張叫出來改)
const title = ref('')
const note = ref('')
const goal = ref('')
const formGuild = ref('') // guild path 或 '__custom__'
const formGuildCustom = ref('') // 自訂路徑(formGuild === '__custom__' 時生效)
const formDomain = ref('') // 領域公會(以「做的事」分類,如「金融公會」)
const formSelfFolder = ref(false) // 自建任務資料夾(並行派遣不互卡)
const formTaskMemory = ref(true) // 記憶特化:成果只存任務記憶,不寫 llm wiki
const effectiveCwd = computed(() =>
  formGuild.value === '__custom__' ? formGuildCustom.value.trim() : formGuild.value)
const formModel = ref('') // 冒險者位階(派遣模型)
const modelTouched = ref(false) // 使用者是否手動選過位階(選過就不再自動覆蓋)
const formSkin = ref('') // 呈現方式/職業:決定結果卡皮膚(空=自動 / 工程師=diff / 書記官=字卡)
// 呈現方式選項(只列有專屬皮膚的職業,其餘結果用一般文字)
const SKIN_OPTS = [
  { value: '', label: '🗡️ 一般(自動:有改 code 就用 diff)' },
  { value: '工程師', label: '⚙️ 工程師 — 程式碼 diff 卡' },
  { value: '機關師', label: '🔧 機關師 — 指令/服務卡(基礎設施)' },
  { value: '鑑定師', label: '🧪 鑑定師 — 查驗清單/裁決卡(QA)' },
  { value: '斥候', label: '🌿 斥候 — 出貨卡(commit/推送/部署)' },
  { value: '書記官', label: '📚 書記官 — 教學字卡' },
]

// 依標題+目標自動建議位階(使用者未手動選過才套用)
const rankHint = computed(() => suggestRank(`${title.value} ${goal.value}`))
watch([title, goal], () => {
  if (!modelTouched.value) formModel.value = rankHint.value.value
})

// 附圖:貼上(Ctrl+V)或選檔 → 上傳 → 收集 /uploads/... URL,顯示縮圖
const images = ref([])
const uploading = ref(false)
const imgErr = ref(null)

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result)
    r.onerror = reject
    r.readAsDataURL(file)
  })
}
async function addImageFiles(files) {
  const imgs = [...files].filter((f) => f.type.startsWith('image/'))
  if (!imgs.length) return
  imgErr.value = null
  uploading.value = true
  try {
    for (const f of imgs) {
      try {
        const url = await uploadImage(await fileToDataUrl(f))
        images.value.push(url)
      } catch (e) {
        imgErr.value = e?.response?.data?.detail || '圖片上傳失敗'
      }
    }
  } finally {
    uploading.value = false
  }
}
function onPaste(e) {
  const items = e.clipboardData?.items
  if (!items) return
  const files = [...items].filter((it) => it.kind === 'file').map((it) => it.getAsFile()).filter(Boolean)
  if (files.length) { e.preventDefault(); addImageFiles(files) }
}
function onPickFiles(e) {
  addImageFiles(e.target.files)
  e.target.value = '' // 允許重選同一張
}
function removeImage(i) { images.value.splice(i, 1) }

const formErr = ref(null)
const posting = ref(false)

// 把一張既有委託「整張叫出來」填回表單重新編輯(含附圖、位階、皮膚)
function openEdit(c) {
  editingId.value = c.id
  title.value = c.title || ''
  goal.value = c.goal || ''
  note.value = c.note || ''
  formModel.value = c.model || ''
  modelTouched.value = true // 編輯既有委託:沿用它的位階,別被自動建議覆蓋
  formSkin.value = c.klass || ''
  formDomain.value = c.domain || ''
  formSelfFolder.value = !!c.self_folder
  formTaskMemory.value = !!c.task_memory
  images.value = (c.images || []).slice()
  // 資料夾:比對既有公會路徑;對不上就用自訂路徑帶入
  const g = store.guilds.find((x) => x.path === c.cwd)
  if (c.cwd && !g) { formGuild.value = '__custom__'; formGuildCustom.value = c.cwd }
  else { formGuild.value = c.cwd || ''; formGuildCustom.value = '' }
  imgErr.value = null; formErr.value = null
  showForm.value = true
}

// dispatchNow=false 只貼布告欄 / 存檔;true 則貼完(存完)立即派遣
async function submit(dispatchNow) {
  formErr.value = null
  if (!title.value.trim()) { formErr.value = '請至少給個標題'; return }
  if (dispatchNow && !effectiveCwd.value) {
    formErr.value = '立即派遣需要先選資料夾(公會)'; return
  }
  posting.value = true
  try {
    const cwd = effectiveCwd.value
    const g = store.guilds.find((x) => x.path === cwd)
    const payload = {
      title: title.value,
      note: note.value || null,
      goal: goal.value || null,
      guild: g?.name || null,
      cwd: cwd || null,
      model: formModel.value || null,
      klass: formSkin.value || null,
      images: images.value.slice(),
      domain: formDomain.value.trim() || null,
      self_folder: formSelfFolder.value,
      task_memory: formTaskMemory.value,
    }
    let target // 建立或更新後的委託(含 id)
    if (editingId.value != null) {
      target = await store.updateCommission(editingId.value, payload)
      if (!target) { formErr.value = '更新委託失敗,請確認後端在線'; return }
    } else {
      target = await store.addCommission(payload)
      if (!target) { formErr.value = '貼委託失敗,請確認後端在線'; return }
    }
    if (dispatchNow) {
      const rank = rankOf(formModel.value)
      if (!store.confirmDispatch(`立即派遣${rank.short}冒險者執行「${title.value}」?\n`
        + `會實際跑 Claude、花 token,並可能修改 ${cwd} 內的檔案。`)) {
        // 已存檔 / 貼上布告欄,只是不立即派;關閉表單
        resetForm(); return
      }
      const err = await store.dispatch(target.id, cwd, formModel.value)
      if (err) { formErr.value = err; return }
    }
    resetForm()
  } finally {
    posting.value = false
  }
}
// 只隱藏表單、保留草稿(點視窗外 / 按 ✕ 關閉用)——避免打到一半被清空
function closeForm() { showForm.value = false }
function resetForm() {
  editingId.value = null
  title.value = ''; note.value = ''; goal.value = ''
  modelTouched.value = false; formModel.value = ''; formSkin.value = ''
  formDomain.value = ''; formSelfFolder.value = false; formTaskMemory.value = true
  formGuild.value = ''; formGuildCustom.value = ''
  images.value = []; imgErr.value = null
  formErr.value = null; showForm.value = false
}

// ── 派遣詳情(展開一張委託:進度時間線 + 中途指示)──
const expandedId = ref(null)
const events = ref([])
const steerText = ref('')
const goalEdit = ref('')
const editingGoal = ref(false)
const actionErr = ref(null)
let evTimer = null

async function refreshEvents() {
  if (expandedId.value == null) return
  try { events.value = await store.loadEvents(expandedId.value) } catch (e) { /* 靜默 */ }
}
function expand(c) {
  expandedId.value = c.id
  goalEdit.value = c.goal || c.title
  // 已設位階就沿用;未設則用自動建議(依委託內容)當預選
  dispatchModel.value = c.model || suggestRank(`${c.title} ${c.goal || ''}`).value
  editingGoal.value = false
  events.value = []
  refreshEvents()
}
function toggleExpand(c) {
  actionErr.value = null
  if (expandedId.value === c.id) { expandedId.value = null; return }
  expand(c)
}
watch(expandedId, (id) => {
  clearInterval(evTimer)
  evTimer = null
  if (id != null) evTimer = setInterval(refreshEvents, 4000)
})

// 執行中委託的即時進度:派遣鎖保證同時只有一張 working,
// 輪詢其最新一筆進度/步驟,顯示在收合的委託列上 —— 不必展開就知道冒險者在做什麼。
const liveLine = ref({}) // cid -> 最新一行文字
let liveTimer = null
async function refreshLive() {
  const working = props.commissions.find((c) => c.status === 'working')
  if (!working) { if (Object.keys(liveLine.value).length) liveLine.value = {}; return }
  if (expandedId.value === working.id) return // 展開中已有完整時間線,不重複抓
  try {
    const evs = await store.loadEvents(working.id) // 新→舊
    const latest = evs.find((e) => e.type === 'step' || e.type === 'progress')
    if (latest) liveLine.value = { [working.id]: latest.content }
  } catch (e) { /* 靜默 */ }
}
onMounted(() => { liveTimer = setInterval(refreshLive, 4000) })

// 委託由「執行中」轉為受阻/失敗時,若沒展開任何東西就自動展開它,
// 讓使用者立刻看到原因與「繼續做」按鈕(解決「被拒卻不知怎麼繼續」)
let _lastStatus = {}
watch(() => props.commissions, (list) => {
  for (const c of list) {
    const prev = _lastStatus[c.id]
    if (prev === 'working' && (c.status === 'blocked' || c.status === 'failed')
        && expandedId.value == null) {
      expand(c)
    }
    // 剛做完:標記為「剛完成」,報告會就地展開顯示(不收合進歷史)
    if (prev === 'working' && c.status === 'done') {
      justDone.value.add(c.id)
    }
  }
  _lastStatus = Object.fromEntries(list.map((c) => [c.id, c.status]))
}, { deep: true, immediate: true })

const expanded = computed(() =>
  props.commissions.find((c) => c.id === expandedId.value) || null)

// 派遣需要工作資料夾:未設定時先讓使用者選公會
const dispatchCwd = ref('')
const dispatchModel = ref('') // 本次派遣位階;展開時帶入委託現值
async function doDispatch(c) {
  actionErr.value = null
  const cwd = c.cwd || dispatchCwd.value
  if (!cwd) { actionErr.value = '請先選擇要在哪個公會(資料夾)執行'; return }
  const rank = rankOf(dispatchModel.value)
  if (!store.confirmDispatch(`派遣${rank.short}冒險者執行「${c.title}」?\n會實際跑 Claude、花 token,並可能修改 ${cwd} 內的檔案。`)) return
  actionErr.value = await store.dispatch(c.id, cwd, dispatchModel.value)
  refreshEvents()
}
async function doEnqueue(c) {
  actionErr.value = null
  const cwd = c.cwd || dispatchCwd.value
  if (!cwd) { actionErr.value = '請先選擇要在哪個公會(資料夾)執行'; return }
  actionErr.value = await store.enqueue(c.id, cwd, dispatchModel.value)
  refreshEvents()
}
async function doSteer(c) {
  const t = steerText.value.trim()
  if (!t) return
  actionErr.value = await store.steer(c.id, t)
  if (!actionErr.value) steerText.value = ''
  refreshEvents()
}
async function saveGoal(c) {
  await store.updateGoal(c.id, goalEdit.value.trim() || c.title)
  editingGoal.value = false
  refreshEvents()
}

// ── 冒險者提問選單(BLOCKED 時 AskUserQuestion 的結構化選項)──
// 存成 type='options' 事件(content=JSON)。渲染成可點按鈕,點選後組成答覆送調整指示並續跑。
// 取 id 最大(最新)那筆,且 round 需對齊目前回合 —— 否則會顯示上次派遣殘留的舊選項。
const latestOptions = computed(() => {
  const opts = events.value.filter((e) => e.type === 'options')
  if (!opts.length) return null
  const ev = opts.reduce((a, b) => (b.id > a.id ? b : a))
  try {
    const data = JSON.parse(ev.content)
    return { questions: Array.isArray(data.questions) ? data.questions : [], round: ev.round }
  } catch { return null }
})
const showOptions = computed(() =>
  !!latestOptions.value && !!expanded.value
  && latestOptions.value.round === expanded.value.rounds
  && latestOptions.value.questions.length > 0)
const picked = ref({}) // 題index → 已選 label 陣列
function isPicked(qi, label) { return (picked.value[qi] || []).includes(label) }
function pick(qi, label, multi) {
  const cur = picked.value[qi] || []
  const next = multi
    ? (cur.includes(label) ? cur.filter((x) => x !== label) : [...cur, label])
    : [label]
  picked.value = { ...picked.value, [qi]: next }
}
// 清掉選擇(切換展開不同委託時,避免選擇串到別張)
watch(expandedId, () => { picked.value = {} })
async function answerOptions(c) {
  const qs = latestOptions.value?.questions || []
  const lines = []
  qs.forEach((q, i) => {
    const sel = picked.value[i]
    if (sel && sel.length) lines.push(`「${q.question}」→ ${sel.join('、')}`)
  })
  if (!lines.length) { actionErr.value = '請先點選至少一個選項'; return }
  actionErr.value = await store.steer(c.id, '回答你的提問:\n' + lines.join('\n'))
  if (actionErr.value) return
  picked.value = {}
  await doDispatch(c) // 送出答覆後直接續跑同一位冒險者(走既有派遣確認)
}

// 點右側通知 → 展開對應委託並捲到畫面內(讓「完成 / 受阻」一鍵直達內容)
watch(() => store.focusCid, async (cid) => {
  if (cid == null) return
  const c = props.commissions.find((x) => x.id === cid)
  if (c) {
    expand(c)
    await nextTick()
    document.querySelector(`[data-cid="${cid}"]`)?.scrollIntoView({
      behavior: 'smooth', block: 'center',
    })
  }
  store.focusCid = null // 消費掉,避免重複觸發
})

// ── 語音輸入 ──
const voiceTarget = ref(null) // 'title' | 'goal' | 'note' | 'steer' | null
const voiceInterim = ref('')  // 即時辨識(未定稿,顯示為提示文字)
const voiceErr = ref(null)
let _recog = null

const voiceSupported = !!(window.SpeechRecognition || window.webkitSpeechRecognition)

function startVoice(target) {
  if (voiceTarget.value === target) { stopVoice(); return }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SR) { voiceErr.value = '此瀏覽器不支援語音輸入(請使用 Chrome 或 Edge)'; return }
  stopVoice()
  voiceTarget.value = target
  voiceInterim.value = ''
  voiceErr.value = null
  _recog = new SR()
  _recog.lang = 'zh-TW'
  _recog.interimResults = true
  _recog.continuous = true
  _recog.onresult = (event) => {
    let interim = ''
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const t = event.results[i][0].transcript
      if (event.results[i].isFinal) {
        if (target === 'title') title.value += t
        else if (target === 'goal') goal.value += t
        else if (target === 'note') note.value += t
        else if (target === 'steer') steerText.value += t
      } else {
        interim += t
      }
    }
    voiceInterim.value = interim
  }
  _recog.onerror = (e) => {
    voiceErr.value = e.error === 'not-allowed'
      ? '麥克風權限被拒,請在瀏覽器設定允許使用麥克風'
      : `語音辨識錯誤:${e.error}`
    voiceTarget.value = null
    voiceInterim.value = ''
  }
  _recog.onend = () => {
    if (voiceTarget.value === target) voiceTarget.value = null
    voiceInterim.value = ''
  }
  _recog.start()
}

function stopVoice() {
  if (_recog) { try { _recog.stop() } catch (_) {} ; _recog = null }
  voiceTarget.value = null
  voiceInterim.value = ''
}

// 關閉表單時停止錄音
watch(showForm, (v) => { if (!v) stopVoice() })
onUnmounted(() => { clearInterval(evTimer); clearInterval(liveTimer); stopVoice() })
</script>

<template>
  <div class="board">
    <div class="board__title">
      📌 委託布告欄
      <button class="board__add" @click="showForm ? closeForm() : (showForm = true)">
        {{ showForm ? '✕' : '＋ 貼委託' }}
      </button>
    </div>

    <!-- 派工對話框(大表單,好好寫規格) -->
    <div v-if="showForm" class="dpm-backdrop" @click.self="closeForm()">
      <div class="dpm">
        <div class="dpm__head">
          <h3>{{ editingId != null ? '✏️ 編輯委託(整張重改)' : '📋 派一張委託' }}</h3>
          <button class="dpm__close" @click="closeForm()" title="關閉(保留草稿)">✕</button>
        </div>

        <label class="dpm__label">標題</label>
        <div class="dpm__voice-row">
          <input class="dpm__input" v-model="title" placeholder="想做的事(一句話)" />
          <button v-if="voiceSupported" class="dpm__mic"
                  :class="{ 'is-recording': voiceTarget === 'title' }"
                  @click="startVoice('title')"
                  :title="voiceTarget === 'title' ? '點停止錄音' : '語音輸入標題'">🎤</button>
        </div>
        <div v-if="voiceTarget === 'title' && voiceInterim" class="dpm__interim">{{ voiceInterim }}</div>

        <label class="dpm__label">
          🎯 目標 <span class="dim">— 冒險者會照這段動手,寫清楚範圍與驗收標準</span>
        </label>
        <div class="dpm__voice-row dpm__voice-row--area">
          <textarea class="dpm__area" v-model="goal" rows="6" @paste="onPaste"
            placeholder="具體描述要做什麼、涵蓋範圍、完成的標準…&#10;例:重構 X 模組,把 Y 邏輯抽成獨立函式,保持既有測試綠燈,並補一個新測試。&#10;(可直接 Ctrl+V 貼上截圖)"></textarea>
          <button v-if="voiceSupported" class="dpm__mic"
                  :class="{ 'is-recording': voiceTarget === 'goal' }"
                  @click="startVoice('goal')"
                  :title="voiceTarget === 'goal' ? '點停止錄音' : '語音輸入目標'">🎤</button>
        </div>
        <div v-if="voiceTarget === 'goal' && voiceInterim" class="dpm__interim">{{ voiceInterim }}</div>

        <div class="dpm__row">
          <div class="dpm__col">
            <label class="dpm__label">🏰 資料夾(公會)</label>
            <select class="dpm__input" v-model="formGuild">
              <option value="">(不指定,僅貼布告欄)</option>
              <option v-for="g in store.guilds" :key="g.dir_key" :value="g.path">
                🏰 {{ g.name }}
              </option>
              <option value="__custom__">📂 自訂路徑…</option>
            </select>
            <input v-if="formGuild === '__custom__'" class="dpm__input dpm__input--custom"
                   v-model="formGuildCustom" placeholder="例：D:\projects\my-project" />
          </div>
          <div class="dpm__col">
            <label class="dpm__label">🎖 位階</label>
            <ModelPicker v-model="formModel" @update:model-value="modelTouched = true" />
          </div>
        </div>
        <div v-if="rankHint.reason && !modelTouched" class="board__hint">
          🎖 建議 {{ rankOf(rankHint.value).short }} {{ rankOf(rankHint.value).label }}
          <span class="dim">— {{ rankHint.reason }}</span>
        </div>

        <label class="dpm__label">
          🏷 領域公會 <span class="dim">— 以「做的事」分類(如金融公會),同領域的任務與報告歸在一起</span>
        </label>
        <input class="dpm__input" v-model="formDomain" list="domain-list"
               placeholder="例:金融公會、股市分析、英文學習…(可留空)" />
        <datalist id="domain-list">
          <option v-for="d in store.domains" :key="d" :value="d" />
        </datalist>
        <label class="dpm__selffolder" title="派遣時在專屬子資料夾執行,讓並行任務不共用同一資料夾互卡">
          <input type="checkbox" v-model="formSelfFolder" />
          📁 讓冒險者自建任務資料夾 <span class="dim">(並行派遣不互卡;下載內容放這裡)</span>
        </label>
        <label class="dpm__selffolder" title="勾選:成果/心得只存在這張委託的任務記憶(續派自動帶上),不寫入 llm wiki">
          <input type="checkbox" v-model="formTaskMemory" />
          🧠 記憶特化在任務內 <span class="dim">(不寫入 llm wiki;回報仍可在櫃台搜尋)</span>
        </label>

        <label class="dpm__label">
          🎨 呈現方式 <span class="dim">— 完成後結果要用哪種皮膚回來(決定冒險者職業)</span>
        </label>
        <select class="dpm__input" v-model="formSkin">
          <option v-for="s in SKIN_OPTS" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>

        <label class="dpm__label">備註 <span class="dim">(可省略)</span></label>
        <div class="dpm__voice-row dpm__voice-row--area">
          <textarea class="dpm__area" v-model="note" rows="2" @paste="onPaste"
            placeholder="補充說明、參考連結…(可 Ctrl+V 貼圖)"></textarea>
          <button v-if="voiceSupported" class="dpm__mic"
                  :class="{ 'is-recording': voiceTarget === 'note' }"
                  @click="startVoice('note')"
                  :title="voiceTarget === 'note' ? '點停止錄音' : '語音輸入備註'">🎤</button>
        </div>
        <div v-if="voiceTarget === 'note' && voiceInterim" class="dpm__interim">{{ voiceInterim }}</div>
        <div v-if="voiceErr" class="dispatch__err">⚠️ {{ voiceErr }}</div>

        <label class="dpm__label">
          🖼️ 附圖 <span class="dim">— Ctrl+V 貼上截圖,或選檔;派遣的冒險者會讀圖理解需求</span>
        </label>
        <div class="dpm__imgs">
          <div v-for="(url, i) in images" :key="url" class="dpm__thumb">
            <img :src="url" alt="附圖" />
            <button class="dpm__thumb-x" @click="removeImage(i)" title="移除">✕</button>
          </div>
          <label class="dpm__imgadd" :class="{ 'is-busy': uploading }">
            <input type="file" accept="image/*" multiple @change="onPickFiles" hidden />
            <span v-if="uploading">上傳中…</span>
            <span v-else>＋<br>貼圖/選檔</span>
          </label>
        </div>
        <div v-if="imgErr" class="dispatch__err">⚠️ {{ imgErr }}</div>

        <div v-if="formErr" class="dispatch__err">⚠️ {{ formErr }}</div>
        <label class="dpm__skipconfirm" title="派遣會實際改檔並花 token;勾選後這類操作不再跳確認">
          <input type="checkbox" :checked="store.skipDispatchConfirm"
                 @change="store.setSkipDispatchConfirm($event.target.checked)" />
          派遣前不再確認 <span class="dim">(可隨時回來取消勾選)</span>
        </label>
        <div class="dpm__actions">
          <button class="btn-ghost" @click="resetForm()" title="清空並關閉">取消(清空)</button>
          <button class="btn-ghost" :disabled="posting" @click="submit(false)">
            {{ editingId != null ? '💾 儲存修改' : '📌 貼上布告欄' }}
          </button>
          <button class="btn-primary" :disabled="posting || !effectiveCwd"
                  :title="!effectiveCwd ? '需先選資料夾才能立即派遣' : ''"
                  @click="submit(true)">
            {{ editingId != null ? '💾 儲存並派遣' : '🚀 貼上並立即派遣' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 領域公會切換條:以「做的事」分類,點一下只看該領域 -->
    <div v-if="domainSummary.list.length" class="domainbar">
      <button class="domainbar__chip" :class="{ 'is-on': domainFilter === null }"
              @click="domainFilter = null">🏛 全部</button>
      <button v-for="d in domainSummary.list" :key="d.name"
              class="domainbar__chip" :class="{ 'is-on': domainFilter === d.name }"
              @click="domainFilter = domainFilter === d.name ? null : d.name">
        🏷 {{ d.name }} <b>{{ d.n }}</b>
      </button>
      <button v-if="domainSummary.none" class="domainbar__chip"
              :class="{ 'is-on': domainFilter === '__none__' }"
              @click="domainFilter = domainFilter === '__none__' ? null : '__none__'">
        未分領域 <b>{{ domainSummary.none }}</b>
      </button>
    </div>

    <div class="board__pins">
      <!-- 派遣中 / 受阻 / 失敗 -->
      <template v-for="c in runningList" :key="'r' + c.id">
        <button class="quest quest--running" :class="'quest--' + c.status"
                :data-cid="c.id"
                @click="toggleExpand(c)"
                :title="c.goal || c.title">
          <span class="quest__emoji">{{ COMMISSION_META[c.status].emoji }}</span>
          <span class="quest__text">{{ c.title }}</span>
          <span v-if="c.domain" class="quest__domain" title="領域公會">🏷 {{ c.domain }}</span>
          <span class="quest__guild">
            {{ rankOf(c.model).short }} {{ COMMISSION_META[c.status].label }}
            <template v-if="c.status === 'queued' && store.queuePos[c.id]">
              (第 {{ store.queuePos[c.id] }} 位)
            </template>
            <template v-if="c.rounds"> · 第{{ c.rounds }}回合</template>
            <span v-if="fmtCost(c.cost)" class="quest__cost">💰 {{ fmtCost(c.cost) }}</span>
          </span>
          <span v-if="c.status === 'failed'" class="quest__x"
                @click.stop="store.removeCommission(c.id)" title="刪除委託">🗑</span>
        </button>
        <!-- 收合時的即時進度:一眼看出冒險者現在在幹什麼 -->
        <div v-if="c.status === 'working' && expandedId !== c.id && liveLine[c.id]"
             class="quest__live" @click="toggleExpand(c)">
          <span class="quest__live-dot"></span>{{ liveLine[c.id] }}
        </div>
        <div v-if="expandedId === c.id" class="dispatch"
             :class="{ 'dispatch--float': poppedId === c.id }"
             :style="poppedId === c.id ? popStyle() : {}">
          <div class="dispatch__tools" :class="{ 'dispatch__float-bar': poppedId === c.id }"
               @pointerdown="poppedId === c.id ? popDragStart($event, '.dispatch--float') : null">
            <template v-if="poppedId === c.id">
              <span class="dispatch__float-ttl">🗂 {{ c.title }}</span>
              <span class="dispatch__float-hint">⠿ 可拖曳 · 右下角可拉寬</span>
            </template>
            <button class="dispatch__pop" @click.stop="togglePop(c.id)"
                    :title="poppedId === c.id ? '收回內嵌' : '彈出成可拖曳/可拉寬的浮動視窗'">
              {{ poppedId === c.id ? '✕ 收回' : '⤢ 彈出' }}
            </button>
          </div>
          <!-- 任務記憶:這張委託自己累積的成果/心得(續派自動帶上,不必撈全域 wiki)-->
          <details v-if="c.memory" class="dispatch__mem">
            <summary>🧠 任務記憶 <span class="dim">{{ c.task_memory ? '· 特化(不寫 wiki)' : '· 也寫入 wiki' }}</span></summary>
            <pre class="dispatch__mem-body">{{ c.memory }}</pre>
          </details>
          <!-- 目標(可中途調整)-->
          <div class="dispatch__goal">
            <template v-if="editingGoal">
              <input v-model="goalEdit" @keyup.enter="saveGoal(c)" />
              <button class="btn-primary" @click="saveGoal(c)">🎯 更新目標</button>
            </template>
            <template v-else>
              <span class="dispatch__goal-i">🎯</span>
              <Markdown :text="c.goal || c.title" class="dispatch__goal-md" />
              <button class="btn-ghost" @click="editingGoal = true">✏️ 調整</button>
            </template>
          </div>
          <div v-if="c.images?.length" class="dispatch__imgs">
            <a v-for="url in c.images" :key="url" :href="url" target="_blank">
              <img :src="url" alt="委託附圖" />
            </a>
          </div>
          <!-- 進度時間線 -->
          <div class="dispatch__events">
            <div v-for="e in timelineEvents" :key="e.id" class="dispatch__ev"
                 :class="'dispatch__ev--' + e.type">
              <span class="dispatch__ev-tag">{{ evTag(e) }}</span>
              <span>{{ e.content }}</span>
            </div>
            <div v-if="!timelineEvents.length" class="board__empty">尚無進度回報…</div>
          </div>
          <!-- 冒險者的完整提問 / 受阻說明:醒目、可讀完整內容(Markdown)-->
          <div v-if="latestQuestion" class="dispatch__question">
            <div class="dispatch__question-h">❓ 冒險者的提問 / 說明</div>
            <Markdown :text="latestQuestion.content" class="dispatch__question-body" />
          </div>
          <!-- 冒險者提問選單:點選項=送出答覆並續跑(免打字) -->
          <div v-if="showOptions" class="dispatch__opts">
            <div class="dispatch__opts-h">🗳 點選回答 <span class="dim">— 送出後自動繼續</span></div>
            <div v-for="(q, qi) in latestOptions.questions" :key="qi" class="dispatch__optq">
              <div class="dispatch__optq-t">
                <span v-if="q.header" class="dispatch__optq-tag">{{ q.header }}</span>
                {{ q.question }}
                <span v-if="q.multiSelect" class="dim">(可複選)</span>
              </div>
              <div class="dispatch__optbtns">
                <button v-for="(o, oi) in q.options" :key="oi"
                        class="dispatch__opt" :class="{ 'is-on': isPicked(qi, o.label) }"
                        @click="pick(qi, o.label, q.multiSelect)"
                        :title="o.description || ''">
                  {{ o.label }}
                </button>
              </div>
            </div>
            <button class="btn-primary dispatch__opt-send" @click="answerOptions(c)">
              ✅ 送出答覆並繼續
            </button>
          </div>
          <!-- 這次派遣已產出的櫃台回報(閉環:委託與其回報顯示在一起)-->
          <CommissionReport v-if="reportFor(c)" :report="reportFor(c)" judge />
          <!-- 受阻 / 失敗:明確提示需要你處理 -->
          <div v-if="c.status === 'blocked' || c.status === 'failed'" class="dispatch__attn">
            <template v-if="c.status === 'blocked'">
              <template v-if="latestQuestion">
                🙋 <b>冒險者在問你問題</b>(見上方「冒險者的提問」)。
                在下方回答,再按「▶️ 繼續做」他就會照你的答覆接著做。
              </template>
              <template v-else>
                ⏸ <b>做到一半暫停了</b>(通常是做完一個段落、或到了回合上限 —— <b>不是失敗</b>)。
                直接按「▶️ 繼續做」它就會接著做完;想補充方向也可在下方輸入。
              </template>
            </template>
            <template v-else>
              💥 <b>這回合失敗了</b>(見上方原因)。調整後按「▶️ 繼續做」重試,
              若是資源問題可稍等或重開機。
            </template>
          </div>
          <!-- 中途指示 / 補充指示 -->
          <div class="dispatch__steer">
            <input v-model="steerText"
                   :placeholder="c.status === 'blocked' || c.status === 'failed'
                     ? '補充指示 / 回答她的疑問(按「繼續做」注入)'
                     : '下中途指示(下一回合注入)'"
                   @keyup.enter="doSteer(c)" />
            <button v-if="voiceSupported" class="dpm__mic dispatch__mic"
                    :class="{ 'is-recording': voiceTarget === 'steer' }"
                    @click="startVoice('steer')"
                    :title="voiceTarget === 'steer' ? '點停止' : '語音輸入指示'">🎤</button>
            <button class="btn-primary" @click="doSteer(c)">送出</button>
          </div>
          <div v-if="voiceTarget === 'steer' && voiceInterim" class="dpm__interim dpm__interim--steer">{{ voiceInterim }}</div>
          <div class="dispatch__actions">
            <button v-if="c.status === 'working'" class="btn-ghost"
                    @click="store.stopDispatch(c.id)">⏹ 停止派遣</button>
            <button v-else-if="c.status === 'queued'" class="btn-ghost"
                    @click="store.stopDispatch(c.id)">🎫 移出佇列</button>
            <template v-else>
              <ModelPicker v-model="dispatchModel" title="冒險者位階" />
              <button class="btn-primary" @click="doDispatch(c)">
                ▶️ 繼續做(續同一位冒險者)
              </button>
              <button class="btn-ghost" @click="doEnqueue(c)">➕ 排隊</button>
            </template>
          </div>
          <div v-if="actionErr" class="dispatch__err">⚠️ {{ actionErr }}</div>
        </div>
      </template>

      <!-- 招募中 -->
      <template v-for="c in openList" :key="'c' + c.id">
        <button class="quest quest--commission" @click="toggleExpand(c)"
                :title="c.note || c.goal || '點開看派遣選項'">
          <span class="quest__emoji">📜</span>
          <span class="quest__text">{{ c.title }}</span>
          <span v-if="c.domain" class="quest__domain" title="領域公會">🏷 {{ c.domain }}</span>
          <span class="quest__guild">招募中</span>
          <span class="quest__edit" @click.stop="openEdit(c)" title="整張叫出來重新編輯(含附圖)">✏️</span>
          <span class="quest__x" @click.stop="store.removeCommission(c.id)" title="刪除委託">🗑</span>
        </button>
        <div v-if="expandedId === c.id" class="dispatch"
             :class="{ 'dispatch--float': poppedId === c.id }"
             :style="poppedId === c.id ? popStyle() : {}">
          <div class="dispatch__tools" :class="{ 'dispatch__float-bar': poppedId === c.id }"
               @pointerdown="poppedId === c.id ? popDragStart($event, '.dispatch--float') : null">
            <template v-if="poppedId === c.id">
              <span class="dispatch__float-ttl">🗂 {{ c.title }}</span>
              <span class="dispatch__float-hint">⠿ 可拖曳 · 右下角可拉寬</span>
            </template>
            <button class="dispatch__pop" @click.stop="togglePop(c.id)"
                    :title="poppedId === c.id ? '收回內嵌' : '彈出成可拖曳/可拉寬的浮動視窗'">
              {{ poppedId === c.id ? '✕ 收回' : '⤢ 彈出' }}
            </button>
          </div>
          <!-- 任務記憶:這張委託自己累積的成果/心得(續派自動帶上,不必撈全域 wiki)-->
          <details v-if="c.memory" class="dispatch__mem">
            <summary>🧠 任務記憶 <span class="dim">{{ c.task_memory ? '· 特化(不寫 wiki)' : '· 也寫入 wiki' }}</span></summary>
            <pre class="dispatch__mem-body">{{ c.memory }}</pre>
          </details>
          <div class="dispatch__goal">
            <template v-if="editingGoal">
              <input v-model="goalEdit" @keyup.enter="saveGoal(c)" />
              <button class="btn-primary" @click="saveGoal(c)">🎯 更新目標</button>
            </template>
            <template v-else>
              <span class="dispatch__goal-i">🎯</span>
              <Markdown :text="c.goal || c.title" class="dispatch__goal-md" />
              <button class="btn-ghost" @click="editingGoal = true">✏️ 調整</button>
            </template>
          </div>
          <div v-if="c.images?.length" class="dispatch__imgs">
            <a v-for="url in c.images" :key="url" :href="url" target="_blank">
              <img :src="url" alt="委託附圖" />
            </a>
          </div>
          <div v-if="events.length" class="dispatch__events">
            <div v-for="e in events" :key="e.id" class="dispatch__ev"
                 :class="'dispatch__ev--' + e.type">
              <span class="dispatch__ev-tag">{{ evTag(e) }}</span>
              <span>{{ e.content }}</span>
            </div>
          </div>
          <div class="dispatch__actions">
            <select v-if="!c.cwd" v-model="dispatchCwd">
              <option value="">選擇公會(工作資料夾)…</option>
              <option v-for="g in store.guilds" :key="g.dir_key" :value="g.path">
                🏰 {{ g.name }}
              </option>
            </select>
            <span v-else class="dispatch__cwd" :title="c.cwd">🏰 {{ c.guild || c.cwd }}</span>
            <ModelPicker v-model="dispatchModel" />
            <button class="btn-primary" @click="doDispatch(c)">🚀 派遣冒險者</button>
            <button class="btn-ghost" @click="doEnqueue(c)"
                    title="加入派遣佇列,有空位就自動執行(不同資料夾可並行;同資料夾依序)">➕ 排隊</button>
            <button class="btn-ghost" @click="store.toggleCommission(c)">✅ 標記完成</button>
          </div>
          <div v-if="actionErr" class="dispatch__err">⚠️ {{ actionErr }}</div>
        </div>
      </template>

      <!-- 剛完成:留在原地、報告就地顯示,不用去已完成分區翻 -->
      <template v-for="c in freshDone" :key="'fd' + c.id">
        <div class="quest quest--done quest--fresh" :title="c.goal || c.title">
          <span class="quest__emoji">🎉</span>
          <span class="quest__text">{{ c.title }}</span>
          <span v-if="doerOf(c)" class="quest__doer"
                :title="`由 ${doerOf(c).guild} 公會的${doerOf(c).klass}完成`">
            {{ doerOf(c).icon }} {{ doerOf(c).klass }}
          </span>
          <span v-if="catOf(c)" class="quest__cat"
                :style="{ '--kc': catOf(c).color }" :title="'解決的問題類型:' + catOf(c).label">
            {{ catOf(c).icon }} {{ catOf(c).label }}
          </span>
          <span class="quest__guild">
            {{ rankOf(c.model).short }} 剛完成<template v-if="c.rounds"> · {{ c.rounds }}回合</template>
            <span v-if="fmtCost(c.cost)" class="quest__cost">💰 {{ fmtCost(c.cost) }}</span>
          </span>
          <button class="quest__dismiss" @click="dismissDone(c)" title="收起到已完成歷史">✓ 收起</button>
        </div>
        <div class="dispatch"
             :class="{ 'dispatch--float': poppedId === c.id }"
             :style="poppedId === c.id ? popStyle() : {}">
          <div class="dispatch__tools" :class="{ 'dispatch__float-bar': poppedId === c.id }"
               @pointerdown="poppedId === c.id ? popDragStart($event, '.dispatch--float') : null">
            <template v-if="poppedId === c.id">
              <span class="dispatch__float-ttl">🗂 {{ c.title }}</span>
              <span class="dispatch__float-hint">⠿ 可拖曳 · 右下角可拉寬</span>
            </template>
            <button class="dispatch__pop" @click.stop="togglePop(c.id)"
                    :title="poppedId === c.id ? '收回內嵌' : '彈出成可拖曳/可拉寬的浮動視窗'">
              {{ poppedId === c.id ? '✕ 收回' : '⤢ 彈出' }}
            </button>
          </div>
          <!-- 任務記憶:這張委託自己累積的成果/心得(續派自動帶上,不必撈全域 wiki)-->
          <details v-if="c.memory" class="dispatch__mem">
            <summary>🧠 任務記憶 <span class="dim">{{ c.task_memory ? '· 特化(不寫 wiki)' : '· 也寫入 wiki' }}</span></summary>
            <pre class="dispatch__mem-body">{{ c.memory }}</pre>
          </details>
          <div class="dispatch__goal">
            <span class="dispatch__goal-i">🎯</span>
            <Markdown :text="c.goal || c.title" class="dispatch__goal-md" />
          </div>
          <CommissionReport v-if="reportFor(c)" :report="reportFor(c)" judge />
          <div v-else class="board__empty">📨 冒險者已完成,櫃台正在歸檔報告…</div>
        </div>
      </template>

      <!-- 已完成歷史(可展開看櫃台回報;預設收合避免塞滿布告欄)-->
      <button v-if="oldDoneAll.length" class="board__fold" @click="showDone = !showDone">
        <span class="board__fold-caret">{{ showDone ? '▾' : '▸' }}</span>
        ✅ 已完成 <span class="dim">({{ oldDoneAll.length }})</span>
      </button>
      <template v-if="showDone">
      <!-- 分類統計條:已完成項目依「解決了哪類問題」歸類,點一下只看那類 -->
      <div v-if="doneCategorySummary.length" class="donecat">
        <button class="donecat__chip" :class="{ 'is-on': catFilter === null }"
                @click="catFilter = null">全部 ({{ oldDoneAll.length }})</button>
        <button v-for="g in doneCategorySummary" :key="g.slug"
                class="donecat__chip" :class="{ 'is-on': catFilter === g.slug }"
                :style="{ '--kc': g.meta.color }"
                @click="toggleCatFilter(g.slug)"
                :title="'只看「' + g.meta.label + '」類的委託'">
          {{ g.meta.icon }} {{ g.meta.label }} <b>{{ g.n }}</b>
        </button>
        <span v-if="uncategorizedDone" class="donecat__uncat"
              :title="'有回報但沒標問題類型(舊卡 / 教學卡)'">未分類 {{ uncategorizedDone }}</span>
      </div>
      <!-- 依完成時間分區:今天 / 近一週 / 更早(各自可收合)-->
      <template v-for="bk in DONE_BUCKETS" :key="bk.key">
      <button v-if="doneGroups[bk.key].length" class="board__fold board__fold--bucket"
              @click="toggleBucket(bk.key)">
        <span class="board__fold-caret">{{ openBuckets.has(bk.key) ? '▾' : '▸' }}</span>
        {{ bk.icon }} {{ bk.label }} <span class="dim">({{ doneGroups[bk.key].length }})</span>
      </button>
      <template v-if="openBuckets.has(bk.key)">
      <template v-for="c in doneGroups[bk.key]" :key="'cd' + c.id">
        <button class="quest quest--done" @click="toggleExpand(c)"
                :title="reportFor(c) ? '點開看櫃台回報' : '點開看詳情'">
          <span class="quest__emoji">✅</span>
          <span class="quest__text">{{ c.title }}</span>
          <span v-if="doerOf(c)" class="quest__doer"
                :title="`由 ${doerOf(c).guild} 公會的${doerOf(c).klass}完成`">
            {{ doerOf(c).icon }} {{ doerOf(c).klass }}
          </span>
          <span v-if="catOf(c)" class="quest__cat"
                :style="{ '--kc': catOf(c).color }" :title="'解決的問題類型:' + catOf(c).label">
            {{ catOf(c).icon }} {{ catOf(c).label }}
          </span>
          <span class="quest__guild" v-if="c.model || c.rounds || c.cost || reportFor(c)">
            <template v-if="c.model">{{ rankOf(c.model).short }} </template>
            <template v-if="c.rounds">🤖 {{ c.rounds }}回合</template>
            <span v-if="reportFor(c)" title="已有櫃台回報">📄</span>
            <span v-if="fmtCost(c.cost)" class="quest__cost">💰 {{ fmtCost(c.cost) }}</span>
          </span>
          <span class="quest__x" @click.stop="store.removeCommission(c.id)" title="刪除委託">🗑</span>
        </button>
        <div v-if="expandedId === c.id" class="dispatch"
             :class="{ 'dispatch--float': poppedId === c.id }"
             :style="poppedId === c.id ? popStyle() : {}">
          <div class="dispatch__tools" :class="{ 'dispatch__float-bar': poppedId === c.id }"
               @pointerdown="poppedId === c.id ? popDragStart($event, '.dispatch--float') : null">
            <template v-if="poppedId === c.id">
              <span class="dispatch__float-ttl">🗂 {{ c.title }}</span>
              <span class="dispatch__float-hint">⠿ 可拖曳 · 右下角可拉寬</span>
            </template>
            <button class="dispatch__pop" @click.stop="togglePop(c.id)"
                    :title="poppedId === c.id ? '收回內嵌' : '彈出成可拖曳/可拉寬的浮動視窗'">
              {{ poppedId === c.id ? '✕ 收回' : '⤢ 彈出' }}
            </button>
          </div>
          <!-- 任務記憶:這張委託自己累積的成果/心得(續派自動帶上,不必撈全域 wiki)-->
          <details v-if="c.memory" class="dispatch__mem">
            <summary>🧠 任務記憶 <span class="dim">{{ c.task_memory ? '· 特化(不寫 wiki)' : '· 也寫入 wiki' }}</span></summary>
            <pre class="dispatch__mem-body">{{ c.memory }}</pre>
          </details>
          <div class="dispatch__goal">
            <span class="dispatch__goal-i">🎯</span>
            <Markdown :text="c.goal || c.title" class="dispatch__goal-md" />
          </div>
          <CommissionReport v-if="reportFor(c)" :report="reportFor(c)" judge />
          <div v-else class="board__empty">這張委託沒有派遣回報(手動標記完成的)。</div>
          <div class="dispatch__actions">
            <button class="btn-ghost" @click="store.toggleCommission(c)">↩️ 重新招募</button>
            <button class="btn-ghost" @click="openEdit(c)" title="整張叫出來重新編輯再派">✏️ 編輯整張</button>
          </div>
        </div>
      </template>
      </template>
      </template><!-- /bucket -->
      </template><!-- /showDone -->

      <!-- session 自動衍生:進行中(已被委託系統追蹤的 session 已過濾)-->
      <template v-if="sessionQuests.length">
        <button class="board__fold" @click="showSessions = !showSessions">
          <span class="board__fold-caret">{{ showSessions ? '▾' : '▸' }}</span>
          ⚔️ 其他進行中 <span class="dim">({{ sessionQuests.length }})</span>
        </button>
        <template v-if="showSessions">
          <button
            v-for="q in sessionQuests"
            :key="q.adventurer_id"
            class="quest"
            :style="{ '--qc': statusMeta(q.status).dot }"
            @click="emit('open', q.adventurer_id)"
            :title="`${q.guild} 公會 · ${relativeTime(q.last_active)}`"
          >
            <span class="quest__emoji">{{ statusMeta(q.status).emoji }}</span>
            <span class="quest__text">{{ q.title }}</span>
            <span class="quest__guild">{{ q.guild }}</span>
          </button>
        </template>
      </template>

      <div v-if="openList.length === 0 && runningList.length === 0 && doneList.length === 0 && sessionQuests.length === 0"
           class="board__empty">
        還沒有委託。點右上「＋ 貼委託」貼一張你想做的事。
      </div>
    </div>
  </div>
</template>
