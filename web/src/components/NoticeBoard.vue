<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useGuildStore } from '../stores/guild'
import { statusMeta, relativeTime, RANKS, rankOf } from '../labels'

const props = defineProps({
  quests: { type: Array, default: () => [] }, // session 自動衍生(進行中)
  commissions: { type: Array, default: () => [] }, // 使用者自己貼
})
const emit = defineEmits(['open'])
const store = useGuildStore()

const sessionQuests = computed(() => props.quests.slice(0, 5))
const openList = computed(() => props.commissions.filter((c) => c.status === 'open'))
const runningList = computed(() =>
  props.commissions.filter((c) => ['working', 'blocked', 'failed'].includes(c.status)))
const doneList = computed(() => props.commissions.filter((c) => c.status === 'done'))

const COMMISSION_META = {
  working: { emoji: '🏃', label: '執行中' },
  blocked: { emoji: '🧱', label: '待指示' },
  failed: { emoji: '💥', label: '失敗' },
}

// ── 貼委託表單 ──
const showForm = ref(false)
const title = ref('')
const note = ref('')
const goal = ref('')
const formGuild = ref('') // guild path,派遣工作資料夾
const formModel = ref('') // 冒險者位階(派遣模型)
async function post() {
  if (!title.value.trim()) return
  const g = store.guilds.find((x) => x.path === formGuild.value)
  const ok = await store.addCommission({
    title: title.value,
    note: note.value || null,
    goal: goal.value || null,
    guild: g?.name || null,
    cwd: formGuild.value || null,
    model: formModel.value || null,
  })
  if (ok) {
    title.value = ''; note.value = ''; goal.value = ''
    showForm.value = false
  }
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
function toggleExpand(c) {
  actionErr.value = null
  if (expandedId.value === c.id) { expandedId.value = null; return }
  expandedId.value = c.id
  goalEdit.value = c.goal || c.title
  dispatchModel.value = c.model || ''
  editingGoal.value = false
  events.value = []
  refreshEvents()
}
watch(expandedId, (id) => {
  clearInterval(evTimer)
  evTimer = null
  if (id != null) evTimer = setInterval(refreshEvents, 4000)
})
onUnmounted(() => clearInterval(evTimer))

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
  if (!confirm(`派遣${rank.short}冒險者執行「${c.title}」?\n會實際跑 Claude、花 token,並可能修改 ${cwd} 內的檔案。`)) return
  actionErr.value = await store.dispatch(c.id, cwd, dispatchModel.value)
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
</script>

<template>
  <div class="board">
    <div class="board__title">
      📌 委託布告欄
      <button class="board__add" @click="showForm = !showForm">
        {{ showForm ? '✕' : '＋ 貼委託' }}
      </button>
    </div>

    <!-- 貼新委託 -->
    <div v-if="showForm" class="board__form">
      <input v-model="title" placeholder="想做的事(委託標題)" @keyup.enter="post" />
      <input v-model="goal" placeholder="明確目標(派遣時給冒險者;可省略)" />
      <input v-model="note" placeholder="備註(可省略)" @keyup.enter="post" />
      <select v-model="formGuild" title="派遣時的工作資料夾">
        <option value="">(不指定公會,僅貼板)</option>
        <option v-for="g in store.guilds" :key="g.dir_key" :value="g.path">
          🏰 {{ g.name }}
        </option>
      </select>
      <select v-model="formModel" title="冒險者位階:派遣時用哪個模型">
        <option v-for="r in RANKS" :key="r.value" :value="r.value">{{ r.label }}</option>
      </select>
      <button class="btn-primary" @click="post">釘上布告欄</button>
    </div>

    <div class="board__pins">
      <!-- 派遣中 / 受阻 / 失敗 -->
      <template v-for="c in runningList" :key="'r' + c.id">
        <button class="quest quest--running" :class="'quest--' + c.status"
                @click="toggleExpand(c)"
                :title="c.goal || c.title">
          <span class="quest__emoji">{{ COMMISSION_META[c.status].emoji }}</span>
          <span class="quest__text">{{ c.title }}</span>
          <span class="quest__guild">
            {{ rankOf(c.model).short }} {{ COMMISSION_META[c.status].label }}
            <template v-if="c.rounds"> · 第{{ c.rounds }}回合</template>
          </span>
        </button>
        <div v-if="expandedId === c.id" class="dispatch">
          <!-- 目標(可中途調整)-->
          <div class="dispatch__goal">
            <template v-if="editingGoal">
              <input v-model="goalEdit" @keyup.enter="saveGoal(c)" />
              <button class="btn-primary" @click="saveGoal(c)">🎯 更新目標</button>
            </template>
            <template v-else>
              <span>🎯 {{ c.goal || c.title }}</span>
              <button class="btn-ghost" @click="editingGoal = true">✏️ 調整</button>
            </template>
          </div>
          <!-- 進度時間線 -->
          <div class="dispatch__events">
            <div v-for="e in events" :key="e.id" class="dispatch__ev"
                 :class="'dispatch__ev--' + e.type">
              <span class="dispatch__ev-tag">
                {{ e.type === 'steer' ? '🗣' : e.type === 'system' ? '📯' : `R${e.round || '?'}` }}
              </span>
              <span>{{ e.content }}</span>
            </div>
            <div v-if="!events.length" class="board__empty">尚無進度回報…</div>
          </div>
          <!-- 中途指示 -->
          <div class="dispatch__steer">
            <input v-model="steerText" placeholder="下中途指示(下一回合注入)"
                   @keyup.enter="doSteer(c)" />
            <button class="btn-primary" @click="doSteer(c)">送出</button>
          </div>
          <div class="dispatch__actions">
            <button v-if="c.status === 'working'" class="btn-ghost"
                    @click="store.stopDispatch(c.id)">⏹ 停止派遣</button>
            <template v-else>
              <select v-model="dispatchModel" title="冒險者位階">
                <option v-for="r in RANKS" :key="r.value" :value="r.value">{{ r.label }}</option>
              </select>
              <button class="btn-primary" @click="doDispatch(c)">
                🚀 再派遣(續同一位冒險者)
              </button>
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
              <span>🎯 {{ c.goal || c.title }}</span>
              <button class="btn-ghost" @click="editingGoal = true">✏️ 調整</button>
            </template>
          </div>
          <div v-if="events.length" class="dispatch__events">
            <div v-for="e in events" :key="e.id" class="dispatch__ev"
                 :class="'dispatch__ev--' + e.type">
              <span class="dispatch__ev-tag">
                {{ e.type === 'steer' ? '🗣' : e.type === 'system' ? '📯' : `R${e.round || '?'}` }}
              </span>
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
            <button class="btn-ghost" @click="store.toggleCommission(c)">✅ 標記完成</button>
          </div>
          <div v-if="actionErr" class="dispatch__err">⚠️ {{ actionErr }}</div>
        </div>
      </template>

      <!-- 已完成 -->
      <button
        v-for="c in doneList"
        :key="'cd' + c.id"
        class="quest quest--done"
        @click="store.toggleCommission(c)"
        title="點一下取消完成"
      >
        <span class="quest__emoji">✅</span>
        <span class="quest__text">{{ c.title }}</span>
        <span class="quest__guild" v-if="c.rounds">🤖 {{ c.rounds }}回合完成</span>
        <span class="quest__x" @click.stop="store.removeCommission(c.id)" title="刪除委託">🗑</span>
      </button>

      <!-- session 自動衍生:進行中 -->
      <div v-if="sessionQuests.length" class="board__sub">— 進行中的冒險 —</div>
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

      <div v-if="openList.length === 0 && runningList.length === 0 && doneList.length === 0 && sessionQuests.length === 0"
           class="board__empty">
        還沒有委託。點右上「＋ 貼委託」貼一張你想做的事。
      </div>
    </div>
  </div>
</template>
