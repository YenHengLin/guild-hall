<script setup>
import { computed } from 'vue'

const props = defineProps({ days: { type: Array, default: () => [] } })

// 把連續天數切成每週一欄(7 天),GitHub 風格。days 已是舊→新排序。
const weeks = computed(() => {
  const out = []
  for (let i = 0; i < props.days.length; i += 7) {
    out.push(props.days.slice(i, i + 7))
  }
  return out
})

const total = computed(() => props.days.reduce((n, d) => n + d.count, 0))
const activeDays = computed(() => props.days.filter((d) => d.count > 0).length)

function tip(d) {
  return `${d.date} · ${d.count} 個行動`
}
</script>

<template>
  <div class="heat">
    <div class="heat__head">
      <b>🔥 活躍熱力圖</b>
      <span class="dim">近 12 週 · {{ activeDays }} 天出勤 · 共 {{ total }} 個行動</span>
    </div>
    <div class="heat__grid">
      <div v-for="(w, wi) in weeks" :key="wi" class="heat__col">
        <span
          v-for="d in w"
          :key="d.date"
          class="heat__cell"
          :data-lv="d.level"
          :title="tip(d)"
        ></span>
      </div>
    </div>
    <div class="heat__legend">
      <span class="dim">少</span>
      <span class="heat__cell" data-lv="0"></span>
      <span class="heat__cell" data-lv="1"></span>
      <span class="heat__cell" data-lv="2"></span>
      <span class="heat__cell" data-lv="3"></span>
      <span class="heat__cell" data-lv="4"></span>
      <span class="dim">多</span>
    </div>
  </div>
</template>
