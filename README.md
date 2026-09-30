# 冒險者公會大廳 (Guild Hall)

把散落在各資料夾的 Claude Code session 包裝成 RPG 冒險者公會的**大廳場景**面板。

- **公會** = 一個資料夾(`~/.claude/projects/<dir>`),在地板上是一座站台
- **冒險者** = 一個 session(`.jsonl`),是站台上的一個 CSS 小人
- **狀態** = 依最後活動時間:⚔️ 冒險中(<10min,跳動)/ 🍺 休息中(<24h,搖擺)/ 💤 久未歸來(灰階)
- **職業** = 依最擅長技能推派(術士/工程師/書記官…)
- **等級 / XP** = token 用量換算,角色與公會皆有經驗條

唯讀,不需要 hooks、不改任何 Claude 設定。

## 大廳場景

- **後牆布告欄** — 進行中的委託單(冒險中/休息中的 session),點擊直接開該冒險者細節
- **右側櫃檯小姐** — 播報今日戰報摘要,點擊開完整戰報面板
- **地板站台** — 每個公會一台,含公會等級 + 經驗條 + 旗下冒險者小人

## 戰報面板(成長系統)

點櫃檯小姐開啟,一眼看進度:

- **今日 / 本週** 委託數、行動數、token 消耗、出勤公會
- **今日最活躍公會** 排行榜
- **活躍熱力圖** — GitHub 風格,近 12 週每天的行動量
- **成就徽章** — 連續冒險、公會網絡、百萬委託、技能精通…(未解鎖顯示進度)

## 啟動

**後端**(port 8848):
```
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8848 --reload
```

**前端**(port 5173,dev 已 proxy /api → 8848):
```
cd web
npm install
npm run dev
```

開 http://localhost:5173

## 零 token 即時觀測(選配,需掛 hook)

大廳頂端「📡 現在在做什麼」即時流,靠一個 **PostToolUse hook** 把每個工具呼叫寫成一行白話
紀錄(`~/.claude/guild-activity.jsonl`),面板讀它顯示。**完全不經過模型 → 不花任何 token**。

安裝:在 `~/.claude/settings.json`(全域,對所有資料夾生效)加入:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "python \"D:\\projects\\guild-hall\\hooks\\activity_logger.py\"" }
        ]
      }
    ]
  }
}
```

(若已有其他 hooks,合併到同一個 `hooks` 物件下即可。)裝好後,任何資料夾的 Claude 每做一個
動作,大廳就即時看得到,零 token。hook 腳本:`hooks/activity_logger.py`(必以 UTF-8 讀 stdin)。

## 櫃台回報 → 深度合成(A4+)

冒險者回報(`POST /api/reports` 或 `公會報告` skill)入列後,櫃台單一 worker 兩段式處理:

1. **存檔(保底)** — 寫進 `wiki/公會回報/<公會>.md` + 前插 `wiki/log.md`,永遠成功。
2. **深度合成(加值)** — spawn headless Claude(haiku、只給 Read/Glob/Grep/Edit/Write、
   max-turns 15、逾時 300s),依 llm-wiki-manager 準則把回報綜合進對應 Wiki **主題頁**
   並補 `index.md` 連結。失敗不影響存檔。

環境變數:`GUILD_SYNTHESIS=0` 關閉合成、`GUILD_SYNTH_MODEL` 換模型、
`GUILD_SYNTH_TIMEOUT` 調逾時、`GUILD_GIT_COMMIT=1` 開啟 wiki git 提交。

## 派遣系統(委託 → headless 冒險者執行)

布告欄的委託可以「🚀 派遣」給一位 headless Claude 冒險者,**回合制**執行:

- 每回合冒險者實際動手推進,結束時回報 `STATUS: WORKING/DONE/BLOCKED`,面板顯示進度時間線
- **回合之間**櫃台會注入你最新的目標(✏️ 調整)與中途指示(🗣 steer)→ **中途調整任務方向**
- `DONE` 時自動把 `REPORT` 送進櫃台佇列(→ wiki 歸檔 + 深度合成),委託自動標記完成 → 閉環
- `BLOCKED` / 回合用盡時暫停,下指示後可「再派遣」續同一位冒險者(同 session)
- **冒險者位階**:每張委託可選派遣模型 —— 🥉銅牌(haiku,快省)/🥈銀牌(sonnet)/
  🥇金牌(opus,最強);未選則跟隨 `GUILD_DISPATCH_MODEL` → CLI 預設
- 護欄:一次一個派遣、回合上限(`GUILD_DISPATCH_ROUNDS`,預設 6)、單回合逾時
  (`GUILD_DISPATCH_TIMEOUT`,預設 600s)、工具白名單(`GUILD_DISPATCH_TOOLS`)、
  模型可換(`GUILD_DISPATCH_MODEL`)

## 訓練所(進化 / 提煉 skill,提案制)

招牌上的「🎓 訓練所」面板,或冒險者抽屜的「送訓練所」:

1. **單人訓練** — headless Claude(唯讀:Read/Glob/Grep)分析該冒險者解決過的任務,
   從四路線(memory / skill / tool / script)擇一產出**進化提案**(含完整 artifact 內容)
2. **⚗️ 全公會提煉** — 掃近期全部完成回報,依 llm-wiki-manager 的 Prune 準則
   「去雜訊、提取通則」,把跨任務反覆出現的模式凝成一項能力提案
3. **批准才落地** — 提案在面板審核;✅ 批准後後端才把 skill/memory/script 寫進
   白名單路徑(`~/.claude`、`D:\projects`、`D:\myInfo`),既有檔案先備份 `.bak`,
   memory 路線自動補 `MEMORY.md` 索引,並登記進化(🎓 習得能力)

環境變數:`GUILD_TRAIN_MODEL`(預設 sonnet)、`GUILD_TRAIN_TIMEOUT`(預設 300s)。

## API

- `GET /api/guilds` — 所有公會 + 冒險者摘要(含 level / total_tokens)
- `GET /api/adventurers/{sessionId}` — 單一冒險者細節 + 冒險日誌
- `GET /api/report` — 戰報:今日/本週、熱力圖、成就、布告欄任務
- `GET /api/activity` — 即時動態(讀 hook 寫的 guild-activity.jsonl,零 token 觀測)
- `GET /api/recall/{report,ask,continue}` — 召回冒險者(用 session id 復活原 Claude)
- `POST /api/commissions/{id}/dispatch|steer|stop` + `GET .../events` — 派遣系統
- `PATCH /api/commissions/{id}` — 中途調整委託目標
- `POST /api/training/propose|distill`、`GET /api/proposals`、
  `POST /api/proposals/{id}/approve|reject` — 訓練所自動化
- `GET /api/health`

## 後續里程碑(未實作)

- MVP3 櫃台小姐「驗收」:派遣完成後自動 review + 不合格退回重做
- 派遣佇列:多張委託排隊自動依序派遣(目前一次一張,手動觸發)
- 體驗打磨:升級特效、日夜氛圍、排行榜、音效
