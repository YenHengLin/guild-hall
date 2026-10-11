<script setup>
// 派遣位階(模型)選擇器:下拉選內建位階,或選「✍️ 自訂模型…」後打任何 cloud 模型名。
// v-model 綁的是「實際模型字串」('' = 跟隨 CLI 預設;自訂時就是使用者打的字串)。
// 全站(布告欄三處派遣選單 + 夜巡設定)共用這一個元件,位階清單統一吃 labels.js 的 RANKS。
import { computed, ref, watch } from 'vue'
import { RANKS, CUSTOM_RANK } from '../labels'

const props = defineProps({
  modelValue: { type: String, default: '' },
  title: { type: String, default: '冒險者位階:派遣時用哪個模型' },
})
const emit = defineEmits(['update:modelValue'])

// 已知位階值集合;modelValue 不在其中且非空 → 視為自訂模型
const known = computed(() => new Set(RANKS.map((r) => r.value).filter((v) => v !== CUSTOM_RANK)))
const isCustom = (v) => !!v && !known.value.has(v)

// select 的選取值:自訂時顯示為哨兵,其餘直接等於 modelValue
const picked = ref(isCustom(props.modelValue) ? CUSTOM_RANK : props.modelValue)
const customText = ref(isCustom(props.modelValue) ? props.modelValue : '')

// 外部改了 modelValue(如展開委託帶入現值)→ 同步內部狀態
watch(() => props.modelValue, (v) => {
  if (isCustom(v)) { picked.value = CUSTOM_RANK; customText.value = v }
  else { picked.value = v; if (v !== CUSTOM_RANK) customText.value = '' }
})

function onSelect(e) {
  picked.value = e.target.value
  // 選到自訂:先沿用已打的字(可能為空),等使用者輸入;其餘直接送出該位階值
  emit('update:modelValue', picked.value === CUSTOM_RANK ? customText.value : picked.value)
}
function onCustomInput(e) {
  customText.value = e.target.value
  emit('update:modelValue', customText.value)
}
</script>

<template>
  <span class="model-picker">
    <select :value="picked" :title="title" @change="onSelect">
      <option v-for="r in RANKS" :key="r.value" :value="r.value">{{ r.label }}</option>
    </select>
    <input v-if="picked === CUSTOM_RANK" class="model-picker__custom"
           :value="customText" @input="onCustomInput"
           placeholder="模型名,如 opus / claude-sonnet-4-6 / claude-fable-5" />
  </span>
</template>

<style scoped>
.model-picker { display: inline-flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; }
.model-picker__custom { min-width: 14rem; }
</style>
