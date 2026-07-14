<script setup>
import { computed } from 'vue'
import { statusMeta } from '../labels'

const props = defineProps({ status: String })
const meta = computed(() => statusMeta(props.status))
</script>

<template>
  <span class="status" :style="{ background: meta.color }">
    <span>{{ meta.emoji }}</span>{{ meta.label }}
  </span>
</template>
