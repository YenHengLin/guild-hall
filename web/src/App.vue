<script setup>
import { onMounted, onUnmounted, computed } from 'vue'
import { useGuildStore } from './stores/guild'
import HallScene from './components/HallScene.vue'
import AdventurerDrawer from './components/AdventurerDrawer.vue'
import ReportPanel from './components/ReportPanel.vue'
import TrainingHall from './components/TrainingHall.vue'
import { shortTokens } from './labels'

const store = useGuildStore()
const updated = computed(() =>
  store.lastUpdated ? store.lastUpdated.toLocaleTimeString('zh-TW') : '—',
)
const pendingProposals = computed(() =>
  store.proposals.filter((p) => p.status === 'pending').length,
)

onMounted(() => store.startPolling(15000))
onUnmounted(() => store.stopPolling())
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
        <button class="signboard-btn" @click="store.showTraining = true"
                title="訓練所:進化提案審核 / 全公會提煉">
          🎓 訓練所<b v-if="store.evolvableCount > 0"> {{ store.evolvableCount }}</b><b
            v-if="pendingProposals > 0" class="signboard-btn__badge">{{ pendingProposals }}</b>
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
  </div>
</template>
