<script setup>
import { computed } from 'vue'
import { useGuildStore } from '../stores/guild'
import { statusMeta, xpProgress, hashHue, classOf, relativeTime } from '../labels'

const props = defineProps({ adventurer: Object })
const emit = defineEmits(['open'])
const store = useGuildStore()

// persona(職業角色)帶有 quests 陣列;純 session 冒險者則無。
const isPersona = computed(() => Array.isArray(props.adventurer.quests))
const questCount = computed(() => props.adventurer.quest_count || 0)

const meta = computed(() => statusMeta(props.adventurer.status))
const xp = computed(() => xpProgress(props.adventurer.xp))
const hue = computed(() => hashHue(props.adventurer.id))
// persona 已自帶職業圖示/名稱;否則沿用前端推派規則
const cls = computed(() =>
  isPersona.value
    ? { icon: props.adventurer.icon, klass: props.adventurer.klass }
    : classOf(props.adventurer),
)

// 進化狀態:persona 需跨其所有冒險(session)聚合
const evoList = computed(() =>
  isPersona.value
    ? (props.adventurer.quests || []).map((q) => store.evoMap[q.id]).filter(Boolean)
    : [store.evoMap[props.adventurer.id]].filter(Boolean),
)
const canEvolve = computed(() => evoList.value.some((e) => (e.available || 0) > 0))
const learned = computed(() =>
  evoList.value.reduce((n, e) => n + (e.learned?.length || 0), 0),
)

// persona:有取名就用名字,否則用職業;session 用任務名
const name = computed(() =>
  isPersona.value
    ? (props.adventurer.name || props.adventurer.klass)
    : props.adventurer.title || props.adventurer.last_prompt?.slice(0, 14) || '無名冒險者',
)

// 🫧 即時動作氣泡:從 hook 活動找這位冒險者(或其任一趟 session)最新的動作
const ACTION_ICON = {
  Edit: '🖋️', Write: '🖋️', NotebookEdit: '🖋️', Read: '🔍', Grep: '🔍', Glob: '🔍',
  Bash: '⚙️', PowerShell: '⚙️', Skill: '📜', Task: '📋', TaskCreate: '📋',
  TaskUpdate: '📋', Agent: '🧙', WebSearch: '🌐', WebFetch: '🌐',
}
const FRESH_MS = 5 * 60 * 1000 // 5 分鐘內的動作才冒泡,避免顯示過期
const liveAction = computed(() => {
  const map = store.activityBySession
  const ids = isPersona.value ? (props.adventurer.quests || []).map((q) => q.id) : [props.adventurer.id]
  let best = null
  for (const id of ids) {
    const a = map[id]
    if (a && (!best || (a.ts || '') > (best.ts || ''))) best = a
  }
  if (!best || !best.ts) return null
  if (Date.now() - new Date(best.ts).getTime() > FRESH_MS) return null
  return best
})
// 點擊:persona 傳整個物件(store 需要 quests),session 傳 id
function onClick() {
  emit('open', isPersona.value ? props.adventurer : props.adventurer.id)
}
</script>

<template>
  <button
    class="sprite"
    :class="[`is-${adventurer.status}`, { 'can-evolve': canEvolve }]"
    :style="{ '--hue': hue, '--aura': meta.dot }"
    @click="onClick"
    :title="`${cls.klass} · ${name}${isPersona ? `(${questCount} 趟冒險)` : ''}\n${relativeTime(adventurer.last_active)}${canEvolve ? '\n✨ 可進化!' : ''}`"
  >
    <span class="sprite__status">{{ meta.emoji }}</span>
    <span v-if="canEvolve" class="sprite__evo">✨</span>
    <span v-if="isPersona && questCount > 1" class="sprite__quests" title="冒險趟數">×{{ questCount }}</span>

    <!-- 🫧 即時動作氣泡:正在做什麼 -->
    <span v-if="liveAction" class="sprite__bubble" :title="`${liveAction.tool}: ${liveAction.summary}`">
      <span class="sprite__bubble-i">{{ ACTION_ICON[liveAction.tool] || '✨' }}</span>
      <span class="sprite__bubble-t">{{ liveAction.summary || liveAction.tool }}</span>
    </span>

    <span class="sprite__fig">
      <span class="sprite__aura"></span>
      <span class="sprite__head"></span>
      <span class="sprite__body"></span>
      <span class="sprite__badge">{{ cls.icon }}</span>
      <span class="sprite__lvl">Lv.{{ xp.level }}</span>
      <span v-if="learned > 0" class="sprite__learned" title="已習得能力數">🎓{{ learned }}</span>
    </span>

    <span class="sprite__name">{{ name }}</span>

    <span class="sprite__xp">
      <i :style="{ width: xp.pct + '%' }"></i>
    </span>
  </button>
</template>
