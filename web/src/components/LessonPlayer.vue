<script setup>
// 互動學習模式(多鄰國風):一題一題呈現,讓使用者「作答」而非只讀。
// 有 options → 單選小測驗 + 即時對錯回饋;沒有 → 看答案 + 自評。附進度條與完成畫面。
import { ref, computed } from 'vue'
import MiniChart from './MiniChart.vue'

const props = defineProps({ card: { type: Object, required: true } })
const emit = defineEmits(['deepen', 'exit'])

const cards = computed(() => props.card.cards || [])
const idx = ref(0)
const picked = ref(null)     // 選了哪個選項(單選題)
const revealed = ref(false)  // 已作答 / 已看答案
const done = ref(false)
const correct = ref(0)       // 答對題數
const customTopic = ref('')

const cur = computed(() => cards.value[idx.value] || null)
const hasOptions = computed(() =>
  Array.isArray(cur.value?.options) && cur.value.options.length >= 2)
const progress = computed(() =>
  cards.value.length ? Math.round((idx.value / cards.value.length) * 100) : 0)

function pick(i) {
  if (revealed.value) return
  picked.value = i
  revealed.value = true
  if (i === cur.value.answer) correct.value++
}
function reveal() { revealed.value = true }
function next() {
  if (idx.value + 1 >= cards.value.length) { done.value = true; return }
  idx.value++
  picked.value = null
  revealed.value = false
}
function restart() {
  idx.value = 0; picked.value = null; revealed.value = false; done.value = false; correct.value = 0
}
function deepenCustom() {
  const t = customTopic.value.trim()
  if (!t) return
  emit('deepen', t); customTopic.value = ''
}
function optClass(i) {
  if (!revealed.value) return ''
  if (i === cur.value.answer) return 'is-correct'
  if (i === picked.value) return 'is-wrong'
  return ''
}
</script>

<template>
  <div class="lp">
    <div class="lp__bar">
      <button class="lp__exit" @click="emit('exit')" title="回到總覽">✕</button>
      <div class="lp__prog"><i :style="{ width: (done ? 100 : progress) + '%' }"></i></div>
      <span class="lp__count">{{ done ? cards.length : idx + 1 }}/{{ cards.length }}</span>
    </div>

    <!-- 題目 -->
    <div v-if="!done && cur" class="lp__card">
      <div class="lp__q">{{ cur.front }}</div>

      <div v-if="hasOptions" class="lp__opts">
        <button v-for="(o, i) in cur.options" :key="i" class="lp__opt" :class="optClass(i)"
                :disabled="revealed" @click="pick(i)">
          <span class="lp__opt-t">{{ o }}</span>
          <span v-if="revealed && i === cur.answer" class="lp__mark">✓</span>
          <span v-else-if="revealed && i === picked" class="lp__mark">✗</span>
        </button>
      </div>
      <button v-else-if="!revealed" class="lp__reveal" @click="reveal">看答案</button>

      <div v-if="revealed" class="lp__answer">
        <div v-if="hasOptions" class="lp__verdict" :class="picked === cur.answer ? 'ok' : 'no'">
          {{ picked === cur.answer ? '答對了!🎉' : '沒關係,看解釋 👇' }}
        </div>
        <div class="lp__back">{{ cur.back }}</div>
        <MiniChart v-if="cur.chart" :chart="cur.chart" />
        <button class="btn-primary lp__next" @click="next">
          {{ idx + 1 >= cards.length ? '完成 →' : '下一題 →' }}
        </button>
      </div>
    </div>

    <!-- 完成畫面 -->
    <div v-else-if="done" class="lp__done">
      <div class="lp__done-emoji">🎉</div>
      <div class="lp__done-h">完成!</div>
      <div class="lp__done-sub">
        學了 {{ cards.length }} 個重點<template v-if="cards.some((c) => c.options)"> · 答對 {{ correct }}/{{ cards.length }}</template>
      </div>
      <button class="btn-ghost lp__restart" @click="restart">🔁 再複習一次</button>

      <div class="lp__next-block">
        <div class="lp__next-h">想更深入?繼續學 👇</div>
        <button v-for="(t, i) in (card.next || [])" :key="i" class="fcard__next-btn"
                @click="emit('deepen', t)">
          📚 {{ t }} <span class="fcard__next-arrow">→</span>
        </button>
        <div class="fcard__next-custom">
          <input v-model="customTopic" placeholder="或自己打一個想學的主題…" @keyup.enter="deepenCustom" />
          <button class="btn-primary" :disabled="!customTopic.trim()" @click="deepenCustom">學這個</button>
        </div>
      </div>
    </div>
  </div>
</template>
