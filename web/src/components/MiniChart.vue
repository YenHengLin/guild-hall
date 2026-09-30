<script setup>
// 教學用輕量 SVG 圖表(不加圖表庫依賴)。支援折線(可多系列,各自正規化以看趨勢對照)
// 與長條(單系列)。用於把「股價 vs 財報」這類抽象關係畫成看得見的對照。
import { computed } from 'vue'

const props = defineProps({ chart: { type: Object, required: true } })

const W = 300, H = 140
const PAD_L = 10, PAD_R = 10, PAD_T = 10, PAD_B = 22
const plotW = W - PAD_L - PAD_R
const plotH = H - PAD_T - PAD_B
const COLORS = ['#b8860b', '#3f9142', '#5a6fb0']

const series = computed(() => props.chart.series || [])
const xlabels = computed(() => props.chart.xlabels || [])
const isBar = computed(() => props.chart.type === 'bar')

function xAt(i, len) {
  if (len <= 1) return PAD_L + plotW / 2
  return PAD_L + (i / (len - 1)) * plotW
}
function yAt(t) { return PAD_T + (1 - t) * plotH } // t: 0..1

// 每條系列各自正規化到自身 min~max → 不同量級(營收 vs 股價)也能比「形狀」
function normed(data) {
  const max = Math.max(...data), min = Math.min(...data)
  const range = max - min || 1
  return data.map((v) => (v - min) / range)
}

const lines = computed(() => series.value.map((s, si) => {
  const ns = normed(s.data)
  return {
    color: COLORS[si % COLORS.length],
    points: ns.map((t, i) => `${xAt(i, s.data.length)},${yAt(t)}`).join(' '),
    dots: ns.map((t, i) => ({ x: xAt(i, s.data.length), y: yAt(t) })),
  }
}))

const bars = computed(() => {
  const s = series.value[0]
  if (!s) return []
  const max = Math.max(...s.data) || 1
  const len = s.data.length
  const bw = (plotW / len) * 0.6
  return s.data.map((v, i) => {
    const h = (v / max) * plotH
    return { x: xAt(i, len) - bw / 2, y: PAD_T + plotH - h, w: bw, h, color: COLORS[0] }
  })
})
</script>

<template>
  <div class="mchart">
    <div v-if="chart.title" class="mchart__title">{{ chart.title }}</div>
    <div v-if="!isBar && series.length > 1" class="mchart__legend">
      <span v-for="(s, si) in series" :key="si" class="mchart__leg">
        <i :style="{ background: COLORS[si % COLORS.length] }"></i>{{ s.label }}
      </span>
    </div>
    <svg :viewBox="`0 0 ${W} ${H}`" class="mchart__svg" preserveAspectRatio="xMidYMid meet">
      <line :x1="PAD_L" :y1="PAD_T + plotH" :x2="W - PAD_R" :y2="PAD_T + plotH"
            stroke="#d0bd86" stroke-width="1" />
      <template v-if="isBar">
        <rect v-for="(b, i) in bars" :key="i" :x="b.x" :y="b.y" :width="b.w" :height="b.h"
              :fill="b.color" rx="2" />
      </template>
      <template v-else>
        <polyline v-for="(l, li) in lines" :key="li" :points="l.points" fill="none"
                  :stroke="l.color" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
        <template v-for="(l, li) in lines" :key="'d' + li">
          <circle v-for="(d, di) in l.dots" :key="di" :cx="d.x" :cy="d.y" r="2.5" :fill="l.color" />
        </template>
      </template>
      <text v-for="(xl, i) in xlabels" :key="i" :x="xAt(i, xlabels.length)" :y="H - 7"
            font-size="9" fill="#6b5c42" text-anchor="middle">{{ xl }}</text>
    </svg>
    <div v-if="chart.note" class="mchart__note">💡 {{ chart.note }}</div>
  </div>
</template>
