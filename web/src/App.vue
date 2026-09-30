<script setup>
import { onMounted, onUnmounted, computed } from 'vue'
import { useGuildStore } from './stores/guild'
import HallScene from './components/HallScene.vue'
import AdventurerDrawer from './components/AdventurerDrawer.vue'
import ReportPanel from './components/ReportPanel.vue'
import TrainingHall from './components/TrainingHall.vue'
import MusePanel from './components/MusePanel.vue'
import LessonsPanel from './components/LessonsPanel.vue'
import { shortTokens } from './labels'

const store = useGuildStore()
const updated = computed(() =>
  store.lastUpdated ? store.lastUpdated.toLocaleTimeString('zh-TW') : '—',
)
const pendingProposals = computed(() =>
  store.proposals.filter((p) => p.status === 'pending').length,
)
function openMuse() {
  store.loadMuse() // 開面板時抓最新設定 + 花費 + 進度
  store.startMusePolling() // 面板開著時輪詢即時進度
  store.showMuse = true
}
function closeMuse() {
  store.stopMusePolling()
  store.showMuse = false
}

onMounted(() => {
  store.startPolling(15000)
  // 徵詢系統通知權限,好在委託完成 / 受阻時「跳出來」提醒(即使不在此視窗)
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
  }
})
onUnmounted(() => { store.stopPolling(); store.stopMusePolling() })
</script>

<template>
  <div class="hall">
    <div class="signboard">
      <h1>⚜️ 冒險者公會大廳 ⚜️</h1>
      <div class="sub">每個資料夾是一座公會,同職的 session 收斂成一位冒險者</div>
      <div class="stats">
        <span>🏰 公會 <b>{{ store.guilds.length }}</b></span>
        <span>👥 冒險者 <b>{{ store.totalPersonas }}</b></span>
        <span>🗺 冒險 <b>{{ store.totalAdventurers }}</b></span>
        <span>⚔️ 冒險中 <b>{{ store.totalActive }}</b></span>
        <span>🎖 總戰功 <b>{{ store.totalXp }}</b></span>
        <span class="dim">✨ 消耗 <b>{{ shortTokens(store.totalTokens) }}</b></span>
        <button class="signboard-btn" :class="{ 'signboard-btn--active': store.showTraining }"
                @click="store.showTraining = !store.showTraining"
                title="訓練所:進化提案審核 / 全公會提煉">
          🎓 訓練所<b v-if="store.evolvableCount > 0"> {{ store.evolvableCount }}</b><b
            v-if="pendingProposals > 0" class="signboard-btn__badge">{{ pendingProposals }}</b>
        </button>
        <button class="signboard-btn" :class="{ 'signboard-btn--active': store.showMuse }"
                @click="store.showMuse ? closeMuse() : openMuse()"
                title="公會夜巡:NPC 冒險者自主巡視各專案,產出改善靈感卡">
          🌙 夜巡<b v-if="store.newIdeaCount > 0" class="signboard-btn__badge">{{ store.newIdeaCount }}</b>
        </button>
        <button class="signboard-btn" :class="{ 'signboard-btn--active': store.showLessons }"
                @click="store.showLessons ? (store.showLessons = false) : store.openLessons()"
                title="學習書架:所有教學字卡依 AI 分類擺放,一眼找到、能往下鑽">
          📚 學習
        </button>
        <span class="dim" style="color: var(--parchment-dark)">🔄 {{ updated }}</span>
      </div>
    </div>

    <div v-if="store.error" class="err">⚠️ {{ store.error }}(後端是否已啟動於 :8848?)</div>
    <div v-else-if="store.loading" class="empty">📜 公會名冊召集中…</div>
    <div v-else-if="store.guilds.length === 0" class="empty">目前沒有任何公會活動紀錄</div>

    <HallScene
      v-else
      :guilds="store.guilds"
      :report="store.report"
      :queue="store.queue"
      :commissions="store.commissions"
      @open="store.open"
      @open-report="store.openReport"
    />

    <!-- 冒險者細節抽屜 -->
    <AdventurerDrawer
      v-if="store.selected"
      :adventurer="store.selected"
      @close="store.closeAdventurer"
    />

    <!-- 戰報面板 -->
    <ReportPanel
      v-if="store.showReport"
      :report="store.report"
      :queue="store.queue"
      @close="store.closeReport"
    />

    <!-- 訓練所(進化提案審核)-->
    <TrainingHall v-if="store.showTraining" @close="store.showTraining = false" />

    <!-- 公會夜巡(NPC 自主靈感)-->
    <MusePanel v-if="store.showMuse" @close="closeMuse" />

    <!-- 學習書架(教學字卡依分類擺放)-->
    <LessonsPanel v-if="store.showLessons" @close="store.showLessons = false" />

    <!-- 浮動通知:委託完成 / 受阻需回應 / 失敗 -->
    <div class="toasts">
      <transition-group name="toast">
        <div
          v-for="t in store.toasts"
          :key="t.id"
          class="toast"
          :class="'toast--' + t.kind"
          @click="store.focusCommission(t.cid)"
        >
          <span class="toast__icon">{{ t.icon }}</span>
          <div class="toast__body">
            <div class="toast__title">{{ t.title }}</div>
            <div class="toast__msg">{{ t.body }}</div>
            <div v-if="t.hint" class="toast__hint">{{ t.hint }}</div>
          </div>
          <button class="toast__x" @click.stop="store.dismissToast(t.id)" title="關閉">✕</button>
        </div>
      </transition-group>
    </div>
  </div>
</template>
