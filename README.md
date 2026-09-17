# 情感陪伴機器人（IoT_group5）

## 老年人之友角色與個人長期記憶

DeepSeek 每次回覆前會先載入 `config/roles/elderly_companion.md`，再接收當前問題與已授權、已通過聲紋身份確認的該用戶長期記憶。記憶只作為不可信參考，不能覆寫角色安全規則；只有和當前話題相關時，模型才會自然回訪上一段對話。未通過聲紋確認或撤回 `memory` 授權時，私人記憶不會注入對話。可用 `IOT_ROLE_FILE` 指向另一份受信任的本地角色 Markdown。

第一階段採 simulator-first：在 ESP 到貨前，使用 PC 麥克風（瀏覽器端編碼為 16-bit PCM WAV）、WAV 上傳和本機 Dashboard 驗證整條流程。上游 `upstream/xiaozhi-esp32-server` 保持原樣，後續再以 WebSocket/MQTT/UDP bridge 接入。

## ZIP 一鍵安裝

將 GitHub ZIP 解壓後，雙擊根目錄的 `一鍵安裝並啟動.bat`。它會建立虛擬環境、安裝 Python 依賴、補齊 Xiaozhi 參考程式、下載 ASR 模型、建立 simulator token，首次執行時以隱藏輸入保存 DeepSeek key 與 Dashboard 密碼，最後啟動 Dashboard。需要 Python 3.10+、Git 和可連網環境；模型約 1.6 GB，下載只需一次。

Docker 使用者可執行 `powershell -ExecutionPolicy Bypass -File scripts/bootstrap-project.ps1 -Docker`。第一次會建立 `.env.docker` 範本，填入本機管理員密碼及（可選）DeepSeek key 後再次執行；模型目錄需先按 `scripts/download-model.ps1` 下載，容器會把資料寫入 Docker volume。

注意：`models/asr/whisper-large-v3-turbo` 是 Hugging Face Transformers checkpoint；`faster-whisper` 需要 CTranslate2 格式。因此 ASR 端點預設使用 `models/asr/whisper-large-v3-turbo-ct2`，完成轉換或下載預轉換模型後才會啟用。

## 一鍵啟動（Windows）

第一次雙擊 `一鍵安裝並啟動.bat`，依提示安全輸入 DeepSeek key 與管理員密碼；之後平常只需要雙擊：

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

三語登記現已提供逐步朗讀、試聽、重錄及確認保存；使用者資料頁可修改語言、停用及刪除。操作與驗證限制見 [三語登記指南](docs/ENROLLMENT_GUIDE.md)。正式模式使用 SpeechBrain ECAPA-TDNN 聲紋 embedding 自動識別說話者；測試模式才使用 deterministic baseline，PC 對話會先通過聲紋模板校驗。

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

P1 核心模組也提供 `services/tts`、`services/voiceprint`、`services/memory`、`services/dialogue/pipeline.py` 與 `services/device_gateway/mqtt_udp.py`。Windows TTS 已可輸出 SAPI PCM WAV；deterministic TTS、baseline 聲紋 provider 與 UDP peer 僅供離線測試，仍不等同 ESP 真機通過。

Windows 啟動時 `IOT_TTS_PROVIDER=windows` 會使用內建 SAPI 產生真正的 PCM WAV；非 Windows 或明確設定其他 provider 時仍可使用 deterministic silence。情緒與風險 Transformers 模型（`IOT_EMOTION_MODEL`、`IOT_RISK_MODEL`）及 sentence-transformers 記憶檢索（`IOT_EMBEDDING_MODEL` 加 `IOT_MEMORY_EMBEDDINGS=1`）都是可選且 lazy 載入，未配置時使用規則與 lexical fallback，不會在啟動或測試時下載模型。`/api/system/status` 會顯示目前 provider 狀態。

RAG 分成三個資料域：核准的 `shared` 共享知識、只在身份 accepted 且同意有效時可讀的 `user:<id>` 個人記憶，以及僅供 Dashboard 的 `analysis` 情緒／風險事件。小智的 LLM 工具呼叫方向與 RAGFlow `POST /api/v1/retrieval` 已以 optional adapter 保留；RAGFlow endpoint、dataset 與 token 必須自行配置，P1 預設不連外。

## 上游與 ESP 後續

上游版本固定在 commit `6afc54a17def47578a4b3efc4680873689d3168b`。官方 server 預設 WS 8000、OTA/HTTP 8003；MQTT gateway 另用 1883/TCP、8884/UDP、8007/API。ESP 到貨後才啟動 gateway、OTA 和真機聯調，並以區域網 IP 取代 localhost。
# 長期記憶與語音音色

在使用者詳情頁授予「記憶」權限後，系統會從明確的個人陳述或近期事件建立已批准的長期記憶，例如「最近工作不順利」。下一輪只在話題相關時把最多一條記憶交給 DeepSeek，提示它自然回訪上次話題；撤回權限後私人記憶不再注入對話。

模擬對話頁可選擇系統預設、女聲或男聲。Windows SAPI 不會自帶林志玲或懶洋洋等藝人角色音色；要使用相近音色，需先安裝合法的 SAPI 語音包，再在 `.env` 的 `IOT_TTS_VOICE_FEMALE` 或 `IOT_TTS_VOICE_MALE` 填入系統語音名稱。未安裝時會安全回退到系統預設或瀏覽器語音。
