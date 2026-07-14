<script setup>
import { computed } from 'vue'
import { shortTokens } from '../labels'

const props = defineProps({ report: Object, queue: Object })
const emit = defineEmits(['open'])

const today = computed(() => props.report?.today || {})
const streak = computed(() => props.report?.streak || 0)
const pending = computed(() => (props.queue?.pending || 0) + (props.queue?.processing || 0))
const doneToday = computed(() => props.queue?.done || 0)
const recLevel = computed(() => props.queue?.reception_level || 1)
// 依今日產出給小姐一句播報
const line = computed(() => {
  const t = today.value
  if (!t.actions) return '今天還沒有冒險者出勤喔~'
  return `今天完成 ${t.prompts || 0} 件委託、${t.actions || 0} 個行動!`
})
</script>

<template>
  <button class="desk" @click="emit('open')" title="點我看完整戰報">
    <div class="desk__counter">
      <span class="desk__lady">🧝‍♀️</span>
      <span class="desk__lv" title="櫃台等級(處理越多回報越高)">Lv.{{ recLevel }}</span>
      <span v-if="pending > 0" class="desk__pending" title="待櫃台記錄的回報">📥 {{ pending }}</span>
    </div>
    <div class="desk__bubble">
      <div class="desk__line">{{ line }}</div>
      <div class="desk__stats">
        <span>🗡️ {{ today.prompts || 0 }} 委託</span>
        <span>⚡ {{ today.actions || 0 }} 行動</span>
        <span>✨ {{ shortTokens(today.tokens || 0) }}</span>
        <span v-if="streak > 0" class="desk__streak">🔥 {{ streak }} 連</span>
        <span v-if="doneToday > 0" class="desk__filed">🗂 {{ doneToday }} 已歸檔</span>
      </div>
      <div class="desk__cta">📊 查看戰報 / 回報紀錄 ›</div>
    </div>
  </button>
</template>
