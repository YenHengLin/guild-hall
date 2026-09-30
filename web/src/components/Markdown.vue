<script setup>
// 把冒險者回報 / 心得等 markdown 文字渲染成 HTML。
// 內容來自本機 LLM 輸出(單機自用),故不接受外部不受信任來源。
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({
  text: { type: String, default: '' },
})

// breaks:true → 單一換行也視為 <br>,貼近 LLM 逐行書寫的習慣;gfm 支援清單、粗體、程式碼區塊等。
// marked 會放行內嵌的原始 HTML,LLM 幻覺輸出可能夾帶 <img onerror> / <script> 等,
// 故再過一層 DOMPurify.sanitize 才交給 v-html,避免任意腳本執行。
const html = computed(() => {
  if (!props.text) return ''
  const raw = marked.parse(props.text, { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw)
})
</script>

<template>
  <div class="md" v-html="html"></div>
</template>
