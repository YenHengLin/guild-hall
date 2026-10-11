<script setup>
// 🌿 斥候職業的結果皮膚:成果是「出了什麼貨」—— 送出的 commit、推去哪、部署指令,不是 code diff。
// 資料來自 LLM 回報的 commit / 目標 + 後端客觀收集並篩選出的 git/部署指令。
import { computed } from 'vue'
import { problemKind } from '../labels'

const props = defineProps({
  // { headline, category, solved, target, commits:[], steps:[{cmd,desc}], notes }
  card: { type: Object, required: true },
})

const kind = computed(() => problemKind(props.card.category))
const commits = computed(() => props.card.commits || [])
const steps = computed(() => props.card.steps || [])
</script>

<template>
  <div class="scard">
    <div class="scard__headline">
      <span class="scard__gear">🌿</span>
      <span v-if="kind" class="scard__kind"
            :style="{ '--kc': kind.color }" :title="'問題類型:' + kind.label">
        {{ kind.icon }} {{ kind.label }}
      </span>
      <b>{{ card.headline || '完成一次出貨' }}</b>
      <span v-if="card.target" class="scard__target" :title="'推送目標'">
        🎯 {{ card.target }}
      </span>
    </div>

    <div v-if="card.solved" class="scard__solved">
      <span class="scard__solved-l">交付</span>{{ card.solved }}
    </div>

    <!-- 送出的 commit:像 git log 一樣一列一筆,斥候皮膚的主角 -->
    <div v-if="commits.length" class="scard__commits">
      <div class="scard__commits-h">📦 送出的 commit <span class="dim">({{ commits.length }})</span></div>
      <div v-for="(c, i) in commits" :key="i" class="scard__commit">
        <span class="scard__node">●</span><code>{{ c }}</code>
      </div>
    </div>

    <!-- 出貨 / 部署指令(git push、deploy 等):終端機風格 -->
    <div v-if="steps.length" class="scard__cmds">
      <div class="scard__cmds-h">🚀 出貨指令 <span class="dim">({{ steps.length }})</span></div>
      <div v-for="(s, i) in steps" :key="i" class="scard__cmdline">
        <span class="scard__prompt">$</span><code>{{ s.cmd }}</code>
      </div>
    </div>

    <div v-if="!commits.length && !steps.length" class="scard__nodiff">
      這次沒有實際出貨(未送出 commit / 推送)。
    </div>
    <div v-if="card.notes" class="scard__notes">📌 {{ card.notes }}</div>
  </div>
</template>
