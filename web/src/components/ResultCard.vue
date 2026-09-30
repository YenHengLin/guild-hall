<script setup>
// 職業結果卡分派器:依 card.kind 選對應職業皮膚。
// 目前只有 coding;之後新增職業(字卡…)只要在這裡多一個分支 + 一個元件。
// 認不得的 kind / 無 card → 回傳 false,由呼叫端退回 markdown 呈現。
import CodingCard from './CodingCard.vue'
import FlashCards from './FlashCards.vue'

defineProps({
  card: { type: Object, default: null },
})
defineEmits(['deepen'])

const RENDERERS = { coding: CodingCard, teaching: FlashCards }
</script>

<template>
  <component v-if="card && RENDERERS[card.kind]" :is="RENDERERS[card.kind]"
             :card="card" @deepen="$emit('deepen', $event)" />
</template>
