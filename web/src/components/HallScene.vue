<script setup>
import NoticeBoard from './NoticeBoard.vue'
import ReceptionDesk from './ReceptionDesk.vue'
import GuildStation from './GuildStation.vue'

defineProps({ guilds: Array, report: Object, queue: Object, commissions: Array })
const emit = defineEmits(['open', 'openReport'])
</script>

<template>
  <div class="room">
    <!-- 後牆:掛布告欄 + 櫃檯 -->
    <div class="room__wall">
      <NoticeBoard
        :quests="report?.quests || []"
        :commissions="commissions || []"
        :queue="queue"
        @open="(id) => emit('open', id)"
      />
      <ReceptionDesk :report="report" :queue="queue" @open="emit('openReport')" />
    </div>

    <!-- 地板:各公會的站台 -->
    <div class="room__floor">
      <div class="room__floorlabel">— 冒險者大廳 —</div>
      <div class="stations">
        <GuildStation
          v-for="g in guilds"
          :key="g.dir_key"
          :guild="g"
          @open="(id) => emit('open', id)"
        />
      </div>
    </div>
  </div>
</template>
