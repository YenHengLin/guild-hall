<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useGuildStore } from '../stores/guild'
import { relativeTime } from '../labels'
import Markdown from './Markdown.vue'
import ModelPicker from './ModelPicker.vue'

const emit = defineEmits(['close'])
const store = useGuildStore()

const KIND_META = {
  improve: { icon: '✨', label: '改善' }, bug: { icon: '🐞', label: '疑似 bug' },
  refactor: { icon: '🧹', label: '重構' }, idea: { icon: '💡', label: '點子' },
  test: { icon: '🧪', label: '測試' }, doc: { icon: '📖', label: '文件' },
  perf: { icon: '⚡', label: '效能' }, ux: { icon: '🎨', label: 'UX' },
}
const EFFORT = { S: '小', M: '中', L: '大' }
// 影響/價值提示:高中低對應色調(沿用公會色系)
const IMPACT_META = {
  高: { label: '影響大', cls: 'idea__impact--hi' },
  中: { label: '影響中', cls: 'idea__impact--mid' },
  低: { label: '影響小', cls: 'idea__impact--lo' },
}
const newIdeas = computed(() => store.museIdeas.filter((i) => i.status === 'new'))
const handled = computed(() => store.museIdeas.filter((i) => i.status !== 'new'))

// 每座公會「走到哪」:把所有靈感(含已處理)依公會統計 已採納/略過/待處理,
// 讓分支像技能樹節點一樣顯示進度——這座公會你已經推進了多少、還剩多少沒看。
const guildProgress = computed(() => {
  const by = {}
  for (const i of store.museIdeas) {
    const g = i.guild || '未知公會'
    const p = (by[g] ||= { promoted: 0, dismissed: 0, new: 0, total: 0 })
    if (i.status === 'promoted') p.promoted++
    else if (i.status === 'dismissed') p.dismissed++
    else p.new++
    p.total++
  }
  for (const g in by) {
    const p = by[g]
    // 進度 = 已處理(採納+略過)占比;採納才是「真的往前走」,略過只是清掉
    p.done = p.promoted + p.dismissed
    p.pct = p.total ? Math.round((p.done / p.total) * 100) : 0
  }
  return by
})

// 把一整排扁平靈感依「公會(= 對誰優化)」收攏成分支,讓長清單變成一眼看得懂的樹:
// 每個分支標題直接標出「這座公會幾張 · 幾高幾中幾低」,主人掃一眼就知道主體與輕重。
const IMPACT_ORDER = { 高: 0, 中: 1, 低: 2 }
const newGroups = computed(() => {
  const by = new Map()
  for (const i of newIdeas.value) {
    const key = i.guild || '未知公會'
    if (!by.has(key)) by.set(key, [])
    by.get(key).push(i)
  }
  const groups = []
  for (const [guild, ideas] of by) {
    const impact = { 高: 0, 中: 0, 低: 0 }
    for (const i of ideas) if (impact[i.impact] != null) impact[i.impact]++
    // 分支內的卡片也依影響高→低排,最值得做的先看到
    ideas.sort((a, b) => (IMPACT_ORDER[a.impact] ?? 3) - (IMPACT_ORDER[b.impact] ?? 3))
    groups.push({
      guild, ideas, impact, kinds: groupByKind(ideas),
      score: impact.高 * 100 + impact.中 * 10 + impact.低,
    })
  }
  // 影響大的、張數多的公會排最前
  groups.sort((a, b) => b.score - a.score || b.ideas.length - a.ideas.length)
  return groups
})

// 分支內的第二層:依 kind(改善/bug/效能/UX…)分類成小節,這就是「分類」本身。
// 每個小節標出「這類幾張」,讓主人一眼看出這座公會的優化落在哪些領域、各領域輕重。
const KIND_ORDER = ['bug', 'perf', 'ux', 'improve', 'refactor', 'test', 'doc', 'idea']
function groupByKind(ideas) {
  const by = new Map()
  for (const i of ideas) {
    const k = KIND_META[i.kind] ? i.kind : 'idea'
    if (!by.has(k)) by.set(k, [])
    by.get(k).push(i)
  }
  return [...by.entries()]
    .map(([kind, items]) => ({ kind, meta: KIND_META[kind], items }))
    .sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind))
}
const meta = computed(() => store.museConfig || {})
const prog = computed(() => store.museStatus || {})

// 本地表單:從 store 設定複製一份,編輯後按「儲存」才送出
const form = reactive({
  enabled: false, daily_budget_usd: 1, hour_start: 1, hour_end: 6,
  interval_min: 45, ideas_per_run: 3, model: 'claude-sonnet-4-6', include_guilds: [],
  auto_execute: false, auto_per_run: 1, auto_max_effort: 'S',
})
watch(() => store.museConfig, (m) => {
  if (m?.config) Object.assign(form, m.config)
}, { immediate: true })

const saving = ref(false)
const saved = ref(false)
async function save() {
  saving.value = true; saved.value = false
  const err = await store.saveMuseConfig({ ...form })
  saving.value = false
  if (!err) { saved.value = true; setTimeout(() => (saved.value = false), 1500) }
}

const runMsg = ref(null)
async function runNow() {
  runMsg.value = null
  const res = await store.runMuseNow()
  if (res) {
    runMsg.value = res.skipped
      ? `這輪略過:${res.reason}`
      : `巡了「${res.guild}」→ 新增 ${res.added} 張靈感卡`
      + (res.queued?.length ? `,自動接下 ${res.queued.length} 張` : '')
      + (res.cost ? `(花 $${Number(res.cost).toFixed(3)})` : '')
  }
}
const autoRunning = ref(false)
async function autoNow() {
  runMsg.value = null
  autoRunning.value = true
  try {
    const res = await store.autoRunNow()
    if (res) {
      runMsg.value = res.queued?.length
        ? `已從清單挑 ${res.queued.length} 張排入派遣佇列,開始自動執行(見布告欄)`
        : `沒有可自動執行的靈感:${res.reason || '需安全且工作量在上限內'}`
    }
  } finally {
    autoRunning.value = false
  }
}

async function promote(i) {
  if (!i.cwd) {
    if (!confirm('這張靈感沒有記錄工作資料夾,採納後需自己在布告欄指定公會再派遣。繼續?')) return
  }
  const c = await store.promoteIdea(i.id)
  if (c) runMsg.value = `已採納為布告欄委託「${c.title}」,到布告欄即可派遣。`
}
</script>

<template>
  <div class="report-backdrop" @click.self="emit('close')">
    <div class="training muse">
      <div class="training__head">
        <b>🌙 公會夜巡</b>
        <span class="training__sub">NPC 冒險者在預算/時段內自主巡各專案,提出改善靈感 → 你一鍵採納成委託</span>
        <button class="btn-ghost" @click="emit('close')">✕</button>
      </div>

      <!-- 設定 -->
      <div class="training__sec">
        <div class="training__sec-t">⚙️ 夜巡設定</div>
        <div class="muse__cfg">
          <label class="muse__toggle">
            <input type="checkbox" v-model="form.enabled" />
            <span>啟用自動夜巡</span>
          </label>
          <label>每日預算 $
            <input type="number" min="0" step="0.5" v-model.number="form.daily_budget_usd" />
          </label>
          <label>時段
            <input type="number" min="0" max="23" v-model.number="form.hour_start" />–
            <input type="number" min="0" max="23" v-model.number="form.hour_end" /> 點
          </label>
          <label>間隔
            <input type="number" min="5" step="5" v-model.number="form.interval_min" /> 分
          </label>
          <label>每輪張數
            <input type="number" min="1" max="6" v-model.number="form.ideas_per_run" />
          </label>
          <label>位階
            <ModelPicker v-model="form.model" title="夜巡用哪個模型" />
          </label>
          <button class="btn-primary" :disabled="saving" @click="save">
            {{ saving ? '儲存中…' : (saved ? '✓ 已儲存' : '💾 儲存設定') }}
          </button>
        </div>
        <!-- 自主執行 -->
        <div class="muse__auto">
          <label class="muse__toggle">
            <input type="checkbox" v-model="form.auto_execute" />
            <span>🤖 自主執行(自己挑安全的靈感做掉,免人工同意)</span>
          </label>
          <label>每輪最多接
            <input type="number" min="0" max="3" v-model.number="form.auto_per_run" /> 張
          </label>
          <label>最高工作量
            <select v-model="form.auto_max_effort">
              <option value="S">S(最保守)</option>
              <option value="M">M(含中型)</option>
            </select>
          </label>
        </div>
        <div v-if="form.auto_execute" class="muse__warn">
          ⚠️ 開啟後,夜巡會把模型判定為「安全、自足、低風險」且工作量在上限內的靈感,
          直接排入派遣佇列**自動改檔並花 token**(仍走櫃台驗收)。涉及設計取捨/改 API/
          需判斷的 bug 一律留給你人工挑。
        </div>
        <div class="muse__status">
          <span :class="meta.available === false ? 'muse__bad' : ''">
            {{ meta.available === false ? '⚠️ 找不到 claude CLI' : '✅ claude 可用' }}
          </span>
          <span v-if="meta.spent_today != null">今日已花 <b>${{ meta.spent_today }}</b></span>
          <span v-if="meta.budget_left != null">
            剩餘預算 <b :class="meta.budget_left <= 0 ? 'muse__bad' : ''">${{ meta.budget_left }}</b>
          </span>
          <span class="dim">留空公會設定 = 巡所有有活動的專案</span>
        </div>
        <div class="muse__toolbar">
          <button class="btn-primary" :disabled="store.musing || meta.available === false"
                  @click="runNow" title="不管時段/開關,立刻手動巡一輪(仍守預算與唯讀)">
            {{ store.musing ? '⏳ 夜巡中(跑 claude,可能要一兩分鐘)…' : '🔦 立即巡一輪' }}
          </button>
          <button class="btn-ghost" :disabled="autoRunning || meta.available === false"
                  @click="autoNow"
                  title="不巡邏,直接從下面的靈感清單挑一張安全的排進派遣佇列做掉">
            {{ autoRunning ? '⏳ 安排中…' : '🤖 立即自動做一張' }}
          </button>
          <span v-if="runMsg" class="muse__runmsg">{{ runMsg }}</span>
          <span v-if="store.museError" class="dispatch__err">⚠️ {{ store.museError }}</span>
        </div>
      </div>

      <!-- 即時進度(巡邏中才顯示)-->
      <div v-if="prog.running" class="training__sec muse__live">
        <div class="training__sec-t">
          <span class="muse__live-dot"></span>
          夜巡中 · 🏰 {{ prog.guild }}
        </div>
        <div class="muse__live-now">{{ prog.step }}</div>
        <div v-if="prog.steps && prog.steps.length" class="muse__live-log">
          <div v-for="(s, idx) in prog.steps.slice(-8)" :key="idx" class="muse__live-line">
            {{ s }}
          </div>
        </div>
      </div>
      <div v-else-if="prog.step && !store.musing" class="muse__lastrun">
        上次巡邏:{{ prog.step }}
      </div>

      <!-- 待處理靈感 -->
      <div class="training__sec">
        <div class="training__sec-t">💡 新靈感 ({{ newIdeas.length }})</div>
        <div v-if="!newIdeas.length" class="board__empty">
          還沒有新靈感。開啟自動夜巡並等到設定的時段,或按「🔦 立即巡一輪」馬上試。
        </div>
        <!-- 技能樹分支:一座公會 = 一條分支。標題列一眼看出對誰優化、有幾張、輕重分布 -->
        <details v-for="g in newGroups" :key="g.guild" class="branch" open>
          <summary class="branch__head">
            <span class="branch__name">🏰 {{ g.guild }}</span>
            <span class="branch__count">{{ g.ideas.length }} 張待處理</span>
            <span class="branch__impacts">
              <span v-if="g.impact.高" class="idea__impact idea__impact--hi">{{ g.impact.高 }} 影響大</span>
              <span v-if="g.impact.中" class="idea__impact idea__impact--mid">{{ g.impact.中 }} 影響中</span>
              <span v-if="g.impact.低" class="idea__impact idea__impact--lo">{{ g.impact.低 }} 影響小</span>
            </span>
          </summary>
          <!-- 「走到哪」進度軸:這座公會已採納/略過/待處理,像技能樹節點的點亮進度 -->
          <div v-if="guildProgress[g.guild]" class="branch__prog">
            <div class="branch__bar" :title="`已採納 ${guildProgress[g.guild].promoted}、略過 ${guildProgress[g.guild].dismissed}、待處理 ${guildProgress[g.guild].new}`">
              <span class="branch__bar-fill" :style="{ width: guildProgress[g.guild].pct + '%' }"></span>
            </div>
            <span class="branch__prog-text">
              走到 {{ guildProgress[g.guild].pct }}%
              · 🎫 採納 {{ guildProgress[g.guild].promoted }}
              · 🙈 略過 {{ guildProgress[g.guild].dismissed }}
              · 待辦 {{ guildProgress[g.guild].new }}
            </span>
          </div>
          <!-- 第二層:分類小節(改善/bug/效能/UX…)= 「這座公會的優化落在哪些領域」 -->
          <div v-for="sec in g.kinds" :key="sec.kind" class="twig">
            <div class="twig__head">
              <span class="twig__kind">{{ sec.meta.icon }} {{ sec.meta.label }}</span>
              <span class="twig__count">{{ sec.items.length }}</span>
            </div>
          <div v-for="i in sec.items" :key="i.id" class="idea">
            <div class="idea__head">
              <span v-if="i.impact && IMPACT_META[i.impact]"
                    class="idea__impact" :class="IMPACT_META[i.impact].cls">
                {{ IMPACT_META[i.impact].label }}
              </span>
              <b class="idea__title">{{ i.title }}</b>
            </div>
            <!-- 白話一句話:醒目呈現,主人一眼掃懂發現了什麼 -->
            <div v-if="i.headline" class="idea__headline">
              <span class="idea__headline-mark">👀 發現</span>{{ i.headline }}
            </div>
            <!-- 建議的下一步:白話、可直接開工 -->
            <div v-if="i.next_step" class="idea__next">
              <span class="idea__next-mark">➡️ 下一步</span>{{ i.next_step }}
            </div>
            <div class="idea__meta">
              <template v-if="i.effort">工作量 {{ EFFORT[i.effort] || i.effort }}</template>
              <template v-if="i.created_at"> · {{ relativeTime(i.created_at) }}</template>
            </div>
            <details v-if="i.body" class="idea__more">
              <summary class="idea__more-t">📋 技術細節</summary>
              <Markdown :text="i.body" class="idea__body" />
            </details>
            <div class="idea__actions">
              <button class="btn-primary" @click="promote(i)">🎫 採納成委託</button>
              <button class="btn-ghost" @click="store.dismissIdea(i.id)">🙈 略過</button>
              <button class="btn-ghost" @click="store.removeIdea(i.id)">🗑 刪除</button>
            </div>
          </div>
          </div>
        </details>
      </div>

      <!-- 已處理 -->
      <div v-if="handled.length" class="training__sec">
        <div class="training__sec-t">🗂 已處理</div>
        <div v-for="i in handled.slice(0, 12)" :key="'h' + i.id" class="idea idea--done">
          <span>{{ i.status === 'promoted' ? '🎫' : '🙈' }}</span>
          <span class="idea__hist-title">{{ i.title }}</span>
          <span class="idea__meta">{{ i.guild || '?' }}</span>
          <button class="btn-ghost" @click="store.removeIdea(i.id)">🗑</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 技能樹分支:一座公會收攏成一條可收合的枝幹,左側一道金線像樹幹 */
.branch {
  margin: 0.5rem 0;
  padding-left: 0.75rem;
  border-left: 3px solid var(--border, #c9a969);
}
.branch[open] { border-left-color: var(--gold, #b8860b); }
.branch__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  cursor: pointer;
  padding: 0.3rem 0;
  user-select: none;
  list-style: none;
}
.branch__head::-webkit-details-marker { display: none; }
.branch__head::before {
  content: '▸';
  color: var(--gold, #b8860b);
  transition: transform 0.15s;
}
.branch[open] .branch__head::before { transform: rotate(90deg); }
.branch__name { font-weight: 700; font-size: 1.02rem; color: var(--ink, #3a2f1d); }
.branch__count {
  font-size: 0.8rem;
  color: var(--ink-soft, #6b5c42);
  background: rgba(107, 92, 66, 0.1);
  padding: 0.05rem 0.45rem;
  border-radius: 999px;
}
.branch__impacts { display: inline-flex; gap: 0.3rem; margin-left: auto; }
/* 「走到哪」進度軸:細進度條 + 文字,呈現這座公會已推進多少 */
.branch__prog { margin: 0.1rem 0 0.4rem; }
.branch__bar {
  height: 5px;
  border-radius: 999px;
  background: rgba(107, 92, 66, 0.15);
  overflow: hidden;
}
.branch__bar-fill {
  display: block;
  height: 100%;
  background: var(--gold, #b8860b);
  border-radius: 999px;
  transition: width 0.3s;
}
.branch__prog-text {
  display: inline-block;
  margin-top: 0.2rem;
  font-size: 0.74rem;
  color: var(--ink-soft, #6b5c42);
}
/* 第二層小枝:一個分類(kind)= 一個小節,標題標出領域與張數 */
.twig { margin: 0.35rem 0 0.35rem 0.4rem; }
.twig__head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.2rem;
}
.twig__kind { font-weight: 700; font-size: 0.9rem; color: var(--ink, #3a2f1d); }
.twig__count {
  font-size: 0.72rem;
  color: var(--ink-soft, #6b5c42);
  background: rgba(107, 92, 66, 0.1);
  padding: 0 0.4rem;
  border-radius: 999px;
}
/* 白話一句話:羊皮紙上最醒目的一行,主人一眼掃懂「發現了什麼」 */
.idea__headline {
  margin: 0.4rem 0 0.15rem;
  padding: 0.5rem 0.65rem;
  font-size: 1.02rem;
  font-weight: 600;
  line-height: 1.5;
  color: var(--ink, #3a2f1d);
  background: rgba(184, 134, 11, 0.1);
  border-left: 4px solid var(--gold, #b8860b);
  border-radius: 4px;
}
.idea__headline-mark {
  display: inline-block;
  margin-right: 0.4rem;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--gold, #b8860b);
  letter-spacing: 0.03em;
}
/* 建議下一步:白話、可直接開工 */
.idea__next {
  margin: 0.2rem 0;
  padding-left: 0.2rem;
  font-size: 0.92rem;
  line-height: 1.5;
  color: var(--ink-soft, #6b5c42);
}
.idea__next-mark {
  display: inline-block;
  margin-right: 0.4rem;
  font-weight: 700;
  color: var(--ink, #3a2f1d);
}
/* 影響/價值標籤 */
.idea__impact {
  display: inline-block;
  margin-left: 0.35rem;
  padding: 0.05rem 0.45rem;
  font-size: 0.74rem;
  font-weight: 700;
  border-radius: 999px;
  border: 1px solid var(--border, #c9a969);
  white-space: nowrap;
}
.idea__impact--hi { color: #8a2f0b; background: rgba(180, 60, 20, 0.14); border-color: #c06a3f; }
.idea__impact--mid { color: #7a5a0b; background: rgba(184, 134, 11, 0.14); }
.idea__impact--lo { color: var(--ink-soft, #6b5c42); background: rgba(107, 92, 66, 0.1); }
/* 技術細節收合:預設收起,想深入才展開 */
.idea__more { margin: 0.35rem 0 0.1rem; }
.idea__more-t {
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--ink-soft, #6b5c42);
  user-select: none;
}
.idea__more-t:hover { color: var(--gold, #b8860b); }
</style>
