<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import StatusBadge from './StatusBadge.vue'
import ResultCard from './ResultCard.vue'
import { useGuildStore } from '../stores/guild'
import { recallAsk, recallContinue } from '../api'
import {
  relativeTime, xpProgress, shortTokens, classOf, statusMeta, DEED_META,
  CLASSES, iconForClass, skinForClass,
} from '../labels'

const props = defineProps({ adventurer: Object })
const emit = defineEmits(['close'])
const store = useGuildStore()

// ── 📡 即時串流:看被召回的冒險者一步步幹活(stream-json）──
const streaming = ref(false)
const streamMode = ref('')       // 'report' | 'fix'
const steps = ref([])            // { kind, tool, text }
const streamCost = ref(null)
const streamErr = ref('')
const filedMsg = ref('')          // report 模式串流結束、自動歸檔後的提示
const fixInstr = ref('')
let es = null

const STEP_ICON = { start: '✨', say: '💬', tool: '🛠', done: '🏁', error: '⚠️' }

function stopStream() {
  if (es) { es.close(); es = null }
  streaming.value = false
}

// mode='report':串流看它做 + 結束自動歸檔進知識庫(舊「請他回報」+「即時回報」已合一)
// mode='fix'   :給指示接續做事,會實際改檔
function startStream(mode) {
  if (streaming.value) return
  if (mode === 'fix') {
    if (!fixInstr.value.trim()) return
    if (!store.confirmDispatch('派這位冒險者接續做事 —— 會 resume 原 session 並實際改檔,且花 token。要繼續嗎?')) return
  }
  stopStream()
  steps.value = []
  streamCost.value = null
  streamErr.value = ''
  filedMsg.value = ''
  streamMode.value = mode
  streaming.value = true

  const p = new URLSearchParams({ adventurer_id: props.adventurer.id, mode })
  if (mode === 'fix') p.set('prompt', fixInstr.value.trim())
  es = new EventSource(`/api/recall/stream?${p.toString()}`)

  es.onmessage = (e) => {
    let ev
    try { ev = JSON.parse(e.data) } catch { return }
    if (ev.kind === 'meta') return
    if (ev.kind === 'filed') { filedMsg.value = '✅ 成果已歸檔進櫃台知識庫'; store.load(); return }
    if (ev.kind === 'end') { stopStream(); if (streamMode.value === 'fix') store.load(); return }
    if (ev.kind === 'error') { streamErr.value = ev.text; return }
    if (ev.kind === 'done') streamCost.value = ev.cost
    steps.value.push(ev)
  }
  es.onerror = () => {
    if (streaming.value && steps.value.length === 0) streamErr.value = '連線中斷(後端是否在跑?)'
    stopStream()
  }
}

// 切換冒險者或關閉抽屜時,結束串流
watch(() => props.adventurer?.id, stopStream)
onUnmounted(stopStream)

// 從報告「派冒險者接續」帶進來的預填指示 → 塞進 fix 輸入框(不自動送,讓使用者確認)
function consumeFixSeed() {
  if (store.fixSeed) {
    fixInstr.value = store.fixSeed
    store.fixSeed = ''
  }
}
watch(() => props.adventurer?.id, consumeFixSeed)

// 同職角色的其他冒險(session):>1 趟才顯示切換器
const siblings = computed(() => store.personaQuests || [])
function questLabel(q) {
  return q.title || q.last_prompt?.slice(0, 24) || '無名冒險'
}
function switchQuest(id) {
  if (id !== props.adventurer.id) store.openAdventurer(id, true)
}

// ── 召回冒險者(用 session id 復活原本的 Claude)──
// 「請他回報」已併進 startStream('report')(串流+自動歸檔);這裡只剩答詢 / 接續。
const recallBusy = ref('')       // '', 'ask', 'continue'
const recallResult = ref('')     // 答詢的文字 / 接續的提示
const recallCost = ref(null)
const recallErr = ref('')
const askQ = ref('')
const showAsk = ref(false)

async function doAsk() {
  if (recallBusy.value || !askQ.value.trim()) return
  recallBusy.value = 'ask'; recallErr.value = ''; recallResult.value = ''
  try {
    const d = await recallAsk(props.adventurer.id, askQ.value)
    recallResult.value = d.result; recallCost.value = d.cost
  } catch (e) {
    recallErr.value = e?.response?.data?.detail || e.message
  } finally { recallBusy.value = '' }
}

async function doContinue() {
  if (recallBusy.value) return
  recallBusy.value = 'continue'; recallErr.value = ''
  try {
    await recallContinue(props.adventurer.id)
    recallResult.value = '✅ 已開啟終端視窗,可在那裡接續這個 session。'
  } catch (e) {
    recallErr.value = e?.response?.data?.detail || e.message
  } finally { recallBusy.value = '' }
}

const xp = computed(() => xpProgress(props.adventurer.xp))
// 職業 / 取名:手動指定,存後端(依 session id)→ 大廳與跨裝置都看得到。
// 取名還會讓這位從合併的職業角色「獨立」站上大廳。
const classOverride = ref(props.adventurer.custom_class || '')
const nameOverride = ref(props.adventurer.custom_name || '')
const editingName = ref(false)
const nameDraft = ref('')
const displayName = computed(() =>
  nameOverride.value || props.adventurer.title || '無名冒險者')

// 名字 + 職業一起送(後端一次覆寫兩者,分開送會互相清空)
function persistIdentity() {
  store.setIdentity(props.adventurer.id, {
    name: nameOverride.value || null,
    klass: classOverride.value || null,
  })
}
function startEditName() {
  nameDraft.value = nameOverride.value || ''
  editingName.value = true
}
function saveName() {
  nameOverride.value = nameDraft.value.trim().slice(0, 24)
  editingName.value = false
  persistIdentity()
}

watch(() => props.adventurer?.id, () => {
  classOverride.value = props.adventurer.custom_class || ''
  nameOverride.value = props.adventurer.custom_name || ''
  editingName.value = false
  showSkin.value = false
})
const cls = computed(() => classOverride.value
  ? { klass: classOverride.value, icon: iconForClass(classOverride.value) }
  : classOf(props.adventurer))
function setClass(klass) {
  classOverride.value = klass
  persistIdentity()
}

// 皮膚預覽:用內建範例,讓你選了職業能「先看看」結果長怎樣(不用真的跑 Claude)
const showSkin = ref(false)
const SKIN_SAMPLE = {
  teaching: {
    kind: 'teaching',
    headline: '股票入門:三個必懂觀念',
    cards: [
      { front: '本益比 (P/E) 是什麼?',
        back: '股價 ÷ 每股盈餘(EPS)。代表「為公司每賺 1 元,你願意付多少錢」。P/E 20 = 用 20 元買它每年賺的 1 元;越高通常代表市場期待越高(也可能越貴)。' },
      { front: '「除權息」是什麼意思?',
        back: '公司把賺的錢分給股東:除息=配現金、除權=配股票。當天股價會扣掉配發金額(參考價),帳面看似「跌」,其實錢/股票已進你口袋。' },
      { front: '為什麼要分散持股?',
        back: '把資金分到不同產業/標的,單一公司出事時不會重傷整體 —— 雞蛋別放同一個籃子,降低非系統性風險。' },
    ],
  },
  coding: {
    kind: 'coding',
    headline: '範例:把確認彈窗集中成 helper',
    files: [
      { path: 'src/stores/guild.js', why: '新增 confirmDispatch 集中確認',
        hunks: [{ before: "if (!confirm('要派遣嗎?')) return",
                  after: "if (!store.confirmDispatch('要派遣嗎?')) return" }] },
    ],
    notes: '這是範例皮膚,實際派遣完成後會換成真的 diff。',
  },
}
const previewCard = computed(() => SKIN_SAMPLE[skinForClass(cls.value.klass)] || null)
// 戰功明細(只顯示有值的項)
const deedList = computed(() => {
  const d = props.adventurer.deeds || {}
  return DEED_META
    .map(([k, icon, label]) => ({ icon, label, n: d[k] || 0 }))
    .filter((x) => x.n > 0)
})
// 真正的 skill(具名技能,如 webapp-testing / oht-wiki),不含原始工具
const namedSkills = computed(() =>
  (props.adventurer.all_skills || []).filter((s) => s.kind === 'skill'),
)

// ── 進化系統 ──
const evo = computed(() => store.evoMap[props.adventurer.id] || null)
const completed = computed(() => evo.value?.completed || 0)
const toNext = computed(() => 5 - (completed.value % 5)) // 距下次進化還差幾次
const canEvolve = computed(() => (evo.value?.available || 0) > 0)
const ROUTE_LABEL = {
  memory: '🧠 記住通用能力', skill: '📜 進化 / 新建 skill',
  tool: '🔧 採用更好的工具', script: '⚙️ 自己寫工具',
}

// 訓練所提示詞(複製到某個 Claude session 執行 /訓練所)
const trainPrompt = computed(() => {
  const e = evo.value
  if (!e) return ''
  return `/訓練所 冒險者 ${e.adventurer_id}（${e.guild || ''}公會）已完成 ${e.completed} 次任務，可進化。`
    + `\n近期解決：${(e.recent_titles || []).join('、') || '（無）'}`
    + `\n心得：${(e.recent_lessons || []).join('；') || '（無）'}`
    + `\n建議方向：${ROUTE_LABEL[e.suggested_route] || e.suggested_route}。`
    + `請分析後提議一項進化並執行，完成後 POST 回 http://localhost:8848/api/evolutions 登記。`
})
const copied = ref(false)
function copyPrompt() {
  navigator.clipboard?.writeText(trainPrompt.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

// 一鍵送訓練所:headless Claude 分析素材產進化提案,到訓練所面板批准
// (產出的提案還需在訓練所面板批准才落地,故此處不再二次確認)
async function autoTrain() {
  await store.train(props.adventurer.id)
}

// 手動登記進化
const showEvoForm = ref(false)
const evoRoute = ref('memory')
const evoName = ref('')
const evoDetail = ref('')
const evolved = ref(false)
async function doEvolve() {
  const ok = await store.sendEvolution({
    adventurer_id: props.adventurer.id,
    guild: props.adventurer.guild,
    route: evoRoute.value,
    name: evoName.value || '新能力',
    detail: evoDetail.value || null,
  })
  if (ok) { evolved.value = true; showEvoForm.value = false; evoName.value = ''; evoDetail.value = '' }
}

onMounted(() => {
  if (evo.value?.suggested_route) evoRoute.value = evo.value.suggested_route
  consumeFixSeed()
})

// ── 🧬 自訂能力增刪 ──
const newAbility = ref('')
async function addAbility() {
  const n = newAbility.value.trim()
  if (!n) return
  await store.addAbility(n)
  newAbility.value = ''
}
async function removeAbility(aid) {
  await store.removeAbility(aid)
}

// 退隱(從面板隱藏,不刪紀錄)
async function retire() {
  const ok = window.confirm(
    '讓這位冒險者退隱?\n\n只會從面板隱藏,不會刪除任何 Claude 對話紀錄檔。\n(之後仍可從資料庫恢復)',
  )
  if (ok) await store.retireAdventurer(props.adventurer.id)
}

// 回報表單:你自己寫(可挑毛病),按需 AI 潤稿後再歸檔
const showForm = ref(false)
const repTitle = ref('')
const repSummary = ref('')
const lessons = ref('')
const outcome = ref('success')
const sent = ref(false)
const polished = ref(null) // AI 潤稿結果預覽 {title, summary, lessons};採用才覆蓋欄位

function openReportForm() {
  const a = props.adventurer
  repTitle.value = a.title || a.last_prompt?.slice(0, 40) || '完成一項任務'
  repSummary.value = a.last_prompt || ''
  showForm.value = true
}

// ✨ AI 潤稿:把目前草稿(含挑毛病)送便宜模型整理,先給預覽、採用才覆蓋
async function doPolish() {
  const p = await store.polishDraft({
    title: repTitle.value,
    summary: repSummary.value,
    lessons: lessons.value,
    guild: props.adventurer.guild,
  })
  if (p) polished.value = p
}
function acceptPolish() {
  if (!polished.value) return
  repTitle.value = polished.value.title || repTitle.value
  repSummary.value = polished.value.summary || repSummary.value
  lessons.value = polished.value.lessons || lessons.value
  polished.value = null
}

async function send() {
  const a = props.adventurer
  const files = (a.all_skills || [])
    .filter((s) => s.kind === 'tool' && /Edit|Write/.test(s.name))
    .map((s) => `${s.name} ×${s.count}`)
  const ok = await store.sendReport({
    adventurer_id: a.id,
    guild: a.guild,
    title: repTitle.value.trim() || '完成一項任務',
    summary: repSummary.value.trim() || null,
    files_changed: files,
    lessons: lessons.value || null,
    outcome: outcome.value,
  })
  if (ok) {
    sent.value = true
    showForm.value = false
  }
}

// 「叫回來做事」動作面板:預設收合。冒險者頁主要是「看」,動作(召回/回報/
// 進化/能力/退隱)全收進這一顆,點開才展開 —— 看與做事分離。
const showActions = ref(false)
// session 短碼:給使用者一眼認出是哪一趟(resume 用得上)
const shortSid = computed(() => (props.adventurer.id || '').slice(0, 8))
</script>

<template>
  <div class="drawer-backdrop" @click.self="emit('close')">
    <div class="drawer">
      <button class="close" @click="emit('close')">✕ 關閉</button>

      <!-- ══ 看:這位冒險者是誰、在哪、做了什麼 ══ -->
      <div class="advname">
        <template v-if="editingName">
          <span class="advname__icon">{{ cls.icon }}</span>
          <input class="advname__input" v-model="nameDraft" maxlength="24"
                 placeholder="給他取個名字…" @keyup.enter="saveName" @keyup.esc="editingName = false" />
          <button class="btn-primary" @click="saveName">存</button>
          <button class="btn-ghost" @click="editingName = false">取消</button>
        </template>
        <template v-else>
          <h2 class="advname__h">{{ cls.icon }} {{ displayName }}</h2>
          <button class="advname__edit" @click="startEditName"
                  :title="nameOverride ? '改名' : '取名'">✏️</button>
        </template>
      </div>
      <!-- 有取名時,把 session 原本的任務標題當「目前在做的事」附在下面 -->
      <div v-if="nameOverride && adventurer.title" class="advname__task dim">
        目前:{{ adventurer.title }}
      </div>

      <!-- 身份 + 「在哪」:資料夾 / branch / session(resume 用得上,是初心)-->
      <div class="whoami">
        <span class="whoami__loc" :title="`資料夾(公會):${adventurer.guild}`">
          📁 {{ adventurer.guild }}
        </span>
        <span class="whoami__sid" :title="`session id:${adventurer.id}`">🔖 {{ shortSid }}</span>
        <span v-if="adventurer.branch" class="whoami__branch">🜋 {{ adventurer.branch }}</span>
        <StatusBadge :status="adventurer.status" />
      </div>

      <!-- 職業:手動指定(決定結果用哪種皮膚)+ 皮膚預覽 -->
      <div class="classpick">
        <span class="classpick__lbl">職業</span>
        <select :value="cls.klass" @change="setClass($event.target.value)">
          <option v-for="c in CLASSES" :key="c.klass" :value="c.klass">
            {{ c.icon }} {{ c.klass }}
          </option>
        </select>
        <span v-if="!classOverride" class="dim classpick__auto">(自動判定)</span>
        <button v-else class="classpick__reset" @click="setClass('')" title="改回自動判定">↺ 自動</button>
        <button class="classpick__preview" @click="showSkin = !showSkin">
          {{ showSkin ? '收起皮膚' : '預覽此皮膚' }}
        </button>
      </div>
      <div v-if="showSkin" class="classpick__skin">
        <ResultCard v-if="previewCard" :card="previewCard" />
        <div v-else class="dim classpick__none">
          「{{ cls.klass }}」目前沒有專屬皮膚,結果會用一般文字(markdown)呈現。
        </div>
      </div>

      <!-- 等級 + 戰功一覽(戰功制)-->
      <div class="drawer__xp">
        <div class="drawer__xpbar"><i :style="{ width: xp.pct + '%' }"></i></div>
        <div class="dim">
          {{ cls.klass }} · Lv.{{ xp.level }} → {{ xp.level + 1 }} ·
          戰功 {{ adventurer.xp }} · 距升級 {{ xp.span - xp.into }}
        </div>
      </div>
      <div v-if="deedList.length" class="deeds">
        <span v-for="d in deedList" :key="d.label" class="deed" :title="d.label">
          <span class="deed__i">{{ d.icon }}</span>
          <span class="deed__label">{{ d.label }}</span>
          <b class="deed__n">{{ d.n }}</b>
        </span>
      </div>
      <p class="dim">
        🕑 {{ relativeTime(adventurer.last_active) }} ·
        ✨ {{ shortTokens(adventurer.total_tokens) }} tokens ·
        💬 {{ adventurer.message_count }} 則
      </p>

      <!-- 最後委託 -->
      <div v-if="adventurer.last_prompt" class="whatdid">
        <b>最後委託</b>
        <p class="adv__prompt" style="-webkit-line-clamp: 6">「{{ adventurer.last_prompt }}」</p>
      </div>

      <!-- 近期動作(這位冒險者實際做了什麼 —— 看的重點,預設可見)-->
      <div class="whatdid">
        <b>近期動作 <span class="dim">({{ adventurer.recent_log?.length || 0 }} 筆)</span></b>
        <div class="advlog">
          <div v-for="(e, i) in adventurer.recent_log" :key="i" class="log-line" :class="`log--${e.kind}`">
            <span class="k">{{ e.kind === 'tool' ? '🛠' : e.kind === 'prompt' ? '📜' : '🏷' }}</span>
            <span class="log-line__t">{{ e.text }}</span>
          </div>
          <div v-if="!adventurer.recent_log?.length" class="dim" style="font-size: 12px">
            尚無動作紀錄。
          </div>
        </div>
      </div>

      <!-- 施展過的 skill(看)-->
      <div v-if="namedSkills.length" class="whatdid">
        <b>📜 施展過的 skill</b>
        <div class="adv__skills">
          <span v-for="s in namedSkills" :key="s.name" class="chip skill">{{ s.name }} ×{{ s.count }}</span>
        </div>
      </div>

      <!-- 同職多趟 session:切換檢視(看)-->
      <div v-if="siblings.length > 1" class="questlog">
        <div class="questlog__head">
          📜 {{ cls.klass }}的冒險日誌 <span class="dim">共 {{ siblings.length }} 趟 · 點選切換</span>
        </div>
        <div class="questlog__list">
          <button
            v-for="q in siblings"
            :key="q.id"
            class="questlog__item"
            :class="{ 'is-current': q.id === adventurer.id }"
            @click="switchQuest(q.id)"
            :title="questLabel(q)"
          >
            <span>{{ statusMeta(q.status).emoji }}</span>
            <span class="questlog__t">{{ questLabel(q) }}</span>
            <span class="dim">{{ relativeTime(q.last_active) }}</span>
          </button>
        </div>
      </div>

      <!-- ══ 做事:召回 / 回報 / 進化 / 能力 / 退隱 全收進這一顆 ══ -->
      <button class="act-toggle" :class="{ 'is-open': showActions }" @click="showActions = !showActions">
        <span class="act-toggle__i">⚡</span>
        叫回來做事
        <span class="dim">召回 · 回報 · 進化 · 能力</span>
        <span class="act-toggle__caret">{{ showActions ? '▾' : '▸' }}</span>
      </button>

      <div v-if="showActions" class="act-panel">
        <!-- 召回冒險者:用 session id 復活原本的 Claude -->
        <div class="recall-box">
          <div class="recall-box__head">
            🔮 召回冒險者 <span class="dim">(啟動原 Claude · 花 token)</span>
          </div>
          <div class="recall-box__btns">
            <!-- 請他回報 = 串流看它做 + 結束自動歸檔進知識庫(舊「即時回報」已併入)-->
            <button :disabled="streaming || !!recallBusy" @click="startStream('report')">
              {{ streaming && streamMode === 'report' ? '回報中…' : '🗣 請他回報' }}
            </button>
            <button :disabled="streaming || !!recallBusy" @click="showAsk = !showAsk">❓ 問細節</button>
            <button :disabled="streaming || !!recallBusy" @click="doContinue">
              {{ recallBusy === 'continue' ? '開啟中…' : '▶️ 接續做事' }}
            </button>
          </div>
          <div v-if="showAsk" class="recall-box__ask">
            <input v-model="askQ" placeholder="想問這位冒險者什麼?" @keyup.enter="doAsk" />
            <button class="btn-primary" :disabled="!!recallBusy" @click="doAsk">
              {{ recallBusy === 'ask' ? '詢問中…' : '送出' }}
            </button>
          </div>
          <div v-if="recallBusy === 'ask'" class="recall-box__busy">⏳ 冒險者正在回來的路上…(可能要 10-60 秒)</div>
          <div v-if="recallErr" class="recall-box__err">⚠️ {{ recallErr }}</div>
          <div v-if="recallResult" class="recall-box__out">
            <div class="recall-box__outtext">{{ recallResult }}</div>
            <div v-if="recallCost" class="dim">花費 ≈ ${{ recallCost.toFixed(4) }}</div>
          </div>
        </div>

        <!-- 派工:給一項指示,派他接續做事(會實際改檔)-->
        <div class="stream-box">
          <div class="stream-box__head">
            🔧 派工 <span class="dim">給指示接續做事 · 會實際改檔</span>
          </div>
          <div class="stream-box__fix">
            <input v-model="fixInstr" placeholder="派他接續做事(會改檔)…"
                   @keyup.enter="startStream('fix')" :disabled="streaming" />
            <button class="btn-primary" :disabled="streaming || !fixInstr.trim()"
                    @click="startStream('fix')">🔧 派工</button>
          </div>
        </div>

        <!-- 串流輸出:請他回報 / 派工 共用(看它一步步做)-->
        <div v-if="streaming || steps.length || streamErr || filedMsg" class="stream-out">
          <div v-if="streaming" class="stream-box__live">
            ⏳ 冒險者正在幹活…
            <button class="btn-stop" @click="stopStream">⏹ 停止</button>
          </div>
          <div v-if="filedMsg" class="report-box__ok">{{ filedMsg }}</div>
          <div v-if="streamErr" class="recall-box__err">⚠️ {{ streamErr }}</div>
          <div v-if="steps.length" class="stream-log">
            <div v-for="(s, i) in steps" :key="i" class="stream-step" :class="`step--${s.kind}`">
              <span class="stream-step__i">{{ STEP_ICON[s.kind] || '·' }}</span>
              <span v-if="s.tool" class="stream-step__tool">{{ s.tool }}</span>
              <span class="stream-step__t">{{ s.text }}</span>
            </div>
            <div v-if="streamCost" class="dim" style="margin-top: 4px">花費 ≈ ${{ streamCost.toFixed(4) }}</div>
          </div>
        </div>

        <!-- 向櫃台回報:你自己寫(可挑毛病),按需 AI 潤稿 -->
        <div class="report-box">
          <div v-if="sent" class="report-box__ok">
            ✅ 已送交櫃台!櫃台會依序把成果寫入公會知識庫。
          </div>
          <template v-else-if="showForm">
            <label class="report-box__lbl">標題(會寫進知識庫)</label>
            <input v-model="repTitle" placeholder="一句話成果" />
            <label class="report-box__lbl">
              內容 <span class="dim">— 想到什麼寫什麼,可直接挑毛病(如「錯誤處理沒寫」),等等讓 AI 幫你整理</span>
            </label>
            <textarea v-model="repSummary" rows="4"
              placeholder="這次做了什麼、你覺得哪裡寫得好 / 不好…"></textarea>
            <label class="report-box__lbl">心得 / 教訓(可省略)</label>
            <textarea v-model="lessons" placeholder="值得記住的心得" rows="2"></textarea>
            <div class="report-box__row">
              <label>結果</label>
              <select v-model="outcome">
                <option value="success">✅ 成功</option>
                <option value="partial">🟡 部分完成</option>
                <option value="failed">❌ 失敗</option>
              </select>
            </div>

            <!-- AI 潤稿:手動觸發,先預覽再採用 -->
            <button class="btn-polish" :disabled="store.polishing || !repSummary.trim()"
                    @click="doPolish">
              {{ store.polishing ? '✨ 潤稿中…' : '✨ AI 潤稿(把草稿整理通順)' }}
            </button>
            <div v-if="store.polishError" class="recall-box__err">⚠️ {{ store.polishError }}</div>
            <div v-if="polished" class="polish-preview">
              <div class="polish-preview__h">✨ 潤稿結果 <span class="dim">— 採用會覆蓋上方欄位</span></div>
              <div class="polish-preview__t"><b>{{ polished.title }}</b></div>
              <div class="polish-preview__b">{{ polished.summary }}</div>
              <div v-if="polished.lessons" class="polish-preview__l dim">心得:{{ polished.lessons }}</div>
              <div class="report-box__actions">
                <button class="btn-primary" @click="acceptPolish">✓ 採用</button>
                <button class="btn-ghost" @click="polished = null">✗ 不要</button>
              </div>
            </div>

            <div class="report-box__actions">
              <button class="btn-primary" :disabled="store.reporting" @click="send">
                {{ store.reporting ? '送出中…' : '📤 送交回報' }}
              </button>
              <button class="btn-ghost" @click="showForm = false">取消</button>
            </div>
          </template>
          <button v-else class="btn-report" @click="openReportForm">
            ✍️ 自己寫回報 <span class="dim">(可挑毛病 · 可 AI 潤稿)</span>
          </button>
        </div>

        <!-- 進化系統 -->
        <div class="evo-box" :class="{ 'evo-box--ready': canEvolve }">
          <div class="evo-box__head">
            <b>🎓 進化</b>
            <span class="dim">已完成 {{ completed }} 次任務</span>
          </div>
          <div class="evo-prog" :title="`每 5 次任務可進化一次`">
            <i :style="{ width: ((completed % 5) / 5 * 100) + '%' }"></i>
            <span class="evo-prog__t">
              {{ canEvolve ? '✨ 可進化!' : `距下次進化還差 ${toNext} 次` }}
            </span>
          </div>

          <template v-if="evolved">
            <div class="report-box__ok">✨ 已登記進化!冒險者習得新能力。</div>
          </template>

          <template v-else-if="canEvolve">
            <div class="evo-hint">
              建議方向:<b>{{ ROUTE_LABEL[evo.suggested_route] }}</b>。
            </div>
            <button class="btn-primary" :disabled="store.training" @click="autoTrain">
              {{ store.training ? '⏳ 教官分析中…' : '🎓 送訓練所(自動產進化提案)' }}
            </button>
            <div v-if="store.trainError" class="dispatch__err">⚠️ {{ store.trainError }}</div>
            <div class="evo-or">— 或複製指令到該資料夾的 Claude 手動進化 —</div>
            <button class="btn-copy" @click="copyPrompt">
              {{ copied ? '✓ 已複製' : '📋 複製訓練所指令' }}
            </button>
            <div class="evo-or">— 或手動登記已習得的能力 —</div>

            <template v-if="showEvoForm">
              <select v-model="evoRoute">
                <option v-for="(l, k) in ROUTE_LABEL" :key="k" :value="k">{{ l }}</option>
              </select>
              <input v-model="evoName" placeholder="能力 / skill 名稱" />
              <input v-model="evoDetail" placeholder="產出位置 / 說明(可省略)" />
              <div class="report-box__actions">
                <button class="btn-primary" @click="doEvolve">✨ 登記進化</button>
                <button class="btn-ghost" @click="showEvoForm = false">取消</button>
              </div>
            </template>
            <button v-else class="btn-ghost evo-manual" @click="showEvoForm = true">
              ✍️ 手動登記進化
            </button>
          </template>

          <div v-if="evo?.learned?.length" class="evo-learned">
            <div class="dim" style="margin-bottom: 4px">習得能力:</div>
            <div v-for="l in evo.learned" :key="l.id" class="evo-skill">
              <span>{{ ROUTE_LABEL[l.route]?.slice(0, 2) || '✨' }}</span>
              <b>{{ l.name }}</b>
              <span v-if="l.detail" class="dim">· {{ l.detail }}</span>
            </div>
          </div>
        </div>

        <!-- 🧬 自訂能力(可手動增刪)-->
        <div class="abil-box">
          <b>🧬 能力 <span class="dim">(可自己編輯)</span></b>
          <div class="abil">
            <span v-for="a in adventurer.custom_abilities" :key="a.id" class="abil__chip">
              {{ a.name }}
              <button class="abil__x" title="移除" @click="removeAbility(a.id)">✕</button>
            </span>
            <span v-if="!adventurer.custom_abilities?.length" class="dim abil__empty">
              尚無自訂能力 —— 在下方輸入,把這位冒險者擅長的事記下來。
            </span>
          </div>
          <div class="abil__add">
            <input v-model="newAbility" placeholder="新增能力(如 NATS、reroute 演算法…)"
                   maxlength="40" @keyup.enter="addAbility" />
            <button class="btn-primary" :disabled="!newAbility.trim()" @click="addAbility">＋ 加入</button>
          </div>
        </div>

        <!-- 退隱 -->
        <div class="drawer__retire">
          <button class="btn-retire" @click="retire">🫥 讓其退隱(從面板隱藏,不刪紀錄)</button>
        </div>
      </div>
    </div>
  </div>
</template>
