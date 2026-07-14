<script setup>
import { computed } from 'vue'
import CharacterSprite from './CharacterSprite.vue'
import { xpProgress, shortTokens } from '../labels'

const props = defineProps({ guild: Object })
const emit = defineEmits(['open'])

const xp = computed(() => xpProgress(props.guild.xp))
const active = computed(() => props.guild.active_count > 0)
// 站台上站的是「職業角色」(persona);多個同職 session 已收斂成一位。
// 舊後端沒有 personas 時退回原本的 session 清單,確保相容。
const roster = computed(() => props.guild.personas?.length
  ? props.guild.personas
  : props.guild.adventurers)
// 站位:最多顯示前 8 位角色,避免爆版
const shown = computed(() => roster.value.slice(0, 8))
const overflow = computed(() => roster.value.length - shown.value.length)
</script>

<template>
  <div class="station" :class="{ 'is-active': active }">
    <!-- 木牌:公會名 + 等級 + 經驗條 -->
    <div class="station__banner">
      <div class="station__name">
        <span class="station__torch" v-if="active">🔥</span>
        🏰 {{ guild.name }}
      </div>
      <div class="station__lv">Lv.{{ xp.level }}</div>
    </div>
    <div class="station__xp" :title="`戰功 ${guild.xp} · 消耗 ${shortTokens(guild.total_tokens)} tokens`">
      <i :style="{ width: xp.pct + '%' }"></i>
      <span class="station__xptext">{{ xp.pct }}%</span>
    </div>

    <!-- 站台:冒險者們站在這裡 -->
    <div class="station__stage">
      <CharacterSprite
        v-for="a in shown"
        :key="a.id"
        :adventurer="a"
        @open="(payload) => emit('open', payload)"
      />
      <div v-if="overflow > 0" class="station__more">＋{{ overflow }}</div>
    </div>
  </div>
</template>
