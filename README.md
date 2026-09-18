# 情感陪伴機器人（IoT_group5）

第一階段採 simulator-first：在 ESP 到貨前，使用 PC 麥克風（瀏覽器端編碼為 16-bit PCM WAV）、WAV 上傳和本機 Dashboard 驗證整條流程。上游 `upstream/xiaozhi-esp32-server` 保持原樣，後續再以 WebSocket/MQTT/UDP bridge 接入。

## ZIP 一鍵安裝

將 GitHub ZIP 解壓後，雙擊根目錄的 `一鍵安裝並啟動.bat`。它會安裝 Python 依賴、補齊 Xiaozhi 參考程式（**可選**，抓不到不會中斷安裝——`services/`、`simulator/`、`tests/` 都不 import 它）、下載 ASR 模型與長期記憶用的嵌入模型、由 `.env.example` 建立 `.env`、建立 simulator token，首次執行時以隱藏輸入保存 DeepSeek key 與 Dashboard 密碼，最後啟動 Dashboard。需要 Python 3.10+、Git 和可連網環境；模型下載只需一次。

預設下載的是 **SenseVoice-Small（約 228 MB）**，而不是原本 1.5 GB 的 Whisper：它在 CPU 上約 0.5 秒轉寫一句話（Whisper large-v3-turbo 約 35 秒），而且原生支援粵語。另外一定會下載 **BGE-small-zh-v1.5 嵌入模型（約 90 MB）**——它不是 ASR 後端而是長期記憶的檢索依據。要改用 Whisper 基線：

```powershell
.\scripts\bootstrap-project.ps1 -AsrModel whisper     # 約 1.5 GB
.\scripts\download-model.ps1 -Model both              # 兩個 ASR 後端都下
.\scripts\download-model.ps1 -Model embedding         # 單獨補下嵌入模型
```

下載腳本會優先用專案解譯器旁的 `hf` / `huggingface-cli`，找不到就退回 `huggingface_hub` Python API；連線受限時可加 `-Endpoint https://hf-mirror.com`。

Docker 使用者可執行 `powershell -ExecutionPolicy Bypass -File scripts/bootstrap-project.ps1 -Docker`。第一次會建立 `.env.docker` 範本（由 `.env.docker.example` 複製），填入本機管理員密碼及（可選）DeepSeek key 後再次執行；模型目錄需先按 `scripts/download-model.ps1` 下載（**含 `-Model embedding` 的嵌入模型**），容器會把資料寫入 Docker volume，並以唯讀方式掛載 `./models`。

ASR 後端由 `configs/launcher.json` 的 `asr_provider` 決定（`sensevoice` 或 `whisper`），啟動器會把它寫成 `ASR_PROVIDER` 環境變數——所以不依賴未被版本控制的 `.env`。模型清單、來源與 SHA-256 見 `models/manifest.json`。

## 執行資料與設定檔

**資料放哪裡**：執行資料（SQLite 資料庫、simulator token、備份）預設在**專案目錄下的 `IoTGroup5\`**，不寫入 C 槽的 `%LOCALAPPDATA%`——整個專案資料夾複製走就帶著自己的資料。解析順序是 `IOT_DATA_DIR` → `DATABASE_PATH` → 專案預設 `.\IoTGroup5\`；`.env` 裡的相對路徑一律以**專案根目錄**為基準換算，所以從哪個工作目錄啟動都會開到同一份資料庫。要換位置就覆寫 `IOT_DATA_DIR`。

**設定檔哪個才算數**：

| 檔案 | 角色 |
|---|---|
| `.env` | **實際生效的設定**（未納入 Git）。改設定改這個 |
| `.env.example` | 安裝範本。第一次啟動時複製成 `.env`；改範本對已安裝的機器沒有作用 |
| `.env.docker.example` | Docker 範本，複製成 `.env.docker`（容器路徑與主機不同） |

改完 `.env` 要重啟服務（`一鍵停止.bat` → `一鍵啟動.bat`）才生效。**DeepSeek key 與 Dashboard 密碼不在 `.env`**：Windows 上以 DPAPI 加密存在 `data\secrets\`，由啟動器解密後注入環境變數（Docker 沒有 DPAPI，所以 `.env.docker` 才需要寫入明文密碼）。長期記憶的各種門檻見 [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md) 的環境變數表。

## 一鍵啟動（Windows）

第一次雙擊 `一鍵安裝並啟動.bat`，依提示安全輸入 DeepSeek key 與管理員密碼；之後平常只需要雙擊：

```text
一鍵啟動.bat
```

修改程式時使用 `開發模式.bat`，結束時使用 `一鍵停止.bat`。詳細說明見 `docs/STARTUP_GUIDE.md`。

啟動器會驗證程序的 PID、建立時間、執行檔和命令列 ownership，避免誤停止其他專案的程序。

## 手動啟動備援

```powershell
cd <你的專案目錄>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 在目前 shell 內設定密鑰，不要把密鑰寫入 .env 或 Git：
$env:DEEPSEEK_API_KEY = '<從安全密鑰管理器注入>'
.\scripts\download-model.ps1             # ASR：SenseVoice-Small（約 228 MB）
.\scripts\download-model.ps1 -Model embedding   # 長期記憶的嵌入模型（約 90 MB）
uvicorn services.dialogue.app:app --host 127.0.0.1 --port 8080
```

少了嵌入模型服務照樣啟動，但長期記憶會退回字面比對，中文改述的召回明顯變差。

啟動器會依序尋找解譯器：`IOT_PYTHON` → `configs/launcher.json` 的 `python` → 專案 `.venv` → PATH 上的 `python`。若你用的不是專案 `.venv`，把它的路徑填進 `configs/launcher.json` 的 `python` 即可。

開啟 `http://127.0.0.1:8080/dashboard`。未設定密鑰時，服務仍會使用可測試的 fallback 回覆；設定後才呼叫 DeepSeek OpenAI-compatible endpoint。模型 ID 由環境變數 `DEEPSEEK_MODEL` 控制，預設 `deepseek-chat`，不要把未確認的 v4 名稱硬編碼。

## 目前端點

三語登記現已提供逐步朗讀、試聽、重錄及確認保存；使用者資料頁可修改語言、停用及刪除。操作與驗證限制見 [三語登記指南](docs/ENROLLMENT_GUIDE.md)。目前聲紋仍是示範模型，PC 以所選使用者模擬對話歸屬。

- `GET /health`：服務狀態（含 `asr_backend` 與使用的模型路徑）
- `POST /api/chat`：文字對話、情緒標籤、SQLite 紀錄
- `POST /api/transcribe`：WAV/audio 轉寫（後端由 `ASR_PROVIDER` 決定：SenseVoice 或 faster-whisper）
- `GET /api/conversations`：Dashboard 資料
- `GET /dashboard`：最小後台

Dashboard 目前已改為後台監控介面，不提供瀏覽器文字聊天框。可查看情緒分布、最近對話、裝置 heartbeat（online/stale/offline）與服務狀態。使用者資料頁多了「長期記憶」區塊：可看到每則記憶的層級（槽位／熱／冷）、即時分數、命中次數與檢索句，並能手動新增、升降層級、歸零衰減或刪除。點擊「開啟 PC 模擬器」後，可建立使用者、完成三段聲紋登記，並用電腦麥克風進行對話。

長期記憶是**跟著帳號**的：機器人聊完一輪會自動判斷哪句話值得記住並寫入（使用者親口說的才算已確認），下一輪先查 32 個槽位、再查熱記憶、最後才翻冷記憶；常用的事分數會上升並升進槽位，久未提及的分數會隨時間衰減、跌破門檻就變成偽刪除的冷記憶，被重新問起時再復活。

三個容易誤解的地方：

- **「記住」與「用得到」是兩件事**。模型提出的記憶若沒有使用者原話的逐字引用，只會以 `approved=0` 存下，用途是待確認——它永遠不會進提示詞，直到管理員在面板確認、或使用者自己再說一次。
- **模型不一定重用同一個 key**。同一件「在學吉他」的事實際被存成 `hobby`／`guitar`／`hobby_guitar`／`instrument` 四種 key，所以寫入時除了比對 key 與文字，還會比對 **probe 問句的向量**，近似重複就合併進既有那一列而不是新增（門檻 0.9，是量出來的：狗／貓這種「一字之差但不同事」實測 0.826，所以門檻不能訂更低）。
- **串級只在「該層真的回答了這個問題」時才停**。基本資料（名字等）是跟著每一層一起注入的配角，不是「這題答完了」的訊號——否則使用者一旦報過名字，其他記憶就全部檢索不到。

詳細設計、門檻依據與實測數字見 [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md)。多人若共用同一個 `user_id`（例如都不帶 `user_id` 而落到預設的 `sim-user`）就會共用記憶。

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
- `GET/POST /api/users/{user_id}/memories`、`PATCH/DELETE /api/users/{user_id}/memories/{memory_id}`：管理員限定的個人記憶操作（`GET` 回傳層級、分數、命中次數、門檻與嵌入模型是否可用）。
- `POST /api/simulator/sessions`、`GET /simulator`：PC 麥克風／喇叭／320×240 顯示能力的模擬器基礎。

P1 核心模組也提供 `services/tts`、`services/voiceprint`、`services/memory`、`services/dialogue/pipeline.py` 與 `services/device_gateway/mqtt_udp.py`。其中 TTS deterministic provider、聲紋 provider 與 UDP peer 是可測試 contract，並不等同真人聲紋、中文聽感或 ESP 真機通過。

RAG 分成三個資料域：核准的 `shared` 共享知識、只在身份 accepted 且同意有效時可讀的 `user:<id>` 個人記憶，以及僅供 Dashboard 的 `analysis` 情緒／風險事件。小智的 LLM 工具呼叫方向與 RAGFlow `POST /api/v1/retrieval` 已以 optional adapter 保留；RAGFlow endpoint、dataset 與 token 必須自行配置，P1 預設不連外。

## 測試與驗收

```powershell
pip install -r requirements-dev.txt      # 只多了 pytest，執行期相依仍在 requirements.txt
python -m pytest tests -q                # 173 passed
python -m compileall -q services simulator scripts
```

需要嵌入模型的那幾個測試在模型不存在時會自動 skip，所以沒下載模型也能跑完整套。

> **倉庫不含測試**：`tests\` 與驗收報告都列在 `.gitignore`，所以**從 GitHub 下載的副本沒有這些檔案**，在那裡執行 `python -m pytest tests -q` 會得到 `file or directory not found: tests`（exit code 4）。上面的數字是在保留測試的開發工作區跑出來的。服務本身不依賴 `tests\`，下載後照樣能安裝與啟動。

想驗收長期記憶的實際行為，最直接的方式是跑服務並用 Dashboard／模擬器對話：說一件事 → 隔一輪問它 → 使用資料頁的「長期記憶」區塊看層級、分數與命中次數的變化。設計、門檻依據與實測數字見 [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md)，PC 階段的人工步驟見 [PC 驗收](docs/PC_ACCEPTANCE.md)。

## 文件索引

| 文件 | 內容 |
|---|---|
| [實作狀態](docs/IMPLEMENTATION_STATUS.md) | 目前實際完成什麼、已驗證什麼、還有哪些是佔位 |
| [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md) | 三層記憶、分數衰減、probe 檢索、合併門檻與環境變數 |
| [啟動指南](docs/STARTUP_GUIDE.md) | 一鍵／手動／Docker 啟動、日誌與疑難排解 |
| [三語登記指南](docs/ENROLLMENT_GUIDE.md) | 聲紋三段登記的操作與驗證限制 |
| [PC 驗收](docs/PC_ACCEPTANCE.md) | PC 階段的人工驗收步驟 |
| [完善步驟](docs/COMPLETION_PLAN.md) | 佔位模組要改成什麼、怎麼改、怎麼驗收 |
| [RAG 設計](docs/RAG_DESIGN.md) | 資料域、權限順序與不可信參考的邊界 |
| [Git 流程](docs/GIT_WORKFLOW.md) | 分支、提交與上游 pin 的處理 |

`docs/superpowers/` 是當時的規劃紀錄，不是現況；日期化的歷史快照見 `docs/PROJECT_PROGRESS_2026-09-14.html`。

## 上游與 ESP 後續

上游版本固定在 commit `6afc54a17def47578a4b3efc4680873689d3168b`。官方 server 預設 WS 8000、OTA/HTTP 8003；MQTT gateway 另用 1883/TCP、8884/UDP、8007/API。ESP 到貨後才啟動 gateway、OTA 和真機聯調，並以區域網 IP 取代 localhost。
