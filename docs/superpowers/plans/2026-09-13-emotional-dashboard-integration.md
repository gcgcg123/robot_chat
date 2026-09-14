# Emotional Dashboard Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `LQQWU/emotional_chat` 中可重用的情感陪伴資訊架構與視覺元素，整合到本專案的後台 Dashboard；ESP 負責對話輸入，後台只展示使用者情緒、對話分析、裝置連線狀態與系統健康度。

**Architecture:** 保留 `xiaozhi-esp32-server` 作為上游通訊基礎，保留目前 FastAPI dialogue service 作為資料 API。新增 dashboard read-model 與 device heartbeat/event 層，前端採用單頁 Dashboard；不移植文字聊天輸入框、不讓瀏覽器直接呼叫 LLM、不把 API key 暴露給前端。

**Tech Stack:** FastAPI、SQLite（開發環境）、原生 HTML/CSS/JavaScript、Chart.js（固定版本並自託管或鎖定 CDN integrity）、pytest、ESP 到貨前的 simulator heartbeat。

**Spec:** `C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/outputs/情感陪伴機器人_架構與實作規劃.html`、`C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/outputs/情感陪伴機器人_DeepSeek_API_更新計劃.html`，以及使用者提供的 `LQQWU/emotional_chat` GitHub URL。

## Global Constraints

- 不把 `LQQWU/emotional_chat` 的文字對話框、輸入框或瀏覽器端 LLM 呼叫移植到本專案。
- ESP 尚未到貨；所有裝置狀態先由 simulator heartbeat 驗證，不能宣稱已完成真機 MQTT/UDP 測試。
- DeepSeek credentials 只能由後端環境變數取得；前端、HTML、Git、Taskboard 不得出現密鑰。
- 上游 `xiaozhi-esp32-server` 固定在 commit `6afc54a17def47578a4b3efc4680873689d3168b`，不直接修改上游檔案。
- Dashboard 顯示 synthetic/demo data 時必須有明確標記，不能使用真實敏感照護資料。
- GitHub URL 目前從本環境回應 HTTP 404；在取得可讀取的公開 URL 或使用者提供存取權前，不執行 clone、移植或合併。

---

### Task 1: 確認參考專案可讀取範圍

**Files:**
- Create: `docs/integration/emotional_chat-inventory.md`
- Test: `tests/integration/test_reference_inventory.py`

**Interfaces:**
- Produces: a checked inventory of reference pages/components/assets and their licenses.

- [ ] **Step 1: Record repository access result**

記錄 URL、檢查日期、HTTP/clone 結果；目前結果固定記為 GitHub `404 Not Found`，不得假設其內容或授權。

- [ ] **Step 2: When access is provided, inventory only reusable UI**

逐項記錄頁面名稱、元件名稱、依賴、授權與移植決定。明確標記「移植」或「排除」；文字聊天 composer、輸入框、附件上傳與瀏覽器 LLM 呼叫一律排除。

- [ ] **Step 3: Add an inventory validation test**

```python
from pathlib import Path

def test_inventory_has_required_exclusions():
    text = Path("docs/integration/emotional_chat-inventory.md").read_text(encoding="utf-8")
    assert "文字聊天 composer" in text
    assert "不得移植" in text or "排除" in text
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/integration/test_reference_inventory.py -q`
Expected: PASS once the inventory document is present.

---

### Task 2: 建立 Dashboard read-model 與裝置資料表

**Files:**
- Modify: `services/dialogue/app.py`
- Create: `services/dashboard/read_model.py`
- Create: `tests/dashboard/test_read_model.py`

**Interfaces:**
- Produces: SQLite tables `users`, `devices`, `device_events`, `emotion_events`, `conversation_analysis`.
- Produces: `GET /api/dashboard/summary`, `GET /api/devices`, `POST /api/device/heartbeat`.

- [ ] **Step 1: Write failing schema/API tests**

測試 heartbeat 建立裝置、重複 heartbeat 更新 `last_seen`、超過 60 秒回報 `offline`，以及 summary 回傳 `online_devices`, `offline_devices`, `emotion_counts`, `conversation_count`。

- [ ] **Step 2: Implement schema and deterministic status rule**

使用 UTC epoch seconds；`online` 定義為 `now - last_seen <= 60`，`stale` 為 60–300 秒，`offline` 為大於 300 秒。所有資料庫查詢使用參數化 SQL。

- [ ] **Step 3: Run API tests**

Run: `pytest tests/dashboard/test_read_model.py -q`
Expected: PASS with no network dependency.

---

### Task 3: 裝置與情緒事件管線

**Files:**
- Create: `services/device_gateway/events.py`
- Modify: `services/dialogue/app.py`
- Modify: `simulator/replay_chat.py`
- Create: `simulator/heartbeat.py`
- Create: `tests/device_gateway/test_events.py`

**Interfaces:**
- Consumes: `POST /api/chat` conversation result and device heartbeat payload `{device_id, firmware_version, ip_hint, capabilities}`.
- Produces: normalized event records and dashboard status data.

- [ ] **Step 1: Write failing event tests**

覆蓋未知 device_id、缺失 heartbeat timestamp、重複事件 idempotency，以及 `/api/chat` 將 emotion 寫入 `emotion_events`。

- [ ] **Step 2: Implement event normalization**

所有事件包含 `event_id`, `device_id`, `event_type`, `occurred_at`, `payload_json`；拒絕超過大小上限的 payload；禁止把 API key 或 Authorization header 寫入 payload。

- [ ] **Step 3: Extend simulator**

`simulator/heartbeat.py --device sim-device --interval 10` 每 10 秒呼叫 heartbeat；`replay_chat.py` 接受 `--device` 並先送 heartbeat，再送文字訊息。

- [ ] **Step 4: Run event tests and simulator smoke test**

Run: `pytest tests/device_gateway/test_events.py -q`，再以本機服務執行一次 heartbeat + chat replay。

---

### Task 4: Dashboard 介面改造（保留後台定位）

**Files:**
- Modify: `services/dashboard/index.html`
- Create: `services/dashboard/dashboard.css`
- Create: `services/dashboard/dashboard.js`
- Create: `tests/dashboard/test_dashboard_assets.py`

**Interfaces:**
- Consumes: `/api/dashboard/summary`, `/api/devices`, `/api/conversations`。
- Produces: cards for emotion overview, device health, conversation analytics, latency and latest activity.

- [ ] **Step 1: Write asset/API contract test**

檢查頁面存在四個區塊 id：`emotion-overview`, `device-health`, `conversation-analytics`, `recent-activity`；檢查頁面沒有 `<textarea>`、`contenteditable` 或文字聊天送出按鈕。

- [ ] **Step 2: Implement visual information architecture**

採用參考圖的側邊導覽、卡片、柔和色彩與狀態 pill，但將中央區域改成後台統計：今日情緒分布、裝置 online/stale/offline、對話次數與平均延遲、最近事件。不要放任何對話輸入框。

- [ ] **Step 3: Implement polling and empty states**

每 5 秒刷新資料；API 不可用時顯示「服務連線中斷」；沒有資料時顯示「等待 ESP 或 simulator 上報」，並標示 demo/synthetic data。

- [ ] **Step 4: Run asset test and browser smoke test**

Run: `pytest tests/dashboard/test_dashboard_assets.py -q`；瀏覽器開啟 `/dashboard`，確認四區塊、空狀態與 simulator 資料均可見。

---

### Task 5: 安全、可觀測性與後續 MQTT/UDP 接口

**Files:**
- Modify: `.env.example`
- Create: `configs/dashboard.yaml.example`
- Create: `docs/integration/ESP_ONBOARDING.md`
- Create: `tests/security/test_dashboard_security.py`

**Interfaces:**
- Produces: configuration contract for future MQTT gateway and ESP onboarding.

- [ ] **Step 1: Write security tests**

確認 dashboard response 不含 `DEEPSEEK_API_KEY`、Authorization header、完整 IP/音訊內容；確認 CORS 預設只允許 localhost。

- [ ] **Step 2: Add configuration and retention settings**

定義 `DEVICE_ONLINE_TTL_SECONDS=60`、`DEVICE_OFFLINE_TTL_SECONDS=300`、`CONVERSATION_RETENTION_DAYS=30`、`MQTT_GATEWAY_URL`；所有密鑰只由環境變數注入。

- [ ] **Step 3: Document ESP onboarding**

記錄硬體到貨後的順序：同一 LAN → 啟動 xiaozhi server → 啟動 MQTT gateway → OTA 指向 laptop LAN IP → heartbeat → MQTT/UDP 音訊 → 真機驗證；明確禁止使用 `localhost`。

- [ ] **Step 4: Run the complete test suite**

Run: `pytest -q`。
Expected: all local simulator, API, asset and security tests pass; no ESP hardware claim is made.

---

## Spec coverage review

- 參考專案整合：Task 1（先解決目前 GitHub 404，再做受控移植）。
- 不需要文字對話框：Task 1、Task 4 明確排除。
- 使用者情緒狀態：Task 2、Task 4。
- 對話紀錄與分析：Task 2、Task 4。
- IoT 裝置是否連接：Task 2、Task 3、Task 4。
- ESP 尚未到貨：Task 3 simulator、Task 5 onboarding。
- DeepSeek/API key 安全：Global Constraints、Task 5。

