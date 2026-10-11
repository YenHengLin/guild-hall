<script setup>
// ⚙️ 工程師職業的結果皮膚:把「改了什麼」渲染成 IDE 感的檔案 + 紅綠 diff,
// 而不是一串條列文字。資料來自後端客觀收集的 Edit/Write(before→after)+ LLM 的 why。
import { ref, computed } from 'vue'
import { problemKind } from '../labels'

const props = defineProps({
  // { headline, category, solved, files:[{path,why,hunks:[{before,after}],adds,dels}], adds, dels, notes }
  // category/solved 為後端新增欄位(解決了哪類問題);adds/dels 舊卡沒有時前端會即時由 hunks 加總,皆向後相容。
  card: { type: Object, required: true },
})

// 解決的問題分類徽章(認不得 / 舊卡無 category → 不顯示,向後相容)
const kind = computed(() => problemKind(props.card.category))

// VS Code 式:左側檔案清單點選,右側看那個檔的 diff。預設選第一個檔。
const selected = ref(0)
function select(i) { selected.value = i }
const current = computed(() => (props.card.files || [])[selected.value] || null)
// 超長 hunk 預設收合;記錄哪些 (檔index-hunk index) 被使用者展開
const expanded = ref(new Set())
function expandKey(fi, hi) { return fi + '-' + hi }
function toggleHunk(fi, hi) {
  const k = expandKey(fi, hi)
  const s = new Set(expanded.value)
  s.has(k) ? s.delete(k) : s.add(k)
  expanded.value = s
}

function baseName(p) {
  return (p || '').split('/').pop()
}
function dirName(p) {
  const parts = (p || '').split('/')
  parts.pop()
  return parts.join('/')
}
// 把 before/after 切成行,給 diff 呈現;空字串(新檔)不產生 - 行
function lines(text) {
  if (!text) return []
  return text.replace(/\n$/, '').split('\n')
}
function countLines(text) {
  if (!text) return 0
  return text.replace(/\n$/, '').split('\n').length
}

// 依副檔名給檔案類型小圖示(找不到就用通用 📄)
const EXT_ICON = {
  vue: '🟢', js: '🟨', ts: '🔷', jsx: '🟨', tsx: '🔷',
  py: '🐍', json: '🗂️', css: '🎨', scss: '🎨', html: '🌐',
  md: '📝', txt: '📄', yml: '⚙️', yaml: '⚙️', toml: '⚙️',
  sh: '🐚', bat: '🖥️', sql: '🗃️', go: '🐹', rs: '🦀',
  java: '☕', c: '🇨', cpp: '🇨', h: '🇨', vue3: '🟢',
}
function fileIcon(p) {
  const name = baseName(p)
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
  return EXT_ICON[ext] || '📄'
}

// 每檔的 +N/-M:優先用後端給的 adds/dels,沒有就即時由 hunks 加總(向後相容)
function fileStat(f) {
  if (typeof f.adds === 'number' && typeof f.dels === 'number') {
    return { adds: f.adds, dels: f.dels }
  }
  let adds = 0, dels = 0
  for (const h of f.hunks || []) {
    dels += countLines(h.before)
    adds += countLines(h.after)
  }
  return { adds, dels }
}

// 卡片頂部摘要:改了幾個檔 + 總 +/-(後端沒給總數就由各檔加總)
const summary = computed(() => {
  const files = props.card.files || []
  let adds = 0, dels = 0
  for (const f of files) {
    const s = fileStat(f)
    adds += s.adds
    dels += s.dels
  }
  if (typeof props.card.adds === 'number') adds = props.card.adds
  if (typeof props.card.dels === 'number') dels = props.card.dels
  return { count: files.length, adds, dels }
})

// 一個 hunk 的總行數,超過門檻才需要折疊
const COLLAPSE_THRESHOLD = 12
function hunkLen(h) {
  return countLines(h.before) + countLines(h.after)
}
function isLong(h) {
  return hunkLen(h) > COLLAPSE_THRESHOLD
}
</script>

<template>
  <div class="ccard">
    <div class="ccard__headline">
      <span class="ccard__gear">⚙️</span>
      <span v-if="kind" class="ccard__kind"
            :style="{ '--kc': kind.color }" :title="'問題類型:' + kind.label">
        {{ kind.icon }} {{ kind.label }}
      </span>
      <b>{{ card.headline || '完成一項 coding 委託' }}</b>
      <span class="ccard__summary" v-if="summary.count">
        改了 {{ summary.count }} 個檔
        <span class="ccard__stat-add" v-if="summary.adds">+{{ summary.adds }}</span>
        <span class="ccard__stat-del" v-if="summary.dels">-{{ summary.dels }}</span>
      </span>
    </div>
    <!-- 到底解決了哪個問題:模組化、一句話講清楚,取代「一串 diff 看不出重點」 -->
    <div v-if="card.solved" class="ccard__solved">
      <span class="ccard__solved-l">解決</span>{{ card.solved }}
    </div>

    <!-- VS Code 式:左欄點選檔案,右欄看該檔 diff(窄螢幕時自動上下堆疊) -->
    <div v-if="card.files?.length" class="ccard__explorer">
      <!-- 左:檔案清單 -->
      <div class="ccard__list">
        <div class="ccard__list-h">📁 變更檔案 ({{ summary.count }})</div>
        <button v-for="(f, i) in card.files" :key="f.path + i"
                class="ccard__item" :class="{ 'is-on': selected === i }"
                @click="select(i)" :title="f.path">
          <span class="ccard__item-name">{{ fileIcon(f.path) }} {{ baseName(f.path) }}</span>
          <span class="ccard__item-stat">
            <span class="ccard__stat-add" v-if="fileStat(f).adds">+{{ fileStat(f).adds }}</span>
            <span class="ccard__stat-del" v-if="fileStat(f).dels">-{{ fileStat(f).dels }}</span>
          </span>
          <span class="ccard__item-dir" v-if="dirName(f.path)">{{ dirName(f.path) }}</span>
        </button>
      </div>

      <!-- 右:選中檔的 diff -->
      <div class="ccard__pane" v-if="current">
        <div class="ccard__pane-head">
          <span class="ccard__fname">{{ fileIcon(current.path) }} {{ current.path }}</span>
          <span class="ccard__fstat">
            <span class="ccard__stat-add" v-if="fileStat(current).adds">+{{ fileStat(current).adds }}</span>
            <span class="ccard__stat-del" v-if="fileStat(current).dels">-{{ fileStat(current).dels }}</span>
          </span>
        </div>
        <div v-if="current.why" class="ccard__why">💡 {{ current.why }}</div>
        <div class="ccard__diff">
          <div v-for="(h, hi) in current.hunks" :key="hi" class="ccard__hunk">
            <template v-if="!isLong(h) || expanded.has(expandKey(selected, hi))">
              <div v-for="(ln, li) in lines(h.before)" :key="'b' + li" class="ccard__line ccard__line--del">
                <span class="ccard__sign">-</span><code>{{ ln }}</code>
              </div>
              <div v-for="(ln, li) in lines(h.after)" :key="'a' + li" class="ccard__line ccard__line--add">
                <span class="ccard__sign">+</span><code>{{ ln }}</code>
              </div>
            </template>
            <!-- 超長 hunk 收合:只顯示前幾行 + 展開按鈕 -->
            <template v-else>
              <div v-for="(ln, li) in lines(h.before).slice(0, 3)" :key="'bp' + li" class="ccard__line ccard__line--del">
                <span class="ccard__sign">-</span><code>{{ ln }}</code>
              </div>
              <div v-for="(ln, li) in lines(h.after).slice(0, 3)" :key="'ap' + li" class="ccard__line ccard__line--add">
                <span class="ccard__sign">+</span><code>{{ ln }}</code>
              </div>
              <button class="ccard__more" @click="toggleHunk(selected, hi)">
                ⋯ 展開更多({{ hunkLen(h) }} 行)
              </button>
            </template>
            <button
              v-if="isLong(h) && expanded.has(expandKey(selected, hi))"
              class="ccard__more"
              @click="toggleHunk(selected, hi)"
            >▴ 收合</button>
          </div>
          <div v-if="!current.hunks?.length" class="ccard__nodiff">(此檔無可顯示的 diff)</div>
        </div>
      </div>
    </div>
    <div v-else class="ccard__nodiff ccard__nodiff--light">這次沒有明確的檔案改動。</div>

    <div v-if="card.notes" class="ccard__notes">📌 {{ card.notes }}</div>
  </div>
</template>
