<script setup>
// 🔧 機關師職業的結果皮膚:成果不是 code diff,而是「跑了哪些指令、動了哪些服務/設定」。
// 資料來自後端客觀收集的 Bash/PowerShell 指令 + 改動的設定檔 + LLM 的 headline/solved/services。
import { computed } from 'vue'
import { problemKind } from '../labels'

const props = defineProps({
  // { headline, category, solved, steps:[{cmd,desc}], services:[], files:[{path,why,adds,dels}], notes }
  card: { type: Object, required: true },
})

const kind = computed(() => problemKind(props.card.category))
const steps = computed(() => props.card.steps || [])
const services = computed(() => props.card.services || [])
const files = computed(() => props.card.files || [])

function baseName(p) {
  return (p || '').split('/').pop()
}
function dirName(p) {
  const parts = (p || '').split('/')
  parts.pop()
  return parts.join('/')
}
</script>

<template>
  <div class="ocard">
    <div class="ocard__headline">
      <span class="ocard__gear">🔧</span>
      <span v-if="kind" class="ocard__kind"
            :style="{ '--kc': kind.color }" :title="'問題類型:' + kind.label">
        {{ kind.icon }} {{ kind.label }}
      </span>
      <b>{{ card.headline || '完成一項機關調校' }}</b>
      <span class="ocard__summary" v-if="steps.length">
        跑了 {{ steps.length }} 條指令
      </span>
    </div>

    <!-- 解決了哪個問題:一句話講清楚 -->
    <div v-if="card.solved" class="ocard__solved">
      <span class="ocard__solved-l">解決</span>{{ card.solved }}
    </div>

    <!-- 動到的服務/容器/主機:一眼看出影響範圍 -->
    <div v-if="services.length" class="ocard__services">
      <span class="ocard__services-l">⚙️ 動到</span>
      <span v-for="s in services" :key="s" class="ocard__svc">{{ s }}</span>
    </div>

    <!-- 指令時間線:終端機風格,機關師皮膚的主角 -->
    <div v-if="steps.length" class="ocard__terminal">
      <div v-for="(s, i) in steps" :key="i" class="ocard__step">
        <div class="ocard__cmdline">
          <span class="ocard__prompt">$</span>
          <code class="ocard__cmd">{{ s.cmd }}</code>
        </div>
        <div v-if="s.desc" class="ocard__desc">{{ s.desc }}</div>
      </div>
    </div>

    <!-- 動到的設定檔(機關師常改 config,但不是重點 → 緊湊清單,不展開 diff)-->
    <div v-if="files.length" class="ocard__files">
      <div class="ocard__files-h">📄 設定檔異動 <span class="dim">({{ files.length }})</span></div>
      <div v-for="(f, i) in files" :key="f.path + i" class="ocard__file">
        <span class="ocard__fname">{{ baseName(f.path) }}</span>
        <span class="ocard__fdir">{{ dirName(f.path) }}</span>
        <span class="ocard__fstat">
          <span class="ocard__stat-add" v-if="f.adds">+{{ f.adds }}</span>
          <span class="ocard__stat-del" v-if="f.dels">-{{ f.dels }}</span>
        </span>
        <span v-if="f.why" class="ocard__why">{{ f.why }}</span>
      </div>
    </div>

    <div v-if="!steps.length && !files.length" class="ocard__nodiff">
      這次沒有實際動作(純查看 / 診斷)。
    </div>
    <div v-if="card.notes" class="ocard__notes">📌 {{ card.notes }}</div>
  </div>
</template>
