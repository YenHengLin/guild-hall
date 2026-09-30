<script setup>
// 📚 學習書架:把所有教學字卡依 AI 分類擺成一櫃一櫃,點課程展開翻牌字卡。
// 讓學習卡從「埋在櫃檯報告」變成「一眼找得到、能往下鑽」的學習介面。
import { ref } from 'vue'
import { useGuildStore } from '../stores/guild'
import { relativeTime } from '../labels'
import FlashCards from './FlashCards.vue'

const store = useGuildStore()
const emit = defineEmits(['close'])

const openId = ref(null)
function toggle(id) { openId.value = openId.value === id ? null : id }

async function onDeepen(topic, guild) {
  await store.deepenLesson(topic, guild)
  emit('close') // 派工後關面板,去布告欄看新一課跑完(完成後會自動收進書架)
}
</script>

<template>
  <div class="report-backdrop" @click.self="emit('close')">
    <div class="lessons">
      <button class="report__close" @click="emit('close')">✕</button>
      <div class="lessons__top">
        <h2>📚 學習書架 <span class="dim">依 AI 分類擺放的教學字卡</span></h2>
        <button v-if="store.lessons.length" class="btn-classify"
                :disabled="store.classifying" @click="store.reclassifyLessons()"
                title="用便宜模型替未分類 / 缺延伸主題的卡補上分類與延伸主題">
          {{ store.classifying ? '🪄 整理中…' : '🪄 AI 整理書架' }}
        </button>
      </div>

      <div v-if="!store.lessons.length" class="lessons__empty">
        還沒有任何學習卡。<br>
        派一張「🎨 呈現方式 → 📚 書記官」的委託教你點東西,完成後就會自動收進這裡。
      </div>

      <div v-for="cat in store.lessons" :key="cat.category" class="lshelf">
        <div class="lshelf__head">📁 {{ cat.category }} <span class="dim">({{ cat.count }})</span></div>
        <div class="lshelf__items">
          <div v-for="l in cat.lessons" :key="l.id" class="litem">
            <button class="litem__head" @click="toggle(l.id)">
              <span class="litem__caret">{{ openId === l.id ? '▾' : '▸' }}</span>
              <span class="litem__t">{{ l.headline }}</span>
              <span class="dim litem__meta">🏰 {{ l.guild || '—' }} · {{ relativeTime(l.created_at) }}</span>
            </button>
            <div v-if="openId === l.id" class="litem__body">
              <FlashCards :card="l.card" @deepen="(t) => onDeepen(t, l.guild)" />
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
