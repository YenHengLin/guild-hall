<script setup>
// 右側「櫃台」側邊欄:點開就地展開(非蓋全螢的浮窗),可拖左緣加寬,
// 內含回報紀錄搜尋 —— 查以前的紀錄好找。重統計(熱力圖/帳本/成就)仍走完整戰報。
import { ref, computed } from 'vue'
import { useGuildStore } from '../stores/guild'
import CommissionReport from './CommissionReport.vue'
import { relativeTime } from '../labels'

const store = useGuildStore()
const open = ref(false)
const width = ref(360) // 展開寬度(可拖左緣調整)
const q = ref('') // 回報搜尋字串
const openId = ref(null) // 展開中的回報 id

const pending = computed(() =>
  (store.queue?.pending || 0) + (store.queue?.processing || 0))
const today = computed(() => store.report?.today || {})

const OUTCOME = { success: '✅', partial: '🟡', failed: '❌' }
const STATUS_TXT = {
  pending: '⏳ 待處理', processing: '⚙️ 處理中', done: '🗂 已歸檔', failed: '⚠️ 失敗',
}

const reports = computed(() => store.queue?.recent || [])
// 搜尋:標題 / 公會 / 摘要 / 心得(不分大小寫)
const filtered = computed(() => {
  const kw = q.value.trim().toLowerCase()
  if (!kw) return reports.value
  return reports.value.filter((r) => {
    const hay = [r.title, r.guild, r.summary, r.lessons]
      .filter(Boolean).join(' ').toLowerCase()
    return hay.includes(kw)
  })
})
function toggle(id) { openId.value = openId.value === id ? null : id }
function personaOf(r) { return store.personaBySession[r.adventurer_id] || null }

// 拖左緣調整寬度
let startX = 0; let startW = 0
function onGrip(e) {
  startX = e.clientX; startW = width.value
  window.addEventListener('pointermove', onGripMove)
  window.addEventListener('pointerup', onGripEnd)
  e.preventDefault()
}
function onGripMove(e) {
  // 往左拖(clientX 變小)→ 變寬
  width.value = Math.max(300, Math.min(900, startW + (startX - e.clientX)))
}
function onGripEnd() {
  window.removeEventListener('pointermove', onGripMove)
  window.removeEventListener('pointerup', onGripEnd)
}
</script>

<template>
  <div class="rsidebar" :class="{ 'rsidebar--open': open }">
    <!-- 收合狀態:窄 tab -->
    <button v-if="!open" class="rsidebar__tab" @click="open = true" title="展開櫃台">
      <span class="rsidebar__tab-i">🧝‍♀️</span>
      <span class="rsidebar__tab-t">櫃台</span>
      <span v-if="pending > 0" class="rsidebar__tab-badge">{{ pending }}</span>
    </button>

    <!-- 展開狀態:櫃台摘要 + 回報搜尋 + 紀錄清單 -->
    <div v-else class="rsidebar__panel" :style="{ width: width + 'px' }">
      <div class="rsidebar__grip" @pointerdown="onGrip" title="拖曳調整寬度"></div>
      <div class="rsidebar__head">
        🧝‍♀️ 櫃台
        <button class="rsidebar__close" @click="open = false" title="收合(任務區滿版)">»</button>
      </div>

      <!-- 今日摘要(點開完整戰報:統計/熱力圖/帳本/成就)-->
      <button class="rsidebar__summary" @click="store.openReport" title="看完整戰報(統計/熱力圖/成本/成就)">
        <div class="rsidebar__sum-line">
          今天 🗡️ {{ today.prompts || 0 }} 委託 · ⚡ {{ today.actions || 0 }} 行動
          <span v-if="pending > 0">· 📥 {{ pending }} 待處理</span>
        </div>
        <div class="rsidebar__sum-cta">📊 完整戰報 / 統計 ›</div>
      </button>

      <!-- 回報搜尋 -->
      <div class="rsidebar__search">
        <input v-model="q" placeholder="🔎 搜尋回報(標題 / 公會 / 內容)" />
        <span v-if="q" class="rsidebar__search-x" @click="q = ''" title="清除">✕</span>
      </div>
      <div class="rsidebar__count">
        回報紀錄 {{ filtered.length }}<span v-if="q" class="dim"> / 共 {{ reports.length }}</span>
      </div>

      <!-- 紀錄清單(就地展開看完整內容)-->
      <div class="rsidebar__reports">
        <div v-for="r in filtered" :key="r.id" class="rep" :class="`rep--${r.status}`">
          <div class="rep__head" @click="toggle(r.id)">
            <span class="rep__oc">{{ OUTCOME[r.outcome] || '✅' }}</span>
            <div class="rep__main">
              <div class="rep__title">{{ r.title }}</div>
              <div class="rep__meta">
                <span v-if="personaOf(r)" class="rep__doer">
                  {{ personaOf(r).icon }} {{ personaOf(r).klass }} ·
                </span>
                🏰 {{ r.guild || '—' }} · {{ STATUS_TXT[r.status] || r.status }}
                <span v-if="r.to_wiki === false" title="記憶特化在任務內,未寫入 llm wiki"> · 🧠 任務記憶</span>
                <span v-if="r.created_at"> · {{ relativeTime(r.created_at) }}</span>
              </div>
            </div>
            <span class="rep__caret">{{ openId === r.id ? '▲' : '▼' }}</span>
          </div>
          <div v-if="openId === r.id" class="rep__body">
            <CommissionReport :report="r" judge />
          </div>
        </div>
        <div v-if="!filtered.length" class="rsidebar__empty">
          {{ q ? '找不到符合的回報' : '還沒有任何回報紀錄' }}
        </div>
      </div>
    </div>
  </div>
</template>
