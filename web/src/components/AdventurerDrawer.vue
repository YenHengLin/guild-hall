<script setup>
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import StatusBadge from './StatusBadge.vue'
import { useGuildStore } from '../stores/guild'
import { recallReport, recallAsk, recallContinue } from '../api'
import { relativeTime, xpProgress, shortTokens, classOf, statusMeta, DEED_META } from '../labels'

const props = defineProps({ adventurer: Object })
const emit = defineEmits(['close'])
const store = useGuildStore()

// ── 📡 即時串流:看被召回的冒險者一步步幹活(stream-json）──
const streaming = ref(false)
const streamMode = ref('')       // 'report' | 'fix'
const steps = ref([])            // { kind, tool, text }
const streamCost = ref(null)
const streamErr = ref('')
const fixInstr = ref('')
let es = null

const STEP_ICON = { start: '✨', say: '💬', tool: '🛠', done: '🏁', error: '⚠️' }

function stopStream() {
  if (es) { es.close(); es = null }
  streaming.value = false
}

function startStream(mode) {
  if (streaming.value) return
  if (mode === 'fix') {
    if (!fixInstr.value.trim()) return
    if (!window.confirm('派這位冒險者接續做事 —— 會 resume 原 session 並實際改檔,且花 token。要繼續嗎?')) return
  }
  stopStream()
  steps.value = []
  streamCost.value = null
  streamErr.value = ''
  streamMode.value = mode
  streaming.value = true

  const p = new URLSearchParams({ adventurer_id: props.adventurer.id, mode })
  if (mode === 'fix') p.set('prompt', fixInstr.value.trim())
  es = new EventSource(`/api/recall/stream?${p.toString()}`)

  es.onmessage = (e) => {
    let ev
    try { ev = JSON.parse(e.data) } catch { return }
    if (ev.kind === 'meta') return
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
const recallBusy = ref('')       // '', 'report', 'ask', 'continue'
const recallResult = ref('')     // 回報 / 答詢 的文字
const recallCost = ref(null)
const recallErr = ref('')
const askQ = ref('')
const showAsk = ref(false)

async function doReport() {
  if (recallBusy.value) return
  if (!window.confirm('請這位冒險者(原 Claude session)回來寫一份完整回報。\n這會啟動 Claude、花少量 token。要繼續嗎?')) return
  recallBusy.value = 'report'; recallErr.value = ''; recallResult.value = ''
  try {
    const d = await recallReport(props.adventurer.id)
    recallResult.value = d.result; recallCost.value = d.cost
    store.load() // 刷新佇列(回報已歸檔)
  } catch (e) {
    recallErr.value = e?.response?.data?.detail || e.message
  } finally { recallBusy.value = '' }
}

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
  if (!window.confirm('會開一個新的終端視窗,resume 這個 session 讓你親自接續做事。要繼續嗎?')) return
  recallBusy.value = 'continue'; recallErr.value = ''
  try {
    await recallContinue(props.adventurer.id)
    recallResult.value = '✅ 已開啟終端視窗,可在那裡接續這個 session。'
  } catch (e) {
    recallErr.value = e?.response?.data?.detail || e.message
  } finally { recallBusy.value = '' }
}

const xp = computed(() => xpProgress(props.adventurer.xp))
const cls = computed(() => classOf(props.adventurer))
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
async function autoTrain() {
  if (!confirm('送訓練所會 spawn headless Claude 分析(花 token),產出的提案需在訓練所面板批准才落地。繼續?')) return
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

// 回報表單(標題 / 內容 可看可改,回報前先確認品質)
const showForm = ref(false)
const repTitle = ref('')
const repSummary = ref('')
const lessons = ref('')
const outcome = ref('success')
const sent = ref(false)

function openReportForm() {
  const a = props.adventurer
  repTitle.value = a.title || a.last_prompt?.slice(0, 40) || '完成一項任務'
  repSummary.value = a.last_prompt || ''
  showForm.value = true
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
</script>

<template>
  <div class="drawer-backdrop" @click.self="emit('close')">
    <div class="drawer">
      <button class="close" @click="emit('close')">✕ 關閉</button>
      <h2>{{ cls.icon }} {{ adventurer.title || '無名冒險者' }}</h2>

      <!-- 冒險日誌:同職角色的多趟 session,可切換檢視/召回 -->
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

      <!-- 召回冒險者:用 session id 復活原本的 Claude -->
      <div class="recall-box">
        <div class="recall-box__head">
          🔮 召回冒險者 <span class="dim">(啟動原 Claude · 花 token)</span>
        </div>
        <div class="recall-box__btns">
          <button :disabled="!!recallBusy" @click="doReport">
            {{ recallBusy === 'report' ? '召回中…' : '🗣 請他回報' }}
          </button>
          <button :disabled="!!recallBusy" @click="showAsk = !showAsk">❓ 問細節</button>
          <button :disabled="!!recallBusy" @click="doContinue">
            {{ recallBusy === 'continue' ? '開啟中…' : '▶️ 接續做事' }}
          </button>
        </div>
        <div v-if="showAsk" class="recall-box__ask">
          <input v-model="askQ" placeholder="想問這位冒險者什麼?" @keyup.enter="doAsk" />
          <button class="btn-primary" :disabled="!!recallBusy" @click="doAsk">
            {{ recallBusy === 'ask' ? '詢問中…' : '送出' }}
          </button>
        </div>
        <div v-if="recallBusy" class="recall-box__busy">⏳ 冒險者正在回來的路上…(可能要 10-60 秒)</div>
        <div v-if="recallErr" class="recall-box__err">⚠️ {{ recallErr }}</div>
        <div v-if="recallResult" class="recall-box__out">
          <div class="recall-box__outtext">{{ recallResult }}</div>
          <div v-if="recallCost" class="dim">花費 ≈ ${{ recallCost.toFixed(4) }}</div>
        </div>
      </div>

      <!-- 📡 即時串流:stream-json 逐步觀看被召回的冒險者幹活 -->
      <div class="stream-box">
        <div class="stream-box__head">
          📡 即時觀看 <span class="dim">(stream-json · 看它一步步做)</span>
        </div>
        <div class="stream-box__btns">
          <button :disabled="streaming" @click="startStream('report')">
            {{ streaming && streamMode === 'report' ? '串流中…' : '👁 即時回報' }}
          </button>
          <button v-if="streaming" class="btn-stop" @click="stopStream">⏹ 停止</button>
        </div>
        <div class="stream-box__fix">
          <input v-model="fixInstr" placeholder="派他接續做事(會改檔)…"
                 @keyup.enter="startStream('fix')" :disabled="streaming" />
          <button class="btn-primary" :disabled="streaming || !fixInstr.trim()"
                  @click="startStream('fix')">🔧 派工</button>
        </div>

        <div v-if="streaming" class="stream-box__live">⏳ 冒險者正在幹活…</div>
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

      <!-- 向櫃台回報 -->
      <div class="report-box">
        <div v-if="sent" class="report-box__ok">
          ✅ 已送交櫃台!櫃台會依序把成果寫入公會知識庫。
        </div>
        <template v-else-if="showForm">
          <label class="report-box__lbl">標題(會寫進知識庫)</label>
          <input v-model="repTitle" placeholder="一句話成果" />
          <label class="report-box__lbl">內容</label>
          <textarea v-model="repSummary" placeholder="這次做了什麼" rows="3"></textarea>
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
          <div class="report-box__actions">
            <button class="btn-primary" :disabled="store.reporting" @click="send">
              {{ store.reporting ? '送出中…' : '📤 送交回報' }}
            </button>
            <button class="btn-ghost" @click="showForm = false">取消</button>
          </div>
        </template>
        <button v-else class="btn-report" @click="openReportForm">
          ✅ 回報此任務給櫃台
        </button>
      </div>

      <!-- 進化系統 -->
      <div class="evo-box" :class="{ 'evo-box--ready': canEvolve }">
        <div class="evo-box__head">
          <b>🎓 進化</b>
          <span class="dim">已完成 {{ completed }} 次任務</span>
        </div>
        <!-- 進度條:距下次進化 -->
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

        <!-- 已習得能力 -->
        <div v-if="evo?.learned?.length" class="evo-learned">
          <div class="dim" style="margin-bottom: 4px">習得能力:</div>
          <div v-for="l in evo.learned" :key="l.id" class="evo-skill">
            <span>{{ ROUTE_LABEL[l.route]?.slice(0, 2) || '✨' }}</span>
            <b>{{ l.name }}</b>
            <span v-if="l.detail" class="dim">· {{ l.detail }}</span>
          </div>
        </div>
      </div>

      <div class="dim">
        🏰 {{ adventurer.guild }} 公會 · {{ cls.klass }} ·
        Lv.{{ xp.level }} ·
        <StatusBadge :status="adventurer.status" />
      </div>

      <!-- 等級經驗條(戰功制)-->
      <div class="drawer__xp">
        <div class="drawer__xpbar"><i :style="{ width: xp.pct + '%' }"></i></div>
        <div class="dim">
          Lv.{{ xp.level }} → {{ xp.level + 1 }} · 戰功 {{ adventurer.xp }} ·
          距升級 {{ xp.span - xp.into }}
        </div>
      </div>

      <!-- 🎖 戰功明細:等級的真正來源(實際做的事)-->
      <div v-if="deedList.length" class="deeds">
        <span v-for="d in deedList" :key="d.label" class="deed" :title="d.label">
          <span class="deed__i">{{ d.icon }}</span>
          <span class="deed__label">{{ d.label }}</span>
          <b class="deed__n">{{ d.n }}</b>
        </span>
      </div>

      <p class="dim">
        🕑 {{ relativeTime(adventurer.last_active) }} ·
        🜋 {{ adventurer.branch || '—' }} ·
        ✨ 消耗 {{ shortTokens(adventurer.total_tokens) }} tokens ·
        💬 {{ adventurer.message_count }} 則
      </p>

      <div v-if="adventurer.last_prompt">
        <b>最後委託</b>
        <p class="adv__prompt" style="-webkit-line-clamp: 6">
          「{{ adventurer.last_prompt }}」
        </p>
      </div>

      <!-- 🧬 自訂能力(可手動增刪)-->
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

      <b>📜 施展過的 skill</b>
      <div class="adv__skills" style="margin-bottom: 14px">
        <span v-for="s in namedSkills" :key="s.name" class="chip skill">
          {{ s.name }} ×{{ s.count }}
        </span>
        <span v-if="!namedSkills.length" class="dim" style="font-size: 12px">
          這位冒險者還沒用過任何具名 skill(只用了基本工具)。
        </span>
      </div>

      <b>冒險日誌 <span class="dim">(近期實際動作 {{ adventurer.recent_log?.length || 0 }} 筆)</span></b>
      <div class="advlog">
        <div v-for="(e, i) in adventurer.recent_log" :key="i" class="log-line" :class="`log--${e.kind}`">
          <span class="k">{{ e.kind === 'tool' ? '🛠' : e.kind === 'prompt' ? '📜' : '🏷' }}</span>
          <span class="log-line__t">{{ e.text }}</span>
        </div>
        <div v-if="!adventurer.recent_log?.length" class="dim" style="font-size: 12px">
          尚無動作紀錄。
        </div>
      </div>

      <div class="drawer__retire">
        <button class="btn-retire" @click="retire">🫥 讓其退隱(從面板隱藏,不刪紀錄)</button>
      </div>
    </div>
  </div>
</template>
