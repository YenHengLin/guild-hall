<script setup>
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useGuildStore } from '../stores/guild'
import { statusMeta, relativeTime, RANKS, rankOf, suggestRank } from '../labels'
import { uploadImage } from '../api'
import CommissionReport from './CommissionReport.vue'
import Markdown from './Markdown.vue'

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
const openList = computed(() => props.commissions.filter((c) => c.status === 'open'))
const runningList = computed(() =>
  props.commissions.filter((c) => ['working', 'queued', 'blocked', 'failed'].includes(c.status)))
const doneList = computed(() => props.commissions.filter((c) => c.status === 'done'))
// 已完成會累積,預設收合保持布告欄清爽;點分區標題展開
const showDone = ref(false)
// 其他進行中的冒險(非委託系統):有 running commissions 時預設折疊
const showSessions = ref(false)
// 本次剛完成(working→done)的委託:留在原地展開、報告就地顯示,不必去已完成分區翻。
// 使用者按「收起」後才移進下方收合的歷史。
const justDone = ref(new Set())
const freshDone = computed(() => doneList.value.filter((c) => justDone.value.has(c.id)))
const oldDone = computed(() => doneList.value.filter((c) => !justDone.value.has(c.id)))
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

// ── 貼委託表單 ──
const showForm = ref(false)
const title = ref('')
const note = ref('')
const goal = ref('')
const formGuild = ref('') // guild path,派遣工作資料夾
const formModel = ref('') // 冒險者位階(派遣模型)
const modelTouched = ref(false) // 使用者是否手動選過位階(選過就不再自動覆蓋)
const formSkin = ref('') // 呈現方式/職業:決定結果卡皮膚(空=自動 / 工程師=diff / 書記官=字卡)
// 呈現方式選項(只列有專屬皮膚的職業,其餘結果用一般文字)
const SKIN_OPTS = [
  { value: '', label: '🗡️ 一般(自動:有改 code 就用 diff)' },
  { value: '工程師', label: '⚙️ 工程師 — 程式碼 diff 卡' },
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
// dispatchNow=false 只貼布告欄;true 則貼完立即派遣
async function submit(dispatchNow) {
  formErr.value = null
  if (!title.value.trim()) { formErr.value = '請至少給個標題'; return }
  if (dispatchNow && !formGuild.value) {
    formErr.value = '立即派遣需要先選資料夾(公會)'; return
  }
  posting.value = true
  try {
    const g = store.guilds.find((x) => x.path === formGuild.value)
    const created = await store.addCommission({
      title: title.value,
      note: note.value || null,
      goal: goal.value || null,
      guild: g?.name || null,
      cwd: formGuild.value || null,
      model: formModel.value || null,
      klass: formSkin.value || null,
      images: images.value.slice(),
    })
    if (!created) { formErr.value = '貼委託失敗,請確認後端在線'; return }
    if (dispatchNow) {
      const rank = rankOf(formModel.value)
      if (!store.confirmDispatch(`立即派遣${rank.short}冒險者執行「${title.value}」?\n`
        + `會實際跑 Claude、花 token,並可能修改 ${formGuild.value} 內的檔案。`)) {
        // 已貼上布告欄,只是不立即派;關閉表單
        resetForm(); return
      }
      const err = await store.dispatch(created.id, formGuild.value, formModel.value)
      if (err) { formErr.value = err; return }
    }
    resetForm()
  } finally {
    posting.value = false
  }
}
function resetForm() {
  title.value = ''; note.value = ''; goal.value = ''
  modelTouched.value = false; formModel.value = ''; formSkin.value = ''
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
onUnmounted(() => { clearInterval(evTimer); clearInterval(liveTimer) })

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
</script>

<template>
  <div class="board">
    <div class="board__title">
      📌 委託布告欄
      <button class="board__add" @click="showForm = !showForm">
        {{ showForm ? '✕' : '＋ 貼委託' }}
      </button>
    </div>

    <!-- 派工對話框(大表單,好好寫規格) -->
    <div v-if="showForm" class="dpm-backdrop" @click.self="showForm = false">
      <div class="dpm">
        <div class="dpm__head">
          <h3>📋 派一張委託</h3>
          <button class="dpm__close" @click="showForm = false">✕</button>
        </div>

        <label class="dpm__label">標題</label>
        <input class="dpm__input" v-model="title" placeholder="想做的事(一句話)" />

        <label class="dpm__label">
          🎯 目標 <span class="dim">— 冒險者會照這段動手,寫清楚範圍與驗收標準</span>
        </label>
        <textarea class="dpm__area" v-model="goal" rows="6" @paste="onPaste"
          placeholder="具體描述要做什麼、涵蓋範圍、完成的標準…&#10;例:重構 X 模組,把 Y 邏輯抽成獨立函式,保持既有測試綠燈,並補一個新測試。&#10;(可直接 Ctrl+V 貼上截圖)"></textarea>

        <div class="dpm__row">
          <div class="dpm__col">
            <label class="dpm__label">🏰 資料夾(公會)</label>
            <select class="dpm__input" v-model="formGuild">
              <option value="">(不指定,僅貼布告欄)</option>
              <option v-for="g in store.guilds" :key="g.dir_key" :value="g.path">
                🏰 {{ g.name }}
              </option>
            </select>
          </div>
          <div class="dpm__col">
            <label class="dpm__label">🎖 位階</label>
            <select class="dpm__input" v-model="formModel" @change="modelTouched = true">
              <option v-for="r in RANKS" :key="r.value" :value="r.value">{{ r.label }}</option>
            </select>
          </div>
        </div>
        <div v-if="rankHint.reason && !modelTouched" class="board__hint">
          🎖 建議 {{ rankOf(rankHint.value).short }} {{ rankOf(rankHint.value).label }}
          <span class="dim">— {{ rankHint.reason }}</span>
        </div>

        <label class="dpm__label">
          🎨 呈現方式 <span class="dim">— 完成後結果要用哪種皮膚回來(決定冒險者職業)</span>
        </label>
        <select class="dpm__input" v-model="formSkin">
          <option v-for="s in SKIN_OPTS" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>

        <label class="dpm__label">備註 <span class="dim">(可省略)</span></label>
        <textarea class="dpm__area" v-model="note" rows="2" @paste="onPaste"
          placeholder="補充說明、參考連結…(可 Ctrl+V 貼圖)"></textarea>

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
          <button class="btn-ghost" @click="showForm = false">取消</button>
          <button class="btn-ghost" :disabled="posting" @click="submit(false)">
            📌 貼上布告欄
          </button>
          <button class="btn-primary" :disabled="posting || !formGuild"
                  :title="!formGuild ? '需先選資料夾才能立即派遣' : ''"
                  @click="submit(true)">
            🚀 貼上並立即派遣
          </button>
        </div>
      </div>
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
          <span class="quest__guild">
            {{ rankOf(c.model).short }} {{ COMMISSION_META[c.status].label }}
            <template v-if="c.status === 'queued' && store.queuePos[c.id]">
              (第 {{ store.queuePos[c.id] }} 位)
            </template>
            <template v-if="c.rounds"> · 第{{ c.rounds }}回合</template>
            <span v-if="fmtCost(c.cost)" class="quest__cost">💰 {{ fmtCost(c.cost) }}</span>
          </span>
        </button>
        <!-- 收合時的即時進度:一眼看出冒險者現在在幹什麼 -->
        <div v-if="c.status === 'working' && expandedId !== c.id && liveLine[c.id]"
             class="quest__live" @click="toggleExpand(c)">
          <span class="quest__live-dot"></span>{{ liveLine[c.id] }}
        </div>
        <div v-if="expandedId === c.id" class="dispatch">
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
            <button class="btn-primary" @click="doSteer(c)">送出</button>
          </div>
          <div class="dispatch__actions">
            <button v-if="c.status === 'working'" class="btn-ghost"
                    @click="store.stopDispatch(c.id)">⏹ 停止派遣</button>
            <button v-else-if="c.status === 'queued'" class="btn-ghost"
                    @click="store.stopDispatch(c.id)">🎫 移出佇列</button>
            <template v-else>
              <select v-model="dispatchModel" title="冒險者位階">
                <option v-for="r in RANKS" :key="r.value" :value="r.value">{{ r.label }}</option>
              </select>
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
          <span class="quest__guild">招募中</span>
          <span class="quest__x" @click.stop="store.removeCommission(c.id)" title="刪除委託">🗑</span>
        </button>
        <div v-if="expandedId === c.id" class="dispatch">
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
            <select v-model="dispatchModel" title="冒險者位階:派遣時用哪個模型">
              <option v-for="r in RANKS" :key="r.value" :value="r.value">{{ r.label }}</option>
            </select>
            <button class="btn-primary" @click="doDispatch(c)">🚀 派遣冒險者</button>
            <button class="btn-ghost" @click="doEnqueue(c)"
                    title="加入派遣佇列,依序自動執行(可一次排多張)">➕ 排隊</button>
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
          <span class="quest__guild">
            {{ rankOf(c.model).short }} 剛完成<template v-if="c.rounds"> · {{ c.rounds }}回合</template>
            <span v-if="fmtCost(c.cost)" class="quest__cost">💰 {{ fmtCost(c.cost) }}</span>
          </span>
          <button class="quest__dismiss" @click="dismissDone(c)" title="收起到已完成歷史">✓ 收起</button>
        </div>
        <div class="dispatch">
          <div class="dispatch__goal">
            <span class="dispatch__goal-i">🎯</span>
            <Markdown :text="c.goal || c.title" class="dispatch__goal-md" />
          </div>
          <CommissionReport v-if="reportFor(c)" :report="reportFor(c)" judge />
          <div v-else class="board__empty">📨 冒險者已完成,櫃台正在歸檔報告…</div>
        </div>
      </template>

      <!-- 已完成歷史(可展開看櫃台回報;預設收合避免塞滿布告欄)-->
      <button v-if="oldDone.length" class="board__fold" @click="showDone = !showDone">
        <span class="board__fold-caret">{{ showDone ? '▾' : '▸' }}</span>
        ✅ 已完成 <span class="dim">({{ oldDone.length }})</span>
      </button>
      <template v-if="showDone">
      <template v-for="c in oldDone" :key="'cd' + c.id">
        <button class="quest quest--done" @click="toggleExpand(c)"
                :title="reportFor(c) ? '點開看櫃台回報' : '點開看詳情'">
          <span class="quest__emoji">✅</span>
          <span class="quest__text">{{ c.title }}</span>
          <span v-if="doerOf(c)" class="quest__doer"
                :title="`由 ${doerOf(c).guild} 公會的${doerOf(c).klass}完成`">
            {{ doerOf(c).icon }} {{ doerOf(c).klass }}
          </span>
          <span class="quest__guild" v-if="c.model || c.rounds || c.cost || reportFor(c)">
            <template v-if="c.model">{{ rankOf(c.model).short }} </template>
            <template v-if="c.rounds">🤖 {{ c.rounds }}回合</template>
            <span v-if="reportFor(c)" title="已有櫃台回報">📄</span>
            <span v-if="fmtCost(c.cost)" class="quest__cost">💰 {{ fmtCost(c.cost) }}</span>
          </span>
          <span class="quest__x" @click.stop="store.removeCommission(c.id)" title="刪除委託">🗑</span>
        </button>
        <div v-if="expandedId === c.id" class="dispatch">
          <div class="dispatch__goal">
            <span class="dispatch__goal-i">🎯</span>
            <Markdown :text="c.goal || c.title" class="dispatch__goal-md" />
          </div>
          <CommissionReport v-if="reportFor(c)" :report="reportFor(c)" judge />
          <div v-else class="board__empty">這張委託沒有派遣回報(手動標記完成的)。</div>
          <div class="dispatch__actions">
            <button class="btn-ghost" @click="store.toggleCommission(c)">↩️ 重新招募</button>
          </div>
        </div>
      </template>
      </template>

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
