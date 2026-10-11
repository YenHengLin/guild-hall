# 冒險者公會大廳 (Guild Hall)

把 Claude Code 包裝成一座 RPG **冒險者公會**:你在布告欄貼「委託」,公會派 headless Claude
冒險者去執行;執行過程即時可見、可中途調整;完成後櫃台驗收、歸檔、整理成可讀的成果卡,
並累積成任務記憶 / 知識庫。

- **委託** = 一個你要完成的任務(標題、目標、附圖、指定資料夾、模型位階、呈現方式)
- **冒險者** = 一個 Claude Code session(派遣產生,或掃描 `~/.claude/projects` 既有 session)
- **公會** = 資料夾公會(session 所在專案)+ **領域公會**(以「做的事」分類,如「金融公會」)
- **櫃台** = 收回報、驗收、歸檔、可搜尋的回報紀錄

> 需求:Windows / macOS / Linux、Python 3.10+、Node 18+、已登入的 [`claude` CLI](https://docs.claude.com/en/docs/claude-code)。
> 主要在 Windows 上開發與使用(附 `start.bat` / `stop.bat`)。

---

## 快速開始

```bash
git clone https://github.com/YenHengLin/guild-hall.git
cd guild-hall
pip install -r requirements.txt
cp config.example.yaml config.yaml   # 依需要修改(可省略,全用預設值)
```

**Windows**:雙擊 `start.bat`(首次會自動 `npm install`),會開兩個最小化視窗並打開瀏覽器;
`stop.bat` 關閉。

**手動啟動**:

```bash
# 後端(port 8848)
python -m uvicorn app.main:app --port 8848

# 前端(port 5173,dev 已 proxy /api、/uploads → 8848)
cd web && npm install && npm run dev
```

開 <http://localhost:5173>。

> ⚠️ 後端**不要**加 `--reload`:派遣的冒險者常會改到本專案自己的 `.py`,`--reload`
> 會把正在跑派遣的後端重啟(自己把自己踢掉)。改完後端程式碼請手動重啟。
> 改了 DB schema 的版本更新後也需要重啟,啟動時會自動 migration。

---

## 設定(config.yaml)

所有可調參數集中在 `config.yaml`(不進 git,每台機器各一份),範本與說明見
[`config.example.yaml`](config.example.yaml)。

- 優先序:**環境變數 > config.yaml > 程式預設值**
- 每個 key 對應一個環境變數:`GUILD_` + 大寫,例如 `wiki_dir` ↔ `GUILD_WIKI_DIR`
- 路徑可用 `~` 開頭;Windows 路徑請用**單引號**(`'D:\myInfo\wiki'`)
- 換設定檔位置:環境變數 `GUILD_CONFIG=/path/to/config.yaml`

常用設定:

| key | 預設 | 說明 |
|---|---|---|
| `wiki_dir` | `""` | llm wiki 目錄;留空 = 不寫 wiki,回報只存在公會 DB |
| `dispatch_parallel` | `2` | 同時最多幾張派遣(同資料夾永遠只跑一張) |
| `dispatch_model` | `""` | 預設派遣模型;空 = CLI 預設 |
| `task_base` | `""` | 自建任務資料夾的基底 |
| `projects_dir` | `~/projects` | 訓練所 script 落地根目錄 |
| `memory_dir` | `~/.claude/memory` | 訓練所 memory 落地目錄 |
| `acceptance` | `true` | DONE 時是否跑櫃台驗收 |
| `tailscale` | `false` | 讓 tailnet 上的裝置連前端(見下方「遠端連線」) |
| `tailscale_hostname` | `""` | MagicDNS 主機名,啟動時印出連線網址 |

### 遠端連線(Tailscale)

預設前端只聽 `127.0.0.1`,其他電腦連不進來。想用手機 / 筆電透過 [Tailscale](https://tailscale.com)
連,在 `config.yaml` 設:

```yaml
tailscale: true
tailscale_hostname: 'my-pc.tailxxxx.ts.net'   # 可留空;`tailscale status` 查得到
```

重啟前端後,前端視窗會印出 `Tailscale: http://my-pc.tailxxxx.ts.net:5173`。

- 此時前端綁 `0.0.0.0`,但**只放行本機與 Tailscale 位址**(`100.64.0.0/10`、`fd7a:115c:a1e0::/48`);
  同網段、ZeroTier、公用網路來的連線一律 403。
- 後端(8848)永遠只聽 `127.0.0.1`,只經由前端 proxy 轉發。
- 連不進來時:Windows 防火牆要允許 node.exe(建議只對 Tailscale 網段放行)。
- ⚠️ API **沒有登入驗證**,而派遣的冒險者能執行指令 —— 只在你信任 tailnet 上所有裝置時開啟。

---

## 功能

### 📜 委託布告欄 + 派遣

- 貼委託:標題 / 目標 / 備註、貼上或選取**附圖**、工作資料夾、**冒險者位階**(模型)、
  **呈現方式**(決定結果卡皮膚)、🏷 **領域公會**、📁 **自建任務資料夾**、🧠 **任務記憶**。
  草稿點視窗外不會消失;可整張重新編輯後再派遣。
- **派遣 = 回合制**:每回合冒險者推進一段,回報 `STATUS: WORKING / DONE / BLOCKED`。
  回合之間可 ✏️ 改目標、🗣 下中途指示、⏹ 停止;冒險者用 `AskUserQuestion` 提問時,
  面板會渲染成可點選的按鈕。
- **即時時間線**:每個工具動作(讀檔、改檔、跑指令…)即時顯示。任務可 ⤢ 彈出成
  可拖曳、可拉寬的浮動視窗。
- **排隊**:可一次排多張,背景 worker 有空位就自動開跑。
- **並行派遣**:最多 `dispatch_parallel` 張同時跑;**同一資料夾同時只跑一張**
  (避免互改檔案)。要讓同資料夾的任務並行,勾「📁 自建任務資料夾」——
  冒險者會在 `<base>/_tasks/<領域>/<標題-id>/` 裡工作。
- **櫃台驗收**:冒險者宣稱 DONE 時,另起唯讀 claude 實地查驗;沒過自動退回重做,
  仍不過則轉人工。
- 已完成的委託依 **今天 / 本週 / 更早** 分區,舊的預設收合。

### 🧠 任務記憶(記憶特化)

- 每張委託結案時,成果 / 心得 / 改動檔案自動累積到**這張委託自己的任務記憶**,
  續派時注入首回合 prompt,冒險者接著做而不是重來。
- 勾選「記憶特化在任務內」(預設勾選)時,回報**不寫入 llm wiki**、不跑合成,
  並提示冒險者別去撈全域 wiki;回報仍可在櫃台搜尋。

### 🎨 職業結果卡

依冒險者職業 / 呈現方式,把結果渲染成專屬介面,而不是一大段文字:

| 皮膚 | 內容 |
|---|---|
| coding | VS Code 式檔案清單,點檔案看 before → after diff |
| teaching | 互動教學字卡(問答 + 3 選 1 小測驗 + 真實數據圖表) |
| ops | 跑過的指令、動到的服務、設定檔異動 |
| review | 查驗清單 + 裁決(pass / warn / fail) |
| ship | commit、推送目標、部署指令 |

### 🧝‍♀️ 櫃台側邊欄

- 右側可收合;收合時布告欄滿版。展開後**就地顯示**,可拖左緣加寬。
- **回報搜尋**(標題 / 公會 / 內容),點開看完整回報與結果卡。
- 幕後審核:**✍️ 退回櫃台重寫**(你的說明會被學成敘事偏好)、
  **🔧 派冒險者接續**(召回原 session 實際重做)。
- 「📊 完整戰報」:今日 / 本週統計、熱力圖、成本帳本、成就、學習迴路。

### 📚 回報歸檔 → wiki 深度合成(選用)

設定了 `wiki_dir` 才會啟用。櫃台單一 worker 序列化處理:

1. **存檔(保底)**:寫進 `<wiki_dir>/公會回報/<公會>.md`,並前插 `<wiki_dir>/log.md`
2. **深度合成(加值)**:headless Claude 把回報綜合進對應主題頁並補 `index.md` 連結;
   失敗不影響存檔

相關設定:`synthesis`、`synth_model`、`synth_timeout`、`git_commit`。

### 🎓 訓練所(提案制進化)

- **單人訓練**:分析某位冒險者解決過的任務,產出一項進化提案(skill / memory / tool / script)
- **⚗️ 全公會提煉**:從近期全部回報提取跨任務通則
- **批准才落地**:只會寫進白名單路徑(`~/.claude`、`memory_dir`、`projects_dir`、
  wiki 上層、`extra_write_roots`),既有檔案先備份 `.bak`

### 🌙 公會夜巡

NPC 依你的公會現況自主產生靈感,可升級成委託;標記為安全的可自動執行。

### 🔮 召回冒險者

用 session id 叫回原本的 Claude(`claude --resume`)問問題或接續工作——
重開機後也能找回「哪個冒險者在哪個資料夾做到哪」。

---

## 零 token 即時觀測(選配,需掛 hook)

大廳頂端「📡 現在在做什麼」,靠一個 **PostToolUse hook** 把每個工具呼叫寫成一行白話紀錄
(預設 `~/.claude/guild-activity.jsonl`,可用環境變數 `GUILD_ACTIVITY_LOG` 改)。
完全不經過模型,不花 token。

在 `~/.claude/settings.json` 加入(路徑換成你的 clone 位置):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "python \"<clone 路徑>/hooks/activity_logger.py\"" }
        ]
      }
    ]
  }
}
```

---

## 專案結構

```
app/                FastAPI 後端
  config.py         設定載入(環境變數 > config.yaml > 預設)
  dispatch.py       派遣系統(回合制、並行、驗收、結果卡、任務記憶)
  reception.py      櫃台 worker(回報歸檔 → wiki)
  synthesis.py      wiki 深度合成
  training.py       訓練所
  muse.py           公會夜巡
  scanner.py        掃描 ~/.claude/projects 的 session
  db.py             SQLite(guild.db,啟動時自動 migration)
  routers/          API 路由
web/                Vue 3 + Vite + Pinia 前端
hooks/              Claude Code hook(即時動態)
tests/              pytest
config.example.yaml 設定範本
start.bat / stop.bat Windows 啟動 / 關閉
```

## 開發

```bash
python -m pytest tests/ -q     # 後端測試
cd web && npm run build        # 前端建置檢查
```

## API 一覽

所有路由前綴 `/api`。

| 類別 | 路由 |
|---|---|
| 公會 / 冒險者 | `GET /guilds`、`GET /adventurers/{id}`、`POST /adventurers/{id}/hide\|unhide\|identity\|abilities` |
| 委託 | `GET/POST /commissions`、`PATCH/DELETE /commissions/{id}`、`POST /commissions/{id}/done\|reopen`、`GET /domains` |
| 派遣 | `POST /commissions/{id}/dispatch\|enqueue\|steer\|stop`、`GET /commissions/{id}/events` |
| 櫃台 | `GET/POST /reports`、`GET /reports/queue`、`POST /reports/polish`、`POST /reports/{id}/reject` |
| 學習書架 | `GET /lessons`、`POST /lessons/reclassify` |
| 戰報 / 成本 | `GET /report`、`GET /costs`、`GET /activity` |
| 召回 | `POST /recall/ask`、`POST /recall/continue`、`GET /recall/stream` |
| 訓練所 | `POST /training/propose\|distill`、`GET /proposals`、`POST /proposals/{id}/approve\|reject`、`GET/POST /evolutions` |
| 夜巡 | `GET /muse/ideas\|config\|status`、`PUT /muse/config`、`POST /muse/run\|auto-run`、`POST /muse/ideas/{id}/promote\|dismiss` |
| 上傳 | `POST /uploads`(base64 data URL → `/uploads/...`) |
