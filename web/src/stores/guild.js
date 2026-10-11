import { defineStore } from 'pinia'
import {
  fetchGuilds, fetchAdventurer, fetchReport, submitReport, polishReport, fetchQueue,
  fetchLessons, reclassifyLessons,
  fetchEvolutions, submitEvolution,
  fetchCommissions, fetchDomains, postCommission, completeCommission, reopenCommission,
  deleteCommission, hideAdventurer, fetchActivity,
  rejectReport, addAbility, removeAbility, setIdentity,
  patchCommission, dispatchCommission, enqueueCommission, steerCommission, stopCommission,
  fetchCommissionEvents, trainAdventurer, distillGuild, fetchProposals,
  approveProposal, rejectProposal, fetchCosts,
  fetchMuseIdeas, fetchMuseConfig, fetchMuseStatus, saveMuseConfig, runMuse,
  autoRunMuse, promoteIdea, dismissIdea, deleteIdea,
} from '../api'
import { RECEPTION_ID, RECEPTION_PERSONA } from '../labels'

export const useGuildStore = defineStore('guild', {
  state: () => ({
    guilds: [],
    report: null, // 戰報:今日/本週/熱力圖/成就/布告欄
    queue: null, // 櫃台回報佇列
    evolutions: [], // 冒險者進化候選 / 已習得能力
    commissions: [], // 使用者自己貼的委託
    domains: [], // 既有領域公會名稱(供表單建議)
    costs: null, // 成本帳本:{ total, months:[{month,total,sources}] }
    proposals: [], // 訓練所進化提案(pending 在前)
    training: false, // 訓練所分析進行中(跑 claude)
    trainError: null,
    showTraining: false, // 訓練所面板開關
    // ── 公會夜巡(NPC 自主靈感)──
    museIdeas: [], // 夜巡產出的靈感卡
    museConfig: null, // { config, available, spent_today, budget_left }
    museStatus: null, // 夜巡即時進度 { running, guild, step, steps, ... }
    showMuse: false, // 夜巡面板開關
    musing: false, // 手動巡邏進行中(跑 claude)
    museError: null,
    _museTimer: null,
    activity: [], // 即時動態(hook 寫的工具活動)
    toasts: [], // 右側浮動通知(完成 / 受阻需回應 / 失敗)
    focusCid: null, // 要求布告欄展開/捲動到的委託 id(點通知時設定)
    _prevComm: {}, // 委託 id → 上次看到的狀態(偵測轉換用)
    _toastSeq: 0,
    loading: false,
    error: null,
    lastUpdated: null,
    selected: null, // 冒險者細節(目前檢視的那一趟 session)
    personaQuests: [], // 同職角色的所有冒險(session),供抽屜切換
    fixSeed: '', // 從報告「派冒險者接續」帶進 drawer 的預填指示
    detailLoading: false,
    // 派遣前確認偏好:會實際改檔+花 token 的操作預設攔一下;使用者可勾「不再詢問」關掉。
    // localStorage 持久化,重開瀏覽器仍記得。
    skipDispatchConfirm: localStorage.getItem('guild.skipDispatchConfirm') === '1',
    showReport: false, // 戰報面板開關
    lessons: [], // 學習書架:[{category, count, lessons:[...]}]
    showLessons: false, // 學習書架面板開關
    classifying: false, // AI 整理書架進行中
    reporting: false, // 送出回報中
    polishing: false, // AI 潤稿草稿中
    polishError: null,
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
    // 未處理的夜巡靈感卡數(給招牌按鈕徽章)
    newIdeaCount: (s) => s.museIdeas.filter((i) => i.status === 'new').length,
    // 派遣佇列:委託 id → 排隊位置(1-based,FIFO by id)
    queuePos: (s) => {
      const q = s.commissions
        .filter((c) => c.status === 'queued')
        .sort((a, b) => a.id - b.id)
      return Object.fromEntries(q.map((c, i) => [c.id, i + 1]))
    },
    // session_id -> 冒險者身份(職業圖示/職業名/公會),供「這件事誰做的」標示。
    // 來源:各公會 persona 的成員 quests(後端已算好 icon/klass)。派遣產生的
    // headless session 下次掃描後也會歸入某 persona;還沒掃到則反查不到(不顯示標)。
    personaBySession: (s) => {
      const m = {}
      for (const g of s.guilds) {
        for (const p of g.personas || []) {
          const ident = { icon: p.icon, klass: p.klass, guild: p.guild }
          for (const q of p.quests || []) m[q.id] = ident
        }
      }
      // 主 session 主動回報一律歸「櫃台書記」:戰報標得出作者,又不佔真冒險者名冊
      m[RECEPTION_ID] = { ...RECEPTION_PERSONA }
      return m
    },
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
        const [guilds, report, queue, evolutions, commissions, proposals, costs,
          museIdeas] =
          await Promise.all([
            fetchGuilds(), fetchReport(), fetchQueue(), fetchEvolutions(),
            fetchCommissions(), fetchProposals(), fetchCosts(),
            fetchMuseIdeas().catch(() => []),
          ])
        this.guilds = guilds
        this.report = report
        this.queue = queue
        this.evolutions = evolutions
        this._detectCommissionChanges(commissions)
        this.commissions = commissions
        this.proposals = proposals
        this.costs = costs
        this.museIdeas = museIdeas
        if (!this.domains.length) this.loadDomains()
        this.error = null
        this.lastUpdated = new Date()
      } catch (e) {
        this.error = e?.message || '無法連上公會後端'
      } finally {
        this.loading = false
      }
    },
    // 派遣類操作的確認閘:回傳 true 才放行。開了「不再詢問」就一律放行。
    // msg 需含花費/改檔提示;使用者可在對話框勾選記住選擇。
    confirmDispatch(msg) {
      if (this.skipDispatchConfirm) return true
      return window.confirm(msg + '\n\n(可在貼委託視窗勾選「派遣前不再確認」關掉此提示)')
    },
    setSkipDispatchConfirm(v) {
      this.skipDispatchConfirm = !!v
      localStorage.setItem('guild.skipDispatchConfirm', v ? '1' : '0')
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
    // 設定冒險者身份(取名 / 職業)→ 存後端 → 刷新大廳(取名會讓角色獨立站出來)
    async setIdentity(id, payload) {
      try {
        await setIdentity(id, payload)
        await this.load()
        return true
      } catch (e) {
        return false
      }
    },
    // 把草稿回報送 AI 潤稿:回傳整理後 {title, summary, lessons} 或 null(失敗)
    async polishDraft(payload) {
      this.polishing = true
      this.polishError = null
      try {
        return await polishReport(payload)
      } catch (e) {
        this.polishError = e?.response?.data?.detail || e.message || '潤稿失敗'
        return null
      } finally {
        this.polishing = false
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
        const created = await postCommission(payload)
        this.commissions = await fetchCommissions()
        this.loadDomains() // 可能有新領域公會
        return created // 含 id,供「貼上並立即派遣」接著派工
      } catch (e) {
        return null
      }
    },
    async loadDomains() {
      try { this.domains = await fetchDomains() } catch (e) { /* 靜默 */ }
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
    // 學習書架:載入所有教學字卡(依分類彙整)
    async loadLessons() {
      try {
        const d = await fetchLessons()
        this.lessons = d.categories || []
      } catch (e) { /* 靜默 */ }
    },
    openLessons() {
      this.loadLessons()
      this.showLessons = true
    },
    // 🪄 整理書架:AI 替未分類 / 缺延伸主題的卡補上,再重載
    async reclassifyLessons() {
      this.classifying = true
      try {
        const d = await reclassifyLessons()
        await this.loadLessons()
        return d
      } catch (e) {
        return null
      } finally {
        this.classifying = false
      }
    },
    // 教學卡「延伸深入」:一鍵建一張新的書記官委託並派遣(同資料夾、教學皮膚)
    async deepenLesson(topic, guildName) {
      const g = this.guilds.find((x) => x.name === guildName)
      const created = await this.addCommission({
        title: topic, goal: topic,
        guild: guildName || null, cwd: g?.path || null, klass: '書記官',
      })
      if (created && g?.path) await this.dispatch(created.id, g.path, null)
      return created
    },
    // ── 派遣系統 ──
    async dispatch(id, cwd, model) {
      try {
        await dispatchCommission(id, cwd, model)
        this.commissions = await fetchCommissions()
        return null
      } catch (e) {
        return e?.response?.data?.detail || e?.message || '派遣失敗'
      }
    },
    async enqueue(id, cwd, model) {
      try {
        await enqueueCommission(id, cwd, model)
        this.commissions = await fetchCommissions()
        return null
      } catch (e) {
        return e?.response?.data?.detail || e?.message || '加入佇列失敗'
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
    // 整張委託重新編輯:標題 / 目標 / 備註 / 位階 / 皮膚 / 附圖一次更新
    async updateCommission(id, payload) {
      try {
        const updated = await patchCommission(id, payload)
        this.commissions = await fetchCommissions()
        return updated
      } catch (e) {
        return null
      }
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
    // ── 公會夜巡(NPC 自主靈感)──
    async loadMuse() {
      try {
        ;[this.museIdeas, this.museConfig, this.museStatus] = await Promise.all([
          fetchMuseIdeas(), fetchMuseConfig(), fetchMuseStatus(),
        ])
      } catch (e) { /* 靜默 */ }
    },
    // 面板開著時輪詢即時進度;巡完(running=false)自動刷新靈感卡
    startMusePolling() {
      this.stopMusePolling()
      this._museTimer = setInterval(async () => {
        try {
          const prev = this.museStatus?.running
          this.museStatus = await fetchMuseStatus()
          if (prev && !this.museStatus.running) {
            ;[this.museIdeas, this.museConfig] = await Promise.all([
              fetchMuseIdeas(), fetchMuseConfig(),
            ])
          }
        } catch (e) { /* 靜默 */ }
      }, 2500)
    },
    stopMusePolling() {
      if (this._museTimer) clearInterval(this._museTimer)
      this._museTimer = null
    },
    async saveMuseConfig(cfg) {
      this.museError = null
      try {
        this.museConfig = await saveMuseConfig(cfg)
        return null
      } catch (e) {
        this.museError = e?.response?.data?.detail || '設定儲存失敗'
        return this.museError
      }
    },
    async runMuseNow() {
      this.musing = true
      this.museError = null
      try {
        const res = await runMuse()
        await this.loadMuse()
        return res
      } catch (e) {
        this.museError = e?.response?.data?.detail || e?.message || '夜巡失敗'
        return null
      } finally {
        this.musing = false
      }
    },
    // 不巡邏,直接從既有清單挑可自動執行的做掉
    async autoRunNow() {
      this.museError = null
      try {
        const res = await autoRunMuse()
        await Promise.all([this.loadMuse(), this.refreshCommissions()])
        return res
      } catch (e) {
        this.museError = e?.response?.data?.detail || e?.message || '自動執行失敗'
        return null
      }
    },
    async promoteIdea(id) {
      try {
        const c = await promoteIdea(id)
        await Promise.all([this.loadMuse(), this.refreshCommissions()])
        return c
      } catch (e) {
        this.museError = e?.response?.data?.detail || '採納失敗'
        return null
      }
    },
    async dismissIdea(id) {
      await dismissIdea(id)
      await this.loadMuse()
    },
    async removeIdea(id) {
      await deleteIdea(id)
      await this.loadMuse()
    },
    async refreshCommissions() {
      this.commissions = await fetchCommissions()
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
    // ── 報告審核(使用者當幕後審核;認可對 AI 無作用已移除,只留退回重寫)──
    async reviewReject(id, note) {
      await rejectReport(id, note)
      this.queue = await fetchQueue()
    },
    // 從一份報告「派冒險者接續」:關戰報、開該冒險者、預填 fix 指示
    continueFromReport(report) {
      // 櫃台書記的主動總結沒有真 session,無法召回接續
      if (!report.adventurer_id || report.adventurer_id === RECEPTION_ID) return
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
    // ── 浮動通知(toast)──
    // 派遣狀態轉換時跳出右側通知,並(若使用者允許)發系統通知,
    // 解決「完成 / 冒險者反問了卻沒被注意到」的痛點。
    pushToast(t) {
      const id = ++this._toastSeq
      const toast = { id, ...t }
      this.toasts.push(toast)
      // 成功類自動淡出;需要回應(受阻/失敗)的留著等使用者關掉
      if (t.kind === 'done') {
        setTimeout(() => this.dismissToast(id), 8000)
      }
      // 系統層級通知:即使切到別的視窗也「跳出來」
      this._systemNotify(t)
      return id
    },
    dismissToast(id) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
    // 點通知:關掉它並要求布告欄展開/捲動到該委託
    focusCommission(cid) {
      this.focusCid = cid
      this.toasts = this.toasts.filter((t) => t.cid !== cid)
    },
    _systemNotify(t) {
      try {
        if (typeof Notification === 'undefined') return
        if (Notification.permission !== 'granted') return
        new Notification(t.title, { body: t.body || '', tag: 'guild-' + t.cid })
      } catch (e) { /* 靜默 */ }
    },
    // 比對前後委託狀態,working → done/blocked/failed 時發通知
    _detectCommissionChanges(next) {
      const prev = this._prevComm
      const first = Object.keys(prev).length === 0
      for (const c of next) {
        const was = prev[c.id]
        if (!first && was === 'working' && c.status !== 'working') {
          if (c.status === 'done') {
            this.pushToast({
              cid: c.id, kind: 'done', icon: '🎉',
              title: '委託完成', body: c.title,
              hint: '冒險者已完成,櫃台正在歸檔回報',
            })
          } else if (c.status === 'blocked') {
            this.pushToast({
              cid: c.id, kind: 'blocked', icon: '🧱',
              title: '冒險者需要你回應', body: c.title,
              hint: '冒險者卡住或有疑問,展開委託看內容並給指示',
            })
          } else if (c.status === 'failed') {
            this.pushToast({
              cid: c.id, kind: 'failed', icon: '💥',
              title: '這回合失敗了', body: c.title,
              hint: '展開委託看原因,調整後可繼續',
            })
          }
        }
      }
      this._prevComm = Object.fromEntries(next.map((c) => [c.id, c.status]))
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
