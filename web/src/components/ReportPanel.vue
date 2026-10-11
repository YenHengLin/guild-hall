<script setup>
import { computed, ref } from 'vue'
import Heatmap from './Heatmap.vue'
import CommissionReport from './CommissionReport.vue'
import { useGuildStore } from '../stores/guild'
import { shortTokens } from '../labels'
import { useFloatingPanel } from '../composables/useFloatingPanel'

const props = defineProps({ report: Object, queue: Object })
const emit = defineEmits(['close'])
const store = useGuildStore()

// 可拖曳(抓標題列)+ 可縮放(CSS resize)的浮動視窗
const { floating, onDragStart, panelStyle } = useFloatingPanel()

const reports = computed(() => props.queue?.recent || [])
const OUTCOME = { success: '✅', partial: '🟡', failed: '❌' }
const STATUS_TXT = {
  pending: '⏳ 待處理', processing: '⚙️ 處理中', done: '🗂 已歸檔', failed: '⚠️ 失敗',
}
const REVIEW_BADGE = {
  rejected: { t: '↩️ 已退回', c: 'rev-no' },
}

// 展開看回報完整內容
const openId = ref(null)
function toggle(id) { openId.value = openId.value === id ? null : id }

// 櫃台小姐成長
const recLevel = computed(() => props.queue?.reception_level || 1)
const recNextAt = computed(() => props.queue?.reception_next_at)
const reviewSkills = computed(() => props.queue?.review_skills || [])
// 學習迴路:退回報告 → 櫃台學敘事偏好;叫冒險者繼續 → 冒險者學做事方式
const receptionPrefs = computed(() => props.queue?.reception_prefs || [])
const workPrefs = computed(() => props.queue?.work_prefs || [])

const today = computed(() => props.report?.today || {})
const week = computed(() => props.report?.week || {})
const achievements = computed(() => props.report?.achievements || [])
const unlocked = computed(() => achievements.value.filter((a) => a.unlocked).length)
const topGuilds = computed(() => props.report?.top_guilds_today || [])
const maxGuild = computed(() =>
  Math.max(1, ...topGuilds.value.map((g) => g.events)),
)

// ── 成本帳本 ──
const SRC_META = {
  dispatch: { icon: '🚀', label: '派遣' },
  recall: { icon: '🔮', label: '召回' },
  training: { icon: '🎓', label: '訓練' },
  synthesis: { icon: '🧬', label: '合成' },
  polish: { icon: '✨', label: '潤稿' },
  classify: { icon: '📚', label: '整理' },
}
const costs = computed(() => store.costs)
const costTotal = computed(() => store.costs?.total || 0)
function fmtUsd(v) {
  const n = Number(v) || 0
  return '$' + (n < 0.01 && n > 0 ? n.toFixed(4) : n.toFixed(2))
}
</script>

<template>
  <div class="report-backdrop" :class="{ 'report-backdrop--free': floating }" @click.self="emit('close')">
    <div class="report fpanel" :class="{ 'fpanel--free': floating }" :style="panelStyle()">
      <div class="report__bar" @pointerdown="onDragStart($event, '.report')"
           title="拖曳可移動;抓右下角可縮放視窗">
        <h2>📊 公會戰報</h2>
        <span class="report__drag-hint">⠿ 可拖曳 · 右下角可縮放</span>
        <button class="report__close" @click="emit('close')">✕</button>
      </div>

      <div class="report__scroll">
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

      <!-- 成本帳本(每次 headless 花費記帳 → 月報表) -->
      <div class="report__section">
        <b>💰 成本帳本 <span class="dim">累計 {{ fmtUsd(costTotal) }}</span></b>
        <div v-if="costs && costs.months.length" class="ledger">
          <div v-for="m in costs.months" :key="m.month" class="ledger__month">
            <div class="ledger__head">
              <span class="ledger__mon">{{ m.month }}</span>
              <span class="ledger__mtot">{{ fmtUsd(m.total) }}</span>
            </div>
            <div class="ledger__srcs">
              <span v-for="(v, src) in m.sources" :key="src" class="ledger__src">
                {{ (SRC_META[src] || {}).icon || '•' }}
                {{ (SRC_META[src] || {}).label || src }}
                {{ fmtUsd(v.cost) }} <i class="dim">×{{ v.n }}</i>
              </span>
            </div>
          </div>
        </div>
        <div v-else class="dim" style="padding: 8px">
          還沒有任何花費紀錄。派遣 / 召回 / 訓練冒險者會在此記帳。
        </div>
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

      <!-- 學習迴路:退回 → 櫃台學敘事;繼續 → 冒險者學做事方式 -->
      <div v-if="receptionPrefs.length || workPrefs.length" class="report__section">
        <b>🔁 學習迴路 <span class="dim">從你的退回 / 繼續指示累積</span></b>
        <div class="loop">
          <div class="loop__col">
            <div class="loop__h">🧝‍♀️ 櫃台學到的敘事偏好</div>
            <div class="dim loop__hint">你「退回櫃台重寫」時的說明 → 之後報告會照這樣寫</div>
            <ul v-if="receptionPrefs.length" class="loop__list">
              <li v-for="(p, i) in receptionPrefs" :key="'rp' + i">{{ p }}</li>
            </ul>
            <div v-else class="dim loop__empty">還沒有。退回報告時寫下哪裡不清楚即可累積。</div>
          </div>
          <div class="loop__col">
            <div class="loop__h">🧭 冒險者學到的做事方式</div>
            <div class="dim loop__hint">你叫冒險者「繼續做」時的指示 → 之後派遣會照這些習慣</div>
            <ul v-if="workPrefs.length" class="loop__list">
              <li v-for="(p, i) in workPrefs" :key="'wp' + i">{{ p }}</li>
            </ul>
            <div v-else class="dim loop__empty">還沒有。對執行中的委託下中途指示即可累積。</div>
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
                  <span v-if="store.personaBySession[r.adventurer_id]" class="rep__doer">
                    {{ store.personaBySession[r.adventurer_id].icon }}
                    {{ store.personaBySession[r.adventurer_id].klass }} ·
                  </span>
                  🏰 {{ r.guild || '—' }} · {{ STATUS_TXT[r.status] || r.status }}
                </div>
              </div>
              <span v-if="REVIEW_BADGE[r.review]" class="rep__revbadge" :class="REVIEW_BADGE[r.review].c">
                {{ REVIEW_BADGE[r.review].t }}
              </span>
              <span class="rep__caret">{{ openId === r.id ? '▲' : '▼' }}</span>
            </div>
            <!-- 展開:完整內容 + 櫃台審查 + 幕後審核(與布告欄共用同一元件) -->
            <div v-if="openId === r.id" class="rep__body">
              <CommissionReport :report="r" judge />
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
      </div><!-- /.report__scroll -->
    </div>
  </div>
</template>
