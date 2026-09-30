import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 30000 })

export const fetchGuilds = () => http.get('/guilds').then((r) => r.data)
export const fetchAdventurer = (id) => http.get(`/adventurers/${id}`).then((r) => r.data)
export const hideAdventurer = (id) => http.post(`/adventurers/${id}/hide`).then((r) => r.data)
export const addAbility = (id, name) =>
  http.post(`/adventurers/${id}/abilities`, { name }).then((r) => r.data)
export const removeAbility = (id, aid) =>
  http.delete(`/adventurers/${id}/abilities/${aid}`).then((r) => r.data)
export const unhideAdventurer = (id) => http.post(`/adventurers/${id}/unhide`).then((r) => r.data)
// 設定冒險者身份:取名 / 指定職業(取名會讓這位從合併的職業角色獨立出來)
export const setIdentity = (id, payload) =>
  http.post(`/adventurers/${id}/identity`, payload).then((r) => r.data)
export const fetchReport = () => http.get('/report').then((r) => r.data)
export const fetchActivity = () => http.get('/activity').then((r) => r.data)
export const submitReport = (payload) => http.post('/reports', payload).then((r) => r.data)
// 回報潤稿:草稿 → AI 整理(較久,給長 timeout)
export const polishReport = (payload) =>
  http.post('/reports/polish', payload, RECALL_TO).then((r) => r.data)
export const fetchQueue = () => http.get('/reports/queue').then((r) => r.data)
// 學習書架:所有教學字卡依 AI 分類彙整
export const fetchLessons = () => http.get('/lessons').then((r) => r.data)
// 整理書架:替未分類 / 缺延伸主題的卡補上(跑便宜模型,較久)
export const reclassifyLessons = () =>
  http.post('/lessons/reclassify', {}, RECALL_TO).then((r) => r.data)
export const approveReport = (id) => http.post(`/reports/${id}/approve`).then((r) => r.data)
export const rejectReport = (id, note) =>
  http.post(`/reports/${id}/reject`, { note }).then((r) => r.data)
export const fetchEvolutions = () => http.get('/evolutions').then((r) => r.data)
export const submitEvolution = (payload) => http.post('/evolutions', payload).then((r) => r.data)
// 召回冒險者(會跑 claude,較久,給長 timeout)
const RECALL_TO = { timeout: 210000 }
export const recallAsk = (id, question) =>
  http.post('/recall/ask', { adventurer_id: id, question }, RECALL_TO).then((r) => r.data)
export const recallContinue = (id) =>
  http.post('/recall/continue', { adventurer_id: id }).then((r) => r.data)

// 貼委託附圖:把剪貼簿/選檔圖片以 base64 data URL 上傳,回 /uploads/... URL
export const uploadImage = (dataUrl) =>
  http.post('/uploads', { data_url: dataUrl }).then((r) => r.data.url)

export const fetchCommissions = () => http.get('/commissions').then((r) => r.data)
export const fetchCosts = () => http.get('/costs').then((r) => r.data)
export const postCommission = (payload) => http.post('/commissions', payload).then((r) => r.data)
export const completeCommission = (id) => http.post(`/commissions/${id}/done`).then((r) => r.data)
export const reopenCommission = (id) => http.post(`/commissions/${id}/reopen`).then((r) => r.data)
export const deleteCommission = (id) => http.delete(`/commissions/${id}`).then((r) => r.data)

// ── 派遣系統(headless 冒險者回合制執行委託)──
export const patchCommission = (id, payload) =>
  http.patch(`/commissions/${id}`, payload).then((r) => r.data)
export const dispatchCommission = (id, cwd, model) =>
  http.post(`/commissions/${id}/dispatch`,
    { cwd: cwd || null, model: model ?? null }).then((r) => r.data)
export const enqueueCommission = (id, cwd, model) =>
  http.post(`/commissions/${id}/enqueue`,
    { cwd: cwd || null, model: model ?? null }).then((r) => r.data)
export const steerCommission = (id, content) =>
  http.post(`/commissions/${id}/steer`, { content }).then((r) => r.data)
export const stopCommission = (id) => http.post(`/commissions/${id}/stop`).then((r) => r.data)
export const fetchCommissionEvents = (id) =>
  http.get(`/commissions/${id}/events`).then((r) => r.data)

// ── 訓練所自動化(產提案 → 批准落地;會跑 claude,較久)──
const TRAIN_TO = { timeout: 330000 }
export const trainAdventurer = (id) =>
  http.post('/training/propose', { adventurer_id: id }, TRAIN_TO).then((r) => r.data)
export const distillGuild = () => http.post('/training/distill', {}, TRAIN_TO).then((r) => r.data)
export const fetchProposals = () => http.get('/proposals').then((r) => r.data)

// ── 公會夜巡(NPC 自主靈感)──
export const fetchMuseIdeas = () => http.get('/muse/ideas').then((r) => r.data)
export const fetchMuseConfig = () => http.get('/muse/config').then((r) => r.data)
export const fetchMuseStatus = () => http.get('/muse/status').then((r) => r.data)
export const saveMuseConfig = (cfg) => http.put('/muse/config', cfg).then((r) => r.data)
// 手動巡一輪會跑 claude,較久,給長 timeout
export const runMuse = () => http.post('/muse/run', {}, { timeout: 480000 }).then((r) => r.data)
// 不巡邏,直接從既有靈感挑可自動執行的做掉
export const autoRunMuse = () => http.post('/muse/auto-run').then((r) => r.data)
export const promoteIdea = (id) => http.post(`/muse/ideas/${id}/promote`).then((r) => r.data)
export const dismissIdea = (id) => http.post(`/muse/ideas/${id}/dismiss`).then((r) => r.data)
export const deleteIdea = (id) => http.delete(`/muse/ideas/${id}`).then((r) => r.data)
export const approveProposal = (id) => http.post(`/proposals/${id}/approve`).then((r) => r.data)
export const rejectProposal = (id, note) =>
  http.post(`/proposals/${id}/reject`, { note }).then((r) => r.data)
