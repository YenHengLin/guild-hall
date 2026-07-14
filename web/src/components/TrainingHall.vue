<script setup>
import { computed, ref } from 'vue'
import { useGuildStore } from '../stores/guild'

const emit = defineEmits(['close'])
const store = useGuildStore()

const ROUTE_LABEL = {
  memory: '🧠 記住通用能力', skill: '📜 進化 / 新建 skill',
  tool: '🔧 採用更好的工具', script: '⚙️ 自己寫工具',
}
const pending = computed(() => store.proposals.filter((p) => p.status === 'pending'))
const decided = computed(() => store.proposals.filter((p) => p.status !== 'pending'))

// 可進化的冒險者(直接從這裡送訓練)
const evolvable = computed(() => store.evolutions.filter((e) => e.available > 0))

const previewId = ref(null)
const err = ref(null)

async function decide(p, approve) {
  err.value = null
  let note = null
  if (!approve) note = prompt('駁回原因(可留空):') || null
  else if (!confirm(`批准提案「${p.name}」?\n將寫入:${p.artifact_path || '(無檔案,僅登記)'}`)) return
  err.value = await store.decideProposal(p.id, approve, note)
}
async function doDistill() {
  err.value = null
  if (!confirm('全公會提煉會 spawn 一個 headless Claude 分析近期回報(花 token),繼續?')) return
  await store.distill()
  if (store.trainError) err.value = store.trainError
}
async function doTrain(e) {
  err.value = null
  if (!confirm(`送「${e.name}」進訓練所?會 spawn headless Claude 分析(花 token)。`)) return
  await store.train(e.adventurer_id)
  if (store.trainError) err.value = store.trainError
}
</script>

<template>
  <div class="report-backdrop" @click.self="emit('close')">
    <div class="training">
      <div class="training__head">
        <b>🎓 訓練所</b>
        <span class="training__sub">分析解決過的問題 → 產進化提案 → 你批准才落地</span>
        <button class="btn-ghost" @click="emit('close')">✕</button>
      </div>

      <div class="training__toolbar">
        <button class="btn-primary" :disabled="store.training" @click="doDistill">
          {{ store.training ? '⏳ 教官分析中…' : '⚗️ 全公會提煉(跨任務通則)' }}
        </button>
        <span v-if="err" class="dispatch__err">⚠️ {{ err }}</span>
      </div>

      <!-- 可進化的冒險者 -->
      <div v-if="evolvable.length" class="training__sec">
        <div class="training__sec-t">✨ 可進化的冒險者</div>
        <div v-for="e in evolvable" :key="e.adventurer_id" class="training__cand">
          <span class="training__cand-name">{{ e.name }}</span>
          <span class="training__cand-meta">
            {{ e.guild || '?' }} · 完成 {{ e.completed }} 次 · 可進化 {{ e.available }}
          </span>
          <button class="btn-primary" :disabled="store.training" @click="doTrain(e)">
            🎓 送訓練所
          </button>
        </div>
      </div>

      <!-- 待批准提案 -->
      <div class="training__sec">
        <div class="training__sec-t">📋 待批准提案 ({{ pending.length }})</div>
        <div v-if="!pending.length" class="board__empty">
          沒有待批准的提案。從上方送冒險者進訓練所,或執行全公會提煉。
        </div>
        <div v-for="p in pending" :key="p.id" class="proposal">
          <div class="proposal__head">
            <b>{{ ROUTE_LABEL[p.route] || p.route }}:{{ p.name }}</b>
            <span class="training__cand-meta">
              {{ p.adventurer_id ? `冒險者 ${p.adventurer_id.slice(0, 8)}` : '全公會提煉' }}
              <template v-if="p.guild"> · {{ p.guild }}</template>
            </span>
          </div>
          <div v-if="p.detail" class="proposal__detail">{{ p.detail }}</div>
          <div v-if="p.artifact_path" class="proposal__path">📁 {{ p.artifact_path }}</div>
          <pre v-if="previewId === p.id && p.artifact_content"
               class="proposal__preview">{{ p.artifact_content }}</pre>
          <div class="proposal__actions">
            <button v-if="p.artifact_content" class="btn-ghost"
                    @click="previewId = previewId === p.id ? null : p.id">
              {{ previewId === p.id ? '收起內容' : '👀 預覽內容' }}
            </button>
            <button class="btn-primary" @click="decide(p, true)">✅ 批准落地</button>
            <button class="btn-ghost" @click="decide(p, false)">🚫 駁回</button>
          </div>
        </div>
      </div>

      <!-- 歷史裁決 -->
      <div v-if="decided.length" class="training__sec">
        <div class="training__sec-t">🗂 歷史裁決</div>
        <div v-for="p in decided.slice(0, 10)" :key="'d' + p.id" class="proposal proposal--done">
          <span>{{ p.status === 'approved' ? '✅' : '🚫' }}</span>
          <span class="proposal__hist-name">{{ p.name }}</span>
          <span class="training__cand-meta">{{ ROUTE_LABEL[p.route] || p.route }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
