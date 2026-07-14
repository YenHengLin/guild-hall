<script setup>
import { computed, ref } from 'vue'
import Heatmap from './Heatmap.vue'
import { useGuildStore } from '../stores/guild'
import { shortTokens } from '../labels'

const props = defineProps({ report: Object, queue: Object })
const emit = defineEmits(['close'])
const store = useGuildStore()

const reports = computed(() => props.queue?.recent || [])
const OUTCOME = { success: '✅', partial: '🟡', failed: '❌' }
const STATUS_TXT = {
  pending: '⏳ 待處理', processing: '⚙️ 處理中', done: '🗂 已歸檔', failed: '⚠️ 失敗',
}
const REVIEW_BADGE = {
  approved: { t: '✅ 已認可', c: 'rev-ok' },
  rejected: { t: '↩️ 已退回', c: 'rev-no' },
}

// 展開看回報完整內容
const openId = ref(null)
function toggle(id) { openId.value = openId.value === id ? null : id }

// ── 幕後審核動作 ──
const busy = ref(0) // 正在處理的 report id
async function approve(r) {
  busy.value = r.id
  try { await store.reviewApprove(r.id) } finally { busy.value = 0 }
}
async function reject(r) {
  const note = window.prompt('退回這份報告給櫃台重寫。\n可填退回原因(哪裡不清楚),留空也可以:', '')
  if (note === null) return // 取消
  busy.value = r.id
  try { await store.reviewReject(r.id, note || null) } finally { busy.value = 0 }
}
function cont(r) {
  if (!r.adventurer_id) return
  if (!window.confirm('派這位冒險者接續改善(會 resume 原 session、實際改檔、花 token)。\n將開啟其面板讓你確認後派工。要繼續嗎?')) return
  store.continueFromReport(r)
}

// 櫃台小姐成長
const recLevel = computed(() => props.queue?.reception_level || 1)
const recNextAt = computed(() => props.queue?.reception_next_at)
const reviewSkills = computed(() => props.queue?.review_skills || [])

const today = computed(() => props.report?.today || {})
const week = computed(() => props.report?.week || {})
const achievements = computed(() => props.report?.achievements || [])
const unlocked = computed(() => achievements.value.filter((a) => a.unlocked).length)
const topGuilds = computed(() => props.report?.top_guilds_today || [])
const maxGuild = computed(() =>
  Math.max(1, ...topGuilds.value.map((g) => g.events)),
)
</script>

<template>
  <div class="report-backdrop" @click.self="emit('close')">
    <div class="report">
      <button class="report__close" @click="emit('close')">✕</button>
      <h2>📊 公會戰報</h2>

      <!-- 今日 / 本週數字 -->
      <div class="report__cards">
        <div class="rcard">
          <div class="rcard__k">今日委託</div>
          <div class="rcard__v">{{ today.prompts || 0 }}</div>
        </div>
        <div class="rcard">
          <div class="rcard__k">今日行動</div>
          <div class="rcard__v">{{ today.actions || 0 }}</div>
        </div>
        <div class="rcard">
          <div class="rcard__k">今日消耗</div>
          <div class="rcard__v">✨{{ shortTokens(today.tokens || 0) }}</div>
        </div>
        <div class="rcard">
          <div class="rcard__k">出勤公會</div>
          <div class="rcard__v">{{ today.active_guilds || 0 }}</div>
        </div>
        <div class="rcard rcard--week">
          <div class="rcard__k">本週委託 / 行動</div>
          <div class="rcard__v">{{ week.prompts || 0 }} / {{ week.actions || 0 }}</div>
        </div>
        <div class="rcard rcard--week">
          <div class="rcard__k">本週消耗</div>
          <div class="rcard__v">✨{{ shortTokens(week.tokens || 0) }}</div>
        </div>
      </div>

      <!-- 今日各公會排行 -->
      <div v-if="topGuilds.length" class="report__section">
        <b>🏅 今日最活躍公會</b>
        <div class="rank">
          <div v-for="(g, i) in topGuilds" :key="g.name" class="rank__row">
            <span class="rank__medal">{{ ['🥇','🥈','🥉'][i] || '·' }}</span>
            <span class="rank__name">{{ g.name }}</span>
            <span class="rank__bar">
              <i :style="{ width: (g.events / maxGuild * 100) + '%' }"></i>
            </span>
            <span class="rank__n">{{ g.events }}</span>
          </div>
        </div>
      </div>

      <!-- 熱力圖 -->
      <div class="report__section">
        <Heatmap :days="report?.heatmap || []" />
      </div>

      <!-- 櫃台小姐成長 -->
      <div class="report__section">
        <b>🧝‍♀️ 櫃台小姐 <span class="dim">Lv.{{ recLevel }}</span></b>
        <div class="recept">
          <div class="dim recept__hint">
            處理越多回報,解鎖越多審查技能。
            <span v-if="recNextAt"> 下一個技能需累積 {{ recNextAt }} 份回報。</span>
            <span v-else> 已精通所有審查技能!</span>
          </div>
          <div class="rev-grid">
            <div
              v-for="s in reviewSkills"
              :key="s.key"
              class="rev"
              :class="{ 'rev--lock': !s.unlocked }"
            >
              <span class="rev__i">{{ s.unlocked ? '🔓' : '🔒' }}</span>
              <div>
                <div class="rev__name">{{ s.name }}</div>
                <div class="rev__desc">{{ s.desc }}</div>
                <div class="rev__at" v-if="!s.unlocked">需 {{ s.at }} 份回報</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 櫃台回報紀錄(可展開看內容) -->
      <div class="report__section">
        <b>🗂 櫃台回報紀錄
          <span class="dim" v-if="queue">
            (待處理 {{ (queue.pending || 0) + (queue.processing || 0) }} · 已歸檔 {{ queue.done || 0 }})
          </span>
        </b>
        <div class="reports">
          <div v-for="r in reports" :key="r.id" class="rep" :class="`rep--${r.status}`">
            <div class="rep__head" @click="toggle(r.id)">
              <span class="rep__oc">{{ OUTCOME[r.outcome] || '✅' }}</span>
              <div class="rep__main">
                <div class="rep__title">{{ r.title }}</div>
                <div class="rep__meta">
                  🏰 {{ r.guild || '—' }} · {{ STATUS_TXT[r.status] || r.status }}
                </div>
              </div>
              <span v-if="REVIEW_BADGE[r.review]" class="rep__revbadge" :class="REVIEW_BADGE[r.review].c">
                {{ REVIEW_BADGE[r.review].t }}
              </span>
              <span class="rep__caret">{{ openId === r.id ? '▲' : '▼' }}</span>
            </div>
            <!-- 展開:完整內容 + 櫃台審查 + 幕後審核 -->
            <div v-if="openId === r.id" class="rep__body">
              <div v-if="r.summary" class="rep__field"><b>內容</b>{{ r.summary }}</div>
              <div v-if="r.files_changed?.length" class="rep__field">
                <b>改動檔案</b>{{ r.files_changed.join('、') }}
              </div>
              <div v-if="r.lessons" class="rep__field"><b>心得</b>{{ r.lessons }}</div>
              <div v-if="r.result" class="rep__review">🧝‍♀️ 櫃台審查:{{ r.result }}</div>
              <div v-if="r.review_note" class="rep__review rep__review--no">
                ↩️ 你的退回原因:{{ r.review_note }}
              </div>

              <!-- 幕後審核:認可 / 退回櫃台重寫 / 派冒險者接續 -->
              <div class="rep__judge">
                <div class="rep__judge-h">🎖 幕後審核</div>
                <div class="rep__judge-btns">
                  <button class="jbtn jbtn--ok" :disabled="busy === r.id" @click="approve(r)">
                    {{ r.review === 'approved' ? '✅ 已認可' : '✅ 認可' }}
                  </button>
                  <button class="jbtn jbtn--redo" :disabled="busy === r.id" @click="reject(r)">
                    ✍️ 退回櫃台重寫
                  </button>
                  <button class="jbtn jbtn--fix" :disabled="!r.adventurer_id" @click="cont(r)"
                          :title="r.adventurer_id ? '' : '這份報告沒有對應的 session,無法召回'">
                    🔧 派冒險者接續
                  </button>
                </div>
                <div class="dim rep__judge-hint">
                  報告寫不清楚 → 退回櫃台重寫;實際工作沒做好 → 派冒險者接續(召回原 session 改)
                </div>
              </div>
            </div>
          </div>
          <div v-if="reports.length === 0" class="dim" style="padding: 8px">
            還沒有任何回報。冒險者完成任務後,在其細節面板點「回報此任務」即可送交櫃台。
          </div>
        </div>
      </div>

      <!-- 成就 -->
      <div class="report__section">
        <b>🏆 成就徽章 <span class="dim">({{ unlocked }}/{{ achievements.length }})</span></b>
        <div class="ach-grid">
          <div
            v-for="a in achievements"
            :key="a.id"
            class="ach"
            :class="{ 'ach--lock': !a.unlocked }"
          >
            <span class="ach__icon">{{ a.icon }}</span>
            <div class="ach__body">
              <div class="ach__name">{{ a.name }}</div>
              <div class="ach__desc">{{ a.desc }}</div>
              <div v-if="!a.unlocked && a.progress" class="ach__prog">{{ a.progress }}</div>
              <div v-else class="ach__done">✔ 已達成</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
