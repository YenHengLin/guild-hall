# 冒險者公會 — 完善路線圖

> 已完成:MVP1 公會大廳(唯讀觀測) + RPG 大廳場景 + 成長系統(戰報/等級/熱力圖/成就)。
> 以下是接下來要蓋的兩大系統,依「先手動、不改 Claude 設定」→「再自動化」的順序推進。

---

## ★ 執行層:召回冒險者(✅ 2026-07-06,面板從觀測→指揮中心)

用 session id 復活原本的 Claude(`claude -p -r <sid> ... --output-format json`),讓最懂那件事的 Claude:
- [x] **請他回報** — resume→寫結案回報→入列歸檔(haiku 省錢)。品質遠勝手動。
- [x] **問細節** — resume + 使用者提問,帶完整脈絡回答(不歸檔)。
- [x] **接續做事** — 開新終端跑 `claude --resume <sid>`,使用者親自接續。

`app/recall.py` + `app/routers/recall.py`。護欄:plan 唯讀模式、逾時 180s、一次一個、report 用 haiku、前端 confirm 提醒花 token。
坑:Windows uvicorn SelectorEventLoop 不支援 async subprocess → 用 `subprocess.run`+`asyncio.to_thread`。
- [ ] **下一步**:召回可選模型/顯示累積花費、「請他回報」前先預覽再歸檔、批次召回久未回報的冒險者。

---

## A. 任務回報 → 櫃台記錄 → 寫入 LLM Wiki(帶寫入佇列)

**核心流程**:冒險者完成任務 → 向櫃台「回報」→ 櫃台把成果整理後寫回 [[LLM Wiki]]。
**關鍵限制**:多位冒險者可同時回報,但櫃台寫的是**共享的 wiki 檔案**(`index.md` / `log.md` / 同一主題頁),
併發寫會互相覆蓋 → **必須用單一消費者的佇列把寫入序列化**。

- [x] **A1 佇列資料層** — SQLite `guild.db`,`reports` 佇列表(`app/db.py`)。✅ 2026-07-05
- [x] **A2 回報入口(手動)** — `POST /api/reports` + 使用者級 skill `公會報告`(PowerShell 送出)
      + 大廳抽屜「回報此任務」按鈕。完全不碰 Claude 設定。✅ 2026-07-05
- [x] **A3 櫃台工人(單一消費者)** — `app/reception.py` worker,`claim_next` 逐筆處理。
      已用 5 筆併發測試驗證:共享的 `log.md` 5 筆全在、無競寫。✅ 2026-07-05
- [x] **A4 寫入邏輯(v1 結構化)** — 寫入 `wiki/公會回報/<guild>.md` + 前插 `wiki/log.md`。
      **git commit 預設關**(`GUILD_GIT_COMMIT=1` 開;D:\ 是龐大 repo 故預設不提交)。✅ 2026-07-05
- [x] **A5 UI** — 櫃台待處理徽章、戰報面板「櫃台回報紀錄」清單(⏳/⚙️/🗂 狀態)。✅ 2026-07-05
- [x] **A4+ 深度合成** — `app/synthesis.py`:存檔完成後 spawn headless Claude(haiku、
      限 Read/Glob/Grep/Edit/Write、max-turns 15、逾時 300s),依 llm-wiki-manager 準則把回報
      綜合進對應主題頁 + 補 `index.md` 連結。合成失敗不影響歸檔(存檔是保底);
      `GUILD_SYNTHESIS=0` 關閉、`GUILD_SYNTH_MODEL` 換模型。單一 worker 天然序列化,免加鎖。
      已用真實回報驗證:主題頁新增章節、里程碑/frontmatter/index 摘要同步更新。✅ 2026-07-07
- [ ] **A6 自動化(選配,需你同意)** — 用 Claude Code **Stop hook** 在 session 結束時自動回報,
      取代手動 skill。此步會改 `settings.json`,實作前會先問過你。

---

## B. 冒險者進化系統(能力成長)

**核心概念**:冒險者根據「處理過的問題」進化能力。**每完成 5 次任務(回報)可進化一次**。

- [x] **B0 觸發點** — 完成任務數 // 5 = 可進化次數。`evolutions` 表記已進化數,available = earned − claimed。
      大廳小人「可進化」時發金光 ✨。✅ 2026-07-06
- [x] **B1 素材匯整** — `GET /api/evolutions` 匯整該冒險者近期解決的任務(recent_titles/lessons)+
      啟發式建議路線(`suggested_route`)。深度反思(agent-hippocampus)由訓練所 skill 在 LLM 端做。✅ 2026-07-06
- [x] **B2 四條進化路線** — 由 `訓練所` skill(`~/.claude/skills/訓練所/`)在 Claude 內執行,
      「提議→你確認→執行」,完成後 `POST /api/evolutions` 登記:
  - [x] **① 記住通用能力** → 寫 memory 檔  ▪ **② skill** → skill-creator
  - [x] **③ 找更好工具** → WebSearch/gemini  ▪ **④ 自己寫** → scripts/ 腳本
      (路線框架 + 登記完成;實際產出品質依每次 skill 執行。)✅ 2026-07-06
- [x] **B3 UI** — 抽屜進化區塊:進度條(距下次還差 N 次)、可進化時複製訓練所指令 / 手動登記、
      習得能力清單;小人金光 + 🎓 徽章;招牌「可進化 N」。✅ 2026-07-06
- [x] **B4 自動化(提案制)** — `app/training.py`:面板一鍵送訓練所 → spawn headless
      Claude(唯讀 Read/Glob/Grep、sonnet、逾時 300s)分析素材產 JSON 提案存
      `evolution_proposals`;訓練所面板(TrainingHall.vue)批准後才由後端寫 artifact
      (路徑白名單 ~/.claude、D:\projects、D:\myInfo;既有檔備份 .bak;memory 路線
      自動補 MEMORY.md 索引)+ 登記進化。另有「⚗️ 全公會提煉」:跨任務掃 done 回報,
      依 llm-wiki-manager Prune 準則提取通則。✅ 2026-07-14
- [ ] **B5 下一步** — 提案品質評分、進化後成效追蹤(該 skill 被觸發幾次)、
      達 5 次自動觸發訓練(免手按)。

---

## C. 體驗打磨(可穿插進行)

- [ ] 升級特效:小人升級時冒出 "LEVEL UP!" 動畫。
- [ ] 日夜氛圍:大廳依現在時間變白天 / 夜晚。
- [ ] 公會 / 冒險者排行榜(週榜、總戰力榜)。
- [ ] 音效(開門、翻布告欄、升級鈴聲)。
- [x] **委託布告欄(可貼/可刪)** — 使用者在網頁貼委託 → 顯示「招募中」→ 可標記完成 / 刪除。
      `commissions` 表 + `/api/commissions` CRUD;NoticeBoard 分「招募中 / 已完成 / 進行中的冒險」。✅ 2026-07-06
- [x] **MVP2+ 派遣系統(認領的進化版)** — `app/dispatch.py`:委託直接「🚀 派遣」給
      headless Claude 回合制執行(首回合開新 session、之後 -r 續跑);**回合之間**注入
      最新目標(PATCH goal)與中途指示(steer)→ 中途調整任務;DONE 自動把 REPORT
      enqueue 進櫃台佇列(→ 歸檔 + 深度合成)+ 委託自動完成 → 與系統 A 閉環。
      BLOCKED / 回合用盡可「再派遣」續同一 session。護欄:一次一個派遣、回合上限 6、
      單回合逾時 600s、工具白名單、acceptEdits。UI:布告欄委託展開 = 進度時間線 +
      steer 輸入 + 調整目標 + 停止。✅ 2026-07-14

---

## 技術決策 / 風險備忘

- **佇列選型**:SQLite + 單一 worker。理由:簡單可靠、天然序列化寫入、免額外服務。
  不用 Redis/RabbitMQ(殺雞用牛刀,本機單人場景不需要)。
- **回報入口順序**:先做手動 skill(零設定變更)→ 驗證流程 OK → 再上 Stop hook 自動化(需同意)。
- **寫 wiki 一律經 git**:每筆回報 = 一個 commit,寫壞可 revert,符合大腦「可自我糾錯」哲學。
- **進化要有人把關**:B2 的四條路線都先「提議 → 你確認 → 才真正建立 skill / 改 memory / 裝工具」,
  避免自動亂長。之後信任了再放寬。
