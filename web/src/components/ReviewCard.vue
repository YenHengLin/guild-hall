<script setup>
// 🧪 鑑定師職業的結果皮膚:成果是「查了哪些項目、通過與否、總裁決」,不是 code diff。
// 資料來自 LLM 回報的查驗清單 + 總裁決,加上後端客觀收集的查驗指令(跑的 test 等)。
import { computed } from 'vue'
import { problemKind, verdictMeta } from '../labels'

const props = defineProps({
  // { headline, category, solved, verdict, checks:[{item,ok,note}], steps:[{cmd,desc}], notes }
  card: { type: Object, required: true },
})

const kind = computed(() => problemKind(props.card.category))
const verdict = computed(() => verdictMeta(props.card.verdict))
const checks = computed(() => props.card.checks || [])
const steps = computed(() => props.card.steps || [])
// 通過比例:X/Y 項通過,給卡片頂部一個量感
const passCount = computed(() => checks.value.filter((c) => c.ok).length)
</script>

<template>
  <div class="rcard" :class="'rcard--' + (card.verdict || 'warn')">
    <div class="rcard__headline">
      <span class="rcard__gear">🧪</span>
      <span v-if="kind" class="rcard__kind"
            :style="{ '--kc': kind.color }" :title="'問題類型:' + kind.label">
        {{ kind.icon }} {{ kind.label }}
      </span>
      <b>{{ card.headline || '完成一項查驗' }}</b>
      <span class="rcard__verdict" :style="{ '--vc': verdict.color }">
        {{ verdict.icon }} {{ verdict.label }}
        <span v-if="checks.length" class="rcard__ratio">{{ passCount }}/{{ checks.length }}</span>
      </span>
    </div>

    <div v-if="card.solved" class="rcard__solved">
      <span class="rcard__solved-l">結論</span>{{ card.solved }}
    </div>

    <!-- 查驗清單:每項 ✓/✗ + 說明 —— 鑑定師皮膚的主角 -->
    <div v-if="checks.length" class="rcard__checks">
      <div v-for="(c, i) in checks" :key="i" class="rcard__check"
           :class="c.ok ? 'is-ok' : 'is-bad'">
        <span class="rcard__mark">{{ c.ok ? '✓' : '✗' }}</span>
        <div class="rcard__ctext">
          <div class="rcard__citem">{{ c.item }}</div>
          <div v-if="c.note" class="rcard__cnote">{{ c.note }}</div>
        </div>
      </div>
    </div>

    <!-- 跑過的查驗指令(test 等):終端機風格小清單 -->
    <div v-if="steps.length" class="rcard__cmds">
      <div class="rcard__cmds-h">🔬 查驗指令 <span class="dim">({{ steps.length }})</span></div>
      <div v-for="(s, i) in steps" :key="i" class="rcard__cmdline">
        <span class="rcard__prompt">$</span><code>{{ s.cmd }}</code>
      </div>
    </div>

    <div v-if="!checks.length && !steps.length" class="rcard__nodiff">
      這次沒有明確的查驗項目。
    </div>
    <div v-if="card.notes" class="rcard__notes">📌 {{ card.notes }}</div>
  </div>
</template>
