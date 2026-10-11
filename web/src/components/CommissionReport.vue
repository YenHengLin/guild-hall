<script setup>
// 一張委託派遣完成後,櫃台產生的結案回報 —— 直接顯示在委託上(不必再開戰報面板)。
// judge=true 時附上幕後審核動作(認可 / 退回櫃台重寫 / 派冒險者接續),
// 讓布告欄與戰報面板共用同一份回報顯示 + 審核邏輯。
import { ref, computed } from 'vue'
import { useGuildStore } from '../stores/guild'
import { RECEPTION_ID } from '../labels'
import Markdown from './Markdown.vue'
import ResultCard from './ResultCard.vue'

const props = defineProps({
  report: { type: Object, required: true },
  judge: { type: Boolean, default: false }, // 是否顯示幕後審核動作
})
const store = useGuildStore()

// 職業結果卡(如 coding 卡):有就用職業皮膚渲染,取代純檔名清單
const KNOWN_CARDS = ['coding', 'ops', 'review', 'ship', 'teaching']
const hasCard = computed(() =>
  props.report.card && KNOWN_CARDS.includes(props.report.card.kind))
// 教學卡是自足學習介面:不套櫃檯報告框,也提供「延伸深入」一鍵派工
const isTeaching = computed(() => props.report.card?.kind === 'teaching')
function onDeepen(topic) {
  store.deepenLesson(topic, props.report.guild)
}

// 這份回報是「誰做的」:從 adventurer_id(session_id)反查冒險者職業身份。
// 反查不到(如剛派遣還沒被掃描)則退回只顯示公會。
const doer = computed(() => store.personaBySession[props.report.adventurer_id] || null)
// 可否「派冒險者接續」:要有真 session。櫃台書記的主動總結不是 session,不能召回。
const resumable = computed(() =>
  !!props.report.adventurer_id && props.report.adventurer_id !== RECEPTION_ID)

const OUTCOME_META = {
  success: { emoji: '✅', label: '達成' },
  partial: { emoji: '🟡', label: '部分達成' },
  failed: { emoji: '❌', label: '未達成' },
}
const REVIEW_META = {
  rejected: { emoji: '↩️', label: '已退回櫃台' },
}
const STATUS_TXT = {
  pending: '⏳ 待歸檔', processing: '⚙️ 歸檔中', done: '🗂 已歸檔', failed: '⚠️ 歸檔失敗',
}

// ── 幕後審核 ──
const busy = ref(false)
async function reject() {
  const note = window.prompt('退回這份報告給櫃台重寫。\n可填退回原因(哪裡不清楚),留空也可以:', '')
  if (note === null) return
  busy.value = true
  try { await store.reviewReject(props.report.id, note || null) } finally { busy.value = false }
}
function cont() {
  if (!props.report.adventurer_id) return
  // 只是開啟面板讓使用者填指示後才真正派工,此處不需先確認
  store.continueFromReport(props.report)
}
</script>

<template>
  <!-- 教學卡:自足學習介面,不套櫃檯報告框(也不跑合成,見後端) -->
  <ResultCard v-if="isTeaching" :card="report.card" @deepen="onDeepen" />

  <!-- 一般回報(coding / 文字):完整櫃檯報告 -->
  <div v-else class="crep">
    <div class="crep__head">
      📄 櫃台回報
      <span v-if="doer || report.guild" class="crep__doer"
            :title="doer ? `由 ${doer.guild} 公會的${doer.klass}完成` : `${report.guild} 公會`">
        <template v-if="doer">{{ doer.icon }} {{ doer.klass }}</template>
        <template v-else>🏰 {{ report.guild }}</template>
      </span>
      <span class="crep__oc">
        {{ (OUTCOME_META[report.outcome] || OUTCOME_META.success).emoji }}
        {{ (OUTCOME_META[report.outcome] || OUTCOME_META.success).label }}
      </span>
      <span class="crep__st dim">{{ STATUS_TXT[report.status] || report.status }}</span>
      <span v-if="REVIEW_META[report.review]" class="crep__rev"
            :class="'crep__rev--' + report.review">
        {{ REVIEW_META[report.review].emoji }} {{ REVIEW_META[report.review].label }}
      </span>
    </div>
    <!-- ⓿ 退回原因:被退回時擺最上方、最醒目 —— 一眼就能找到「為什麼被退」與說明。 -->
    <div v-if="report.review === 'rejected' && report.review_note"
         class="crep__reject">
      <div class="crep__reject-h">↩️ 為什麼被退回</div>
      <Markdown :text="report.review_note" class="crep__reject-body" />
    </div>

    <!-- 職業結果卡:coding → IDE/diff 皮膚(取代下方純檔名清單)-->
    <ResultCard v-if="hasCard" :card="report.card" />

    <!-- ❶ 成果 —— 這份回報的主角,放大、置頂強調 -->
    <div v-if="report.summary" class="crep__hero">
      <div class="crep__hero-h">🏆 成果</div>
      <Markdown :text="report.summary" class="crep__hero-body" />
    </div>
    <div v-if="!hasCard && report.files_changed?.length" class="crep__field">
      <b>📄 改動檔案 <span class="dim">({{ report.files_changed.length }})</span></b>
      <ul class="crep__files">
        <li v-for="f in report.files_changed" :key="f">
          <span class="crep__file-i">📄</span><code>{{ f }}</code>
        </li>
      </ul>
    </div>
    <div v-if="report.lessons" class="crep__field">
      <b>💡 心得</b><Markdown :text="report.lessons" />
    </div>
    <div v-if="report.result" class="crep__field crep__field--rev">
      <b>🧝‍♀️ 櫃台審查</b><Markdown :text="report.result" />
    </div>

    <!-- 幕後審核:退回櫃台重寫 / 派冒險者接續(認可對 AI 無作用,已移除)-->
    <div v-if="judge" class="crep__judge">
      <div class="crep__judge-btns">
        <button class="jbtn jbtn--redo" :disabled="busy" @click="reject">
          ✍️ 退回櫃台重寫
        </button>
        <button class="jbtn jbtn--fix" :disabled="!resumable" @click="cont"
                :title="resumable ? '' : '這份報告沒有對應的 session(或為櫃台書記的主動總結),無法召回'">
          🔧 派冒險者接續
        </button>
      </div>
      <div class="dim crep__judge-hint">
        報告寫不清楚 → 退回櫃台重寫;實際工作沒做好 → 派冒險者接續(召回原 session 改)
      </div>
    </div>
  </div>
</template>
