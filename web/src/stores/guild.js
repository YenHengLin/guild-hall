import { defineStore } from 'pinia'
import {
  fetchGuilds, fetchAdventurer, fetchReport, submitReport, fetchQueue,
  fetchEvolutions, submitEvolution,
  fetchCommissions, postCommission, completeCommission, reopenCommission,
  deleteCommission, hideAdventurer, fetchActivity,
  approveReport, rejectReport, addAbility, removeAbility,
  patchCommission, dispatchCommission, steerCommission, stopCommission,
  fetchCommissionEvents, trainAdventurer, distillGuild, fetchProposals,
  approveProposal, rejectProposal,
} from '../api'

export const useGuildStore = defineStore('guild', {
  state: () => ({
    guilds: [],
    report: null, // 戰報:今日/本週/熱力圖/成就/布告欄
    queue: null, // 櫃台回報佇列
    evolutions: [], // 冒險者進化候選 / 已習得能力
    commissions: [], // 使用者自己貼的委託
    proposals: [], // 訓練所進化提案(pending 在前)
    training: false, // 訓練所分析進行中(跑 claude)
    trainError: null,
    showTraining: false, // 訓練所面板開關
    activity: [], // 即時動態(hook 寫的工具活動)
    loading: false,
    error: null,
    lastUpdated: null,
    selected: null, // 冒險者細節(目前檢視的那一趟 session)
    personaQuests: [], // 同職角色的所有冒險(session),供抽屜切換
    fixSeed: '', // 從報告「派冒險者接續」帶進 drawer 的預填指示
    detailLoading: false,
    showReport: false, // 戰報面板開關
    reporting: false, // 送出回報中
    _timer: null,
    _actTimer: null,
  }),
  getters: {
    totalAdventurers: (s) => s.guilds.reduce((n, g) => n + g.adventurer_count, 0),
    // 大廳實際站著的職業角色數(persona);舊後端無 personas 時退回 session 數
    totalPersonas: (s) => s.guilds.reduce(
      (n, g) => n + (g.personas?.length || g.adventurer_count), 0),
    totalActive: (s) => s.guilds.reduce((n, g) => n + g.active_count, 0),
    totalTokens: (s) => s.guilds.reduce((n, g) => n + (g.total_tokens || 0), 0),
    totalXp: (s) => s.guilds.reduce((n, g) => n + (g.xp || 0), 0),
    // adventurer_id -> 進化候選
    evoMap: (s) => Object.fromEntries(s.evolutions.map((e) => [e.adventurer_id, e])),
    evolvableCount: (s) => s.evolutions.filter((e) => e.available > 0).length,
    // session_id -> 最新一筆即時活動(activity 已是新→舊,取首見即最新)
    activityBySession: (s) => {
      const m = {}
      for (const a of s.activity) {
        if (a.session_id && !m[a.session_id]) m[a.session_id] = a
      }
      return m
    },
  },
  actions: {
    async load() {
      this.loading = this.guilds.length === 0
      try {
        const [guilds, report, queue, evolutions, commissions, proposals] =
          await Promise.all([
            fetchGuilds(), fetchReport(), fetchQueue(), fetchEvolutions(),
            fetchCommissions(), fetchProposals(),
          ])
        this.guilds = guilds
        this.report = report
        this.queue = queue
        this.evolutions = evolutions
        this.commissions = commissions
        this.proposals = proposals
        this.error = null
        this.lastUpdated = new Date()
      } catch (e) {
        this.error = e?.message || '無法連上公會後端'
      } finally {
        this.loading = false
      }
    },
    openReport() {
      this.showReport = true
    },
    closeReport() {
      this.showReport = false
    },
    // 冒險者向櫃台回報一筆任務完成
    async sendReport(payload) {
      this.reporting = true
      try {
        await submitReport(payload)
        // 立即刷新佇列與進化狀態(回報會累加完成數 → 可能觸發可進化)
        ;[this.queue, this.evolutions] = await Promise.all([
          fetchQueue(), fetchEvolutions(),
        ])
        return true
      } catch (e) {
        return false
      } finally {
        this.reporting = false
      }
    },
    // 登記一次進化(冒險者習得能力)
    async sendEvolution(payload) {
      try {
        await submitEvolution(payload)
        this.evolutions = await fetchEvolutions()
        return true
      } catch (e) {
        return false
      }
    },
    // ── 委託布告欄 ──
    async addCommission(payload) {
      try {
        await postCommission(payload)
        this.commissions = await fetchCommissions()
        return true
      } catch (e) {
        return false
      }
    },
    async toggleCommission(c) {
      if (c.status === 'done') await reopenCommission(c.id)
      else await completeCommission(c.id)
      this.commissions = await fetchCommissions()
    },
    async removeCommission(id) {
      await deleteCommission(id)
      this.commissions = await fetchCommissions()
    },
    // ── 派遣系統 ──
    async dispatch(id, cwd) {
      try {
        await dispatchCommission(id, cwd)
        this.commissions = await fetchCommissions()
        return null
      } catch (e) {
        return e?.response?.data?.detail || e?.message || '派遣失敗'
      }
    },
    async steer(id, content) {
      try {
        await steerCommission(id, content)
        return null
      } catch (e) {
        return e?.response?.data?.detail || '指示送出失敗'
      }
    },
    async stopDispatch(id) {
      await stopCommission(id)
      this.commissions = await fetchCommissions()
    },
    async updateGoal(id, goal) {
      await patchCommission(id, { goal })
      this.commissions = await fetchCommissions()
    },
    loadEvents(id) {
      return fetchCommissionEvents(id)
    },
    // ── 訓練所自動化(提案 → 批准落地)──
    async train(adventurerId) {
      this.training = true
      this.trainError = null
      try {
        await trainAdventurer(adventurerId)
        this.proposals = await fetchProposals()
        this.showTraining = true
        return true
      } catch (e) {
        this.trainError = e?.response?.data?.detail || e?.message || '訓練失敗'
        return false
      } finally {
        this.training = false
      }
    },
    async distill() {
      this.training = true
      this.trainError = null
      try {
        await distillGuild()
        this.proposals = await fetchProposals()
        return true
      } catch (e) {
        this.trainError = e?.response?.data?.detail || e?.message || '提煉失敗'
        return false
      } finally {
        this.training = false
      }
    },
    async decideProposal(id, approve, note) {
      try {
        if (approve) await approveProposal(id)
        else await rejectProposal(id, note || null)
        ;[this.proposals, this.evolutions] = await Promise.all([
          fetchProposals(), fetchEvolutions(),
        ])
        return null
      } catch (e) {
        return e?.response?.data?.detail || '裁決失敗'
      }
    },
    // 統一入口:persona(帶 quests 的物件)→ 開角色;字串 id → 開單一 session
    open(payload) {
      if (payload && typeof payload === 'object' && Array.isArray(payload.quests)) {
        this.openPersona(payload)
      } else {
        this.openAdventurer(payload)
      }
    },
    // 開一位「職業角色」:預設檢視其最近一趟冒險,並帶出可切換的冒險日誌
    openPersona(persona) {
      this.personaQuests = persona.quests || []
      this.openAdventurer(persona.lead_id, true)
    },
    // ── 報告審核(使用者當幕後審核）──
    async reviewApprove(id) {
      await approveReport(id)
      this.queue = await fetchQueue()
    },
    async reviewReject(id, note) {
      await rejectReport(id, note)
      this.queue = await fetchQueue()
    },
    // 從一份報告「派冒險者接續」:關戰報、開該冒險者、預填 fix 指示
    continueFromReport(report) {
      if (!report.adventurer_id) return
      const why = report.review_note ? `(退回原因:${report.review_note})` : ''
      this.fixSeed = `接續改善「${report.title}」${why}`.trim()
      this.showReport = false
      this.openAdventurer(report.adventurer_id)
    },
    async openAdventurer(id, keepSiblings = false) {
      if (!keepSiblings) this.personaQuests = []
      this.detailLoading = true
      try {
        this.selected = await fetchAdventurer(id)
      } catch (e) {
        this.selected = null
      } finally {
        this.detailLoading = false
      }
    },
    closeAdventurer() {
      this.selected = null
      this.personaQuests = []
    },
    // ── 自訂能力(手動編輯冒險者能力）──
    async addAbility(name) {
      if (!this.selected) return
      const list = await addAbility(this.selected.id, name)
      this.selected.custom_abilities = list
    },
    async removeAbility(aid) {
      if (!this.selected) return
      const list = await removeAbility(this.selected.id, aid)
      this.selected.custom_abilities = list
    },
    // 讓冒險者退隱(從面板隱藏,不刪 session 檔)
    async retireAdventurer(id) {
      await hideAdventurer(id)
      this.selected = null
      await this.load()
    },
    async loadActivity() {
      try {
        this.activity = await fetchActivity()
      } catch (e) { /* 靜默,不干擾主畫面 */ }
    },
    startPolling(ms = 15000) {
      this.load()
      this.loadActivity()
      this.stopPolling()
      this._timer = setInterval(() => this.load(), ms)
      // 即時動態刷新較快(5s),讓「現在在做什麼」有即時感
      this._actTimer = setInterval(() => this.loadActivity(), 5000)
    },
    stopPolling() {
      if (this._timer) clearInterval(this._timer)
      if (this._actTimer) clearInterval(this._actTimer)
      this._timer = null
      this._actTimer = null
    },
  },
})
