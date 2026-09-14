# 情感陪伴機器人後台 Dashboard 改造計畫（v2）

> 本文件只描述後續實作，不代表已開始修改程式。執行前需由使用者確認。

**目標**：以現有 FastAPI 服務為核心，將參考專案可借鑑的情感陪伴資訊架構轉化為 IoT 後台 Dashboard；ESP 負責對話輸入，網站負責狀態監控、情緒統計、紀錄分析與裝置健康度。

## 重新檢查結果

於 2026-09-13 重新讀取：

- `https://github.com/gcgcg123/emotional_chat.git`：可讀取，main HEAD `fc2d07f9711e4f79921bc81d448af8ff004d626a`
- 授權：MIT（repo `LICENSE`）
- 技術：React 18、styled-components、lucide-react、Framer Motion、FastAPI、SQLite/MySQL、ChromaDB、Redis（可選）
- 前端候選：`frontend/src/components/Sidebar.js`、`ContextRail.js`、`frontend/src/styles/sidebar.js`、`layout.js`、`index.css`
- 後端候選：`backend/routers/emotion_analysis.py`、`backend/services/emotion_trend_analyzer.py`、`backend/routers/performance.py`、`backend/database.py`

已完成 clone 供只讀檢查，位置：`work/reference_emotional_chat`。它的 README 顯示該專案本身是完整的文字聊天產品；因此只借鑑資料分析、情緒趨勢與 UI 元件，不直接嵌入整個 React 聊天應用。

## 不會移植的功能

- 中央文字聊天輸入框、textarea、送出按鈕
- 瀏覽器直接呼叫 DeepSeek 的程式碼
- 參考專案的帳號、對話資料或未確認授權的 assets

ESP 取得語音與使用者資料後，後端才接收標準化事件；瀏覽器只讀取後端 API。

## 預定資訊架構

1. 總覽：今日對話數、活躍使用者、平均回覆延遲、服務健康度。
2. 情緒分析：positive / neutral / negative 分布、每日趨勢、每位使用者最近情緒。
3. IoT 裝置：online、stale、offline、最後 heartbeat、IP hint、韌體版本、裝置能力。
4. 對話紀錄：時間、user/device id、轉錄文字、情緒、模型、延遲；敏感欄位預設遮罩。
5. 系統事件：ASR、DeepSeek、MQTT/UDP gateway、資料庫與錯誤事件。

視覺上可採用參考圖的側欄、圓角卡片、狀態 pill、柔和色彩與三欄資訊密度，但中央內容改為後台統計，不再呈現聊天工作區。

## 實作分階段

### Phase 0：參考專案元件與授權確認

**輸出**：`docs/integration/emotional_chat-inventory.md`

1. 固定參考 commit `fc2d07f9711e4f79921bc81d448af8ff004d626a`，避免 main 漂移。
2. 以 MIT license、React 18 與現有原生 Dashboard 的相容性審查候選元件。
3. 優先重新實作 Sidebar/ContextRail 的資訊層級與色彩，不直接拷貝依賴大量聊天狀態的 `App.js`、`ChatContainer.js`、`MultimodalInput.js`。
4. 後端只參考情緒趨勢輸出格式與安全風險欄位；不得把參考專案的 LLM、資料庫或使用者資料直接接入本專案。

### Phase 1：資料模型與 API

**修改/新增**：

- `services/dialogue/app.py`
- `services/dashboard/read_model.py`
- `data/emotional_robot.sqlite3`
- `tests/dashboard/test_read_model.py`

新增資料表：

- `users`
- `devices`
- `device_events`
- `emotion_events`
- `conversation_analysis`

新增 API：

- `GET /api/dashboard/summary`
- `GET /api/devices`
- `GET /api/users/{user_id}/emotion-trend`
- `GET /api/conversations`
- `POST /api/device/heartbeat`

裝置狀態規則：

- `online`：最近 60 秒內有 heartbeat
- `stale`：60 至 300 秒
- `offline`：超過 300 秒

### Phase 2：ESP 前置模擬器

**新增**：

- `simulator/heartbeat.py`
- `services/device_gateway/events.py`
- `tests/device_gateway/test_events.py`

在 ESP 到貨前，模擬器每 10 秒送出 heartbeat，並可搭配現有 `simulator/replay_chat.py` 產生對話與情緒資料。Dashboard 必須標示 `SIMULATOR / DEMO DATA`。

### Phase 3：Dashboard 重構

**修改/新增**：

- `services/dashboard/index.html`
- `services/dashboard/dashboard.css`
- `services/dashboard/dashboard.js`
- `tests/dashboard/test_dashboard_assets.py`

頁面區塊：

- `emotion-overview`
- `device-health`
- `conversation-analytics`
- `recent-activity`

前端每 5 秒只讀取後端 API；API 失敗時顯示連線中斷；無資料時顯示等待 ESP/simulator。頁面不得含 `<textarea>`、`contenteditable` 或聊天送出按鈕。

### Phase 4：安全與可觀測性

**新增/修改**：

- `.env.example`
- `configs/dashboard.yaml.example`
- `tests/security/test_dashboard_security.py`
- `docs/integration/ESP_ONBOARDING.md`

要求：

- DeepSeek key 只存在後端環境變數。
- Dashboard response 不返回 Authorization、API key 或原始音訊。
- 所有事件使用參數化 SQL與大小限制。
- 開發環境只允許 localhost；對外部署前再加登入、反向代理與 TLS。

### Phase 5：ESP 到貨後再整合

順序固定為：

1. ESP 與 laptop 同一個 LAN。
2. 啟動上游 WebSocket server（8000）與 OTA/HTTP（8003）。
3. 啟動 MQTT gateway（1883/TCP、8884/UDP、8007/API）。
4. OTA 使用 laptop LAN IP，不能使用 `localhost`。
5. 驗證 heartbeat、MQTT 指令、UDP 音訊、ASR、DeepSeek 回覆與 Dashboard 事件。

## 驗收條件

- Dashboard 沒有文字聊天框，但能顯示情緒、對話統計與裝置連線狀態。
- simulator heartbeat 可讓裝置由 online 變為 stale/offline。
- `/api/chat`、`/api/dashboard/summary`、`/api/devices` 有自動化測試。
- DeepSeek key 不出現在 HTML、JavaScript、SQLite payload、Git 或 Taskboard。
- ESP 真機功能只在硬體到貨並完成協定測試後宣稱完成。

## 目前狀態與待確認事項

參考 repo 已可讀取並完成初步檢查；目前沒有因 GitHub 存取造成的阻塞。下一個需要使用者確認的事項是：是否接受「只移植 Dashboard 視覺與分析概念，不移植文字聊天產品」的範圍。確認後才進入 Phase 1，並仍然不會修改上游目錄。
