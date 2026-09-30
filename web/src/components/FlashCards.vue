<script setup>
// 📚 書記官職業的結果皮膚:把教學型結果渲染成「字卡」,點一下翻面看答案 ——
// 而不是一長串條列文字。資料格式:{ headline, cards:[{front, back}] }
import { ref } from 'vue'
import LessonPlayer from './LessonPlayer.vue'
import MiniChart from './MiniChart.vue'

defineProps({
  card: { type: Object, required: true },
})
const emit = defineEmits(['deepen']) // 延伸主題 / 自己打 → 派新的教學委託往下學

const playing = ref(false) // 互動學習模式(一題一題作答)
const flipped = ref(new Set())
const customTopic = ref('')
function deepenCustom() {
  const t = customTopic.value.trim()
  if (!t) return
  emit('deepen', t)
  customTopic.value = ''
}
function flip(i) {
  const s = new Set(flipped.value)
  s.has(i) ? s.delete(i) : s.add(i)
  flipped.value = s
}
</script>

<template>
  <div class="fcard">
    <!-- 互動學習模式:一題一題作答 -->
    <LessonPlayer v-if="playing" :card="card"
                  @deepen="emit('deepen', $event)" @exit="playing = false" />

    <template v-else>
    <div class="fcard__headline">
      <span class="fcard__book">📚</span>
      <b>{{ card.headline || '教學字卡' }}</b>
      <button v-if="card.cards?.length" class="fcard__play" @click="playing = true">
        ▶️ 開始學習
      </button>
    </div>

    <div class="fcard__grid">
      <button
        v-for="(c, i) in card.cards"
        :key="i"
        class="flip"
        :class="{ 'is-flipped': flipped.has(i) }"
        @click="flip(i)"
      >
        <div class="flip__inner">
          <div class="flip__face flip__front">
            <span class="flip__tag flip__tag--q">Q{{ i + 1 }}</span>
            <div class="flip__text">{{ c.front }}</div>
            <span class="flip__hint">點一下看答案 →</span>
          </div>
          <div class="flip__face flip__back">
            <span class="flip__tag flip__tag--a">A</span>
            <div class="flip__text">{{ c.back }}</div>
            <MiniChart v-if="c.chart" :chart="c.chart" />
          </div>
        </div>
      </button>
      <div v-if="!card.cards?.length" class="fcard__empty">這份教學沒有字卡內容。</div>
    </div>

    <!-- 延伸深入:點 AI 建議的主題,或自己打一個 → 一鍵派新委託往下學 -->
    <div class="fcard__next">
      <div class="fcard__next-h">想更深入?繼續學 👇</div>
      <button v-for="(t, i) in card.next" :key="i" class="fcard__next-btn"
              @click="emit('deepen', t)">
        📚 {{ t }} <span class="fcard__next-arrow">→</span>
      </button>
      <div class="fcard__next-custom">
        <input v-model="customTopic" placeholder="或自己打一個想學的主題…"
               @keyup.enter="deepenCustom" />
        <button class="btn-primary" :disabled="!customTopic.trim()" @click="deepenCustom">
          學這個
        </button>
      </div>
    </div>
    </template>
  </div>
</template>
