# 情感陪伴機器人（IoT_group5）

第一階段採 simulator-first：在 ESP 到貨前，使用 PC 麥克風（瀏覽器端編碼為 16-bit PCM WAV）、WAV 上傳和本機 Dashboard 驗證整條流程。上游 `upstream/xiaozhi-esp32-server` 保持原樣，後續再以 WebSocket/MQTT/UDP bridge 接入。

## ZIP 一鍵安裝

將 GitHub ZIP 解壓後，雙擊根目錄的 `一鍵安裝並啟動.bat`。它會建立虛擬環境、安裝 Python 依賴、補齊 Xiaozhi 參考程式、下載 ASR 模型、建立 simulator token，首次執行時以隱藏輸入保存 DeepSeek key 與 Dashboard 密碼，最後啟動 Dashboard。需要 Python 3.10+、Git 和可連網環境；模型約 1.6 GB，下載只需一次。

Docker 使用者可執行 `powershell -ExecutionPolicy Bypass -File scripts/bootstrap-project.ps1 -Docker`。第一次會建立 `.env.docker` 範本，填入本機管理員密碼及（可選）DeepSeek key 後再次執行；模型目錄需先按 `scripts/download-model.ps1` 下載，容器會把資料寫入 Docker volume。

注意：`models/asr/whisper-large-v3-turbo` 是 Hugging Face Transformers checkpoint；`faster-whisper` 需要 CTranslate2 格式。因此 ASR 端點預設使用 `models/asr/whisper-large-v3-turbo-ct2`，完成轉換或下載預轉換模型後才會啟用。

## 一鍵啟動（Windows）

第一次雙擊 `首次設定.bat`，安全輸入 DeepSeek key。之後平常只需要雙擊：

```text
一鍵啟動.bat
```

修改程式時使用 `開發模式.bat`，結束時使用 `一鍵停止.bat`。詳細說明見 `docs/STARTUP_GUIDE.md`。

啟動器會驗證程序的 PID、建立時間、執行檔和命令列 ownership，避免誤停止其他專案的程序。

## 手動啟動備援

```powershell
cd C:\Users\gcgcg\OneDrive\Desktop\IoT_group5\project_place
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 在目前 shell 內設定密鑰，不要把密鑰寫入 .env 或 Git：
$env:DEEPSEEK_API_KEY = '<從安全密鑰管理器注入>'
uvicorn services.dialogue.app:app --host 127.0.0.1 --port 8080
```

開啟 `http://127.0.0.1:8080/dashboard`。未設定密鑰時，服務仍會使用可測試的 fallback 回覆；設定後才呼叫 DeepSeek OpenAI-compatible endpoint。模型 ID 由環境變數 `DEEPSEEK_MODEL` 控制，預設 `deepseek-chat`，不要把未確認的 v4 名稱硬編碼。

## 目前端點

- `GET /health`：服務狀態
- `POST /api/chat`：文字對話、情緒標籤、SQLite 紀錄
- `POST /api/transcribe`：faster-whisper Turbo WAV/audio 轉寫
- `GET /api/conversations`：Dashboard 資料
- `GET /dashboard`：最小後台

Dashboard 目前已改為後台監控介面，不提供瀏覽器文字聊天框。可查看情緒分布、最近對話、裝置 heartbeat（online/stale/offline）與服務狀態。點擊「開啟 PC 模擬器」後，可建立使用者、完成三段聲紋登記，並用電腦麥克風進行對話。

裝置模擬器：

```powershell
python simulator\heartbeat.py --once --device-id verification-sim
python simulator\heartbeat.py --device-id verification-sim --interval 10
```

新後端 API：

- `POST /api/device/heartbeat`
- `GET /api/devices`
- `GET /api/dashboard/summary`
- `GET /api/users/{user_id}/emotion-trend`
- `GET/POST /api/users/{user_id}/memories`、`DELETE /api/users/{user_id}/memories/{memory_id}`：管理員限定的個人記憶操作。
- `POST /api/simulator/sessions`、`GET /simulator`：PC 麥克風／喇叭／320×240 顯示能力的模擬器基礎。

P1 核心模組也提供 `services/tts`、`services/voiceprint`、`services/memory`、`services/dialogue/pipeline.py` 與 `services/device_gateway/mqtt_udp.py`。其中 TTS deterministic provider、聲紋 provider 與 UDP peer 是可測試 contract，並不等同真人聲紋、中文聽感或 ESP 真機通過。

RAG 分成三個資料域：核准的 `shared` 共享知識、只在身份 accepted 且同意有效時可讀的 `user:<id>` 個人記憶，以及僅供 Dashboard 的 `analysis` 情緒／風險事件。小智的 LLM 工具呼叫方向與 RAGFlow `POST /api/v1/retrieval` 已以 optional adapter 保留；RAGFlow endpoint、dataset 與 token 必須自行配置，P1 預設不連外。

## 上游與 ESP 後續

上游版本固定在 commit `6afc54a17def47578a4b3efc4680873689d3168b`。官方 server 預設 WS 8000、OTA/HTTP 8003；MQTT gateway 另用 1883/TCP、8884/UDP、8007/API。ESP 到貨後才啟動 gateway、OTA 和真機聯調，並以區域網 IP 取代 localhost。
