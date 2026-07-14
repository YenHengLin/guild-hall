<script setup>
import { computed } from 'vue'
import { relativeTime } from '../labels'

const props = defineProps({ activity: { type: Array, default: () => [] } })

const TOOL_ICON = {
  Edit: '🖋️', Write: '🖋️', NotebookEdit: '🖋️',
  Read: '🔍', Grep: '🔍', Glob: '🔍',
  Bash: '⚙️', PowerShell: '⚙️',
  Skill: '📜', Task: '📋', TaskCreate: '📋', TaskUpdate: '📋', Agent: '🧙',
  WebSearch: '🌐', WebFetch: '🌐',
}
function icon(t) { return TOOL_ICON[t] || '✨' }

const list = computed(() => props.activity.slice(0, 8))
</script>

<template>
  <div class="ticker">
    <div class="ticker__label">📡 現在在做什麼</div>
    <div v-if="list.length === 0" class="ticker__empty">
      尚無即時動態 —— 需先掛上 PostToolUse hook(見 README),之後冒險者的每個動作都會即時出現在這裡。
    </div>
    <div v-else class="ticker__feed">
      <div v-for="(a, i) in list" :key="i" class="tick">
        <span class="tick__i">{{ icon(a.tool) }}</span>
        <span class="tick__tool">{{ a.tool }}</span>
        <span class="tick__sum">{{ a.summary || '—' }}</span>
        <span v-if="a.guild" class="tick__guild">🏰 {{ a.guild }}</span>
        <span class="tick__ago">{{ relativeTime(a.ts) }}</span>
      </div>
    </div>
  </div>
</template>
