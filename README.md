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

安裝時若 `data\RAG_data\` 裡有任何 `.md`，會一併匯入本地知識庫（見下方「本地知識庫」）；沒有語料就整段跳過，服務照常啟動。**語料不隨倉庫分發**，所以剛解壓的副本知識庫是空的。選用功能各自帶自己的依賴檔，不會被隱式安裝：

```powershell
.\scripts\bootstrap-project.ps1 -SkipKnowledge     # 這次不要匯入語料
.\scripts\bootstrap-project.ps1 -SkipModelDownload # 模型已下載過，別再抓
.\scripts\bootstrap-project.ps1 -WithVoiceprint    # 聲紋（+torch，約 730 MB）
.\scripts\bootstrap-project.ps1 -WithRerank        # 知識庫重排器（+torch/transformers 與 1.06 GB 權重）
```

Docker 使用者可執行 `powershell -ExecutionPolicy Bypass -File scripts/bootstrap-project.ps1 -Docker`。第一次會建立 `.env.docker` 範本（由 `.env.docker.example` 複製），填入本機管理員密碼及（可選）DeepSeek key 後再次執行；模型目錄需先按 `scripts/download-model.ps1` 下載（**含 `-Model embedding` 的嵌入模型**），容器會把資料寫入 Docker volume，並以唯讀方式掛載 `./models`。映像檔不會複製 `data\`，所以 Docker 模式的知識庫與剛 clone 的副本一樣是空的。

ASR 後端由 `configs/launcher.json` 的 `asr_provider` 決定（`sensevoice` 或 `whisper`），啟動器會把它寫成 `ASR_PROVIDER` 環境變數——所以不依賴未被版本控制的 `.env`。模型清單、來源與 SHA-256 見 `models/manifest.json`。

## 執行資料與設定檔

**資料放哪裡**：執行資料（SQLite 資料庫、simulator token、備份）預設在**專案目錄下的 `IoTGroup5\`**，不寫入 C 槽的 `%LOCALAPPDATA%`——整個專案資料夾複製走就帶著自己的資料。解析順序是 `IOT_DATA_DIR` → `DATABASE_PATH` → 專案預設 `.\IoTGroup5\`；`.env` 裡的相對路徑一律以**專案根目錄**為基準換算，所以從哪個工作目錄啟動都會開到同一份資料庫。要換位置就覆寫 `IOT_DATA_DIR`。

**設定檔哪個才算數**：

| 檔案 | 角色 |
|---|---|
| `.env` | **實際生效的設定**（未納入 Git）。改設定改這個 |
| `.env.example` | 安裝範本。第一次啟動時複製成 `.env`；改範本對已安裝的機器沒有作用 |
| `.env.docker.example` | Docker 範本，複製成 `.env.docker`（容器路徑與主機不同） |

改完 `.env` 要重啟服務（`一鍵停止.bat` → `一鍵啟動.bat`）才生效。**DeepSeek key 與 Dashboard 密碼不在 `.env`**：Windows 上以 DPAPI 加密存在 `data\secrets\`，由啟動器解密後注入環境變數（Docker 沒有 DPAPI，所以 `.env.docker` 才需要寫入明文密碼）。長期記憶的各種門檻見 [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md) 的環境變數表，知識庫的門檻見 `.env.example` 的 `IOT_KNOWLEDGE_*`。

資料庫 schema 目前是 **v9**，啟動時自動遷移（純新增，不破壞既有資料）：v7 知識 chunk 的 probe、v8 句子滑窗、v9 滾動摘要表 `conversation_summaries`。

## 一鍵啟動（Windows）

第一次雙擊 `一鍵安裝並啟動.bat`，依提示安全輸入 DeepSeek key 與管理員密碼；之後平常只需要雙擊：

```text
一鍵啟動.bat
```

安裝腳本不會把依賴裝進系統 Python：若沒有指定 `IOT_PYTHON`，它會建立專案 `.venv` 並把依賴裝進那裡（需要 Python 3.10 或更新版本）。要用既有的 conda／venv 環境，就把該環境的 `python.exe` 填進 `.env` 的 `IOT_PYTHON`（見下方「手動啟動備援」），那是明確指定，腳本不會再動 `.venv`。

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
python scripts\import-knowledge.py      # 可選：data\RAG_data\ 有語料時才需要
uvicorn services.dialogue.app:app --host 127.0.0.1 --port 8080
```

少了嵌入模型服務照樣啟動，但長期記憶會退回字面比對，中文改述的召回明顯變差。

啟動器會依序尋找解譯器：`IOT_PYTHON`（環境變數）→ `.env` 的 `IOT_PYTHON` → `configs/launcher.json` 的 `python` → 專案 `.venv` → PATH 上的 `python`。若你用的不是專案 `.venv`（例如某個 conda 環境），把它的 `python.exe` 填進 **`.env` 的 `IOT_PYTHON`**——`configs/launcher.json` 是版本控制檔案，單機路徑放那裡會被之後的整理清掉。啟動前會先確認該解譯器真的能跑這個專案，不能就指名它並說明怎麼改。

開啟 `http://127.0.0.1:8080/dashboard`。未設定密鑰時，服務仍會使用可測試的 fallback 回覆；設定後才呼叫 DeepSeek OpenAI-compatible endpoint。模型 ID 由 `DEEPSEEK_MODEL` 控制（`configs/launcher.json` 的 `deepseek_model` 會覆寫 `.env`，四份設定檔的值已統一為 `deepseek-flash`）。

> **端點可能改寫模型名**：實測這個 endpoint 對任何模型名都回 200 並回報 `served_model=deepseek-flash`（連不存在的名稱也一樣），所以請求的名稱在這裡只是形式。對話紀錄因此分開存兩個欄位——`model` 一律是你設定的名稱（成功與失敗一致，方便比對），`served_model` 是端點回報的實際模型，另有 `llm_status` 標示這輪是否降級（見下）。設定與實際一致時兩者會相同。

## 目前端點

三語登記現已提供逐步朗讀、試聽、重錄及確認保存；使用者資料頁可修改語言、停用及刪除。操作與驗證限制見 [三語登記指南](docs/ENROLLMENT_GUIDE.md)。聲紋辨識（ECAPA-TDNN，192 維、CPU 推論）需要選用依賴才會啟用：沒安裝時登記會回 503 並附上安裝指令，`/health` 會列出 `voiceprint_dependencies_missing`——不會退回只能測出三個聲學數字的替身 provider，因為那樣的登記等於沒有辨識能力。PC 端仍以所選使用者模擬對話歸屬。

- `GET /health`：服務狀態（含 `asr_backend`、使用的模型路徑、`knowledge_chunks`/`knowledge_mode`/`knowledge_floor_*`、`knowledge_reranker`/`knowledge_rerank_enabled` 與聲紋缺件診斷）
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

**短期記憶（2026-10-05 新增）**：上一輪之前說過的話真的進得了提示詞——最近 **10 輪**逐字重播（`IOT_DIALOGUE_HISTORY_TURNS`，0 可關閉），更早的輪次由**滾動摘要**壓成 3～5 句放在 system prompt（`IOT_DIALOGUE_SUMMARY`，約每 6 輪更新一次，那一次多一個 LLM 請求）。上限與摘要演算法刻意保證「不留下空隙」：任何一輪不是被重播就是被摘要，不會兩邊都沒有。沒有這層之前，第二輪問「你剛剛說了什麼」只會得到「我還沒開口」（實測）。細節見 [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md)。

**安全類發言不靠模型自願記住**：偵測到自傷／傷人意念（`自殺`、`跳樓`、`想死`、`殺了他`…）時，除了寫入 `risk_events` 供人工確認，還會**不經模型**直接落一則長期記憶（`risk:safety`／`risk:violence`，逐字引用原話所以 `approved=1`，`/health` 與 Dashboard 都看得到）。「我不想死」這種否定句不建記憶，但風險事件照記——漏掉一次披露比多一次誤報更貴。

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

RAG 分成三個資料域：核准的 `shared` 共享知識、只在身份 accepted 且同意有效時可讀的 `user:<id>` 個人記憶，以及僅供 Dashboard 的 `analysis` 情緒／風險事件。小智的 LLM 工具呼叫方向與 RAGFlow `POST /api/v1/retrieval` 已以 optional adapter 保留；RAGFlow endpoint、dataset 與 token 必須自行配置，P1 預設不連外。（這一段說的是**資料域邊界與外部 RAGFlow 的保留介面**；實際跑在服務裡的檢索是下一節的本地知識庫，兩者不衝突。）

## 本地知識庫（RAG）

長輩溝通手冊的節錄會依相關度注入提示詞，出處以 `citations` 回傳給前端顯示。**語料不隨倉庫分發**：`data\RAG_data\` 列在 `.gitignore`（三本書的 PDF 含掃描版／z-library 來源，處置理由見 [RAG 知識庫計畫](docs/RAG_KNOWLEDGE_PLAN.md) A9.7.7）。所以剛 clone／解壓的副本**知識庫是空的**：`citations` 會是空陣列、回答不會引用手冊，其餘功能（記憶、安全記憶、聲紋、對話）完全不受影響。

| 層 | 位置 | 內容 |
|---|---|---|
| 語料原件 | `data\RAG_data\`（**未納入 Git**） | 3 本書的 PDF 與 MD，含 `md\ocr\` 裡兩本掃描書的 OCR 結果 |
| 清洗檔與校準題 | `data\knowledge\`（**納入 Git**） | `profiles\`（怎麼切、哪些是純習題）、`calibration\`（每題期望頁碼＋全域負例）、`probes\` |
| 入庫後的資料 | `IoTGroup5\emotional_robot.sqlite3` | `knowledge_documents`／`knowledge_chunks`：3 份文件、341 段，含 512 維向量與句子滑窗 |

自己建一份知識庫：

```powershell
# 1. 掃描版 PDF 先 OCR（文字版 PDF／既有 MD 可跳過）
python scripts\ocr-book.py --pdf data\RAG_data\某書.pdf --pages 84-246 --offset 12 --threads 4 --out data\RAG_data\md\ocr\某書.md
# 2. 匯入並嵌入（冪等：以「檔案 sha256 + cleaner 版本」判斷是否需要重做）
python scripts\import-knowledge.py
# 3. 看語料切得對不對、量門檻與閘門
python scripts\analyze-rag-corpus.py
python scripts\calibrate-knowledge.py --dry-run
```

校準閘門是硬性的：**每份文件命中率 ≥80%，且命中帶與真負例帶不得重疊**。現況（2026-10-05）：3 份文件、341 段全部 `calibration_status=passed`；全域 26/28（93%），門檻 0.623（命中帶最低 0.6243 vs 真負例最高 0.6226，餘量僅 0.0017——能分開，但沒有安全邊際）。

- 仍有兩題「書裡有、排不上來」：`奶奶不肯吃饭怎么劝？`、`老人做了手术一直很怕`。瓶頸在嵌入模型（bge-small-zh）的判別力，不在門檻——四次嘗試（probe 向量、LLM 重排、句子滑窗、本地 cross-encoder）都留有實測數字，見 [RAG 知識庫計畫](docs/RAG_KNOWLEDGE_PLAN.md)。
- **重排器預設關閉**（`IOT_KNOWLEDGE_RERANK=0`）。本地 cross-encoder 實測能把召回從 26/28 提到 27/28，但它的分數帶與必須擋掉的負例重疊（正例最低 −1.67、負例最高 +1.53），等於會把「手冊沒寫」的問題答得和真命中一樣肯定，且每個子問題多花約 8 秒。要試：`bootstrap-project.ps1 -WithRerank` 之後把 `IOT_KNOWLEDGE_RERANK` 設成 `local`。
- **回答不會唸出書名或頁碼**：注入時只給節錄內容、不給來源標籤，出處改由 `citations` 欄位交給前端顯示（實測發現模型會照抄「（《溝通手冊》p18）」），並要求整段口語回答以 2～3 句、約 60 字為上限。
- 檢索**有**節錄但不夠回答時，提示詞要求它直說「手冊裡沒有寫到」；**完全檢索不到**時就當一般對話（`citations` 為空），不會假稱手冊寫過——實測問「怎麼給汽車換輪胎？」得到的是不引用來源的一般建議。

## ESP32 真機接入（小智 xiaozhi 協議）

專案內建一個**小智相容接入層**（`services/device_gateway/xiaozhi/`），不需要跑上游的 server：燒錄小智固件的 ESP32 直接連本服務的 `/xiaozhi/v1/`，上行 16 kHz 單聲道裸 Opus、下行 24 kHz，語音經既有 ASR → LLM → TTS 管線後回傳給設備播放。接入層是純增量，沒有改動既有對話路徑。

**預設是關閉的**（`IOT_ESP_ENABLED=0`）：未設定的 checkout 不會在區域網上監聽設備。要開啟：

1. `pip install pyogg opuslib_next edge-tts`，並確認 `ffmpeg` 在 PATH（或設 `IOT_FFMPEG`）。
   Windows 上 `opuslib_next` 透過 `ctypes.util.find_library("opus")` 找 libopus，而該函式在 Windows **無條件回傳 None**，所以 `pyogg` 是必需的——它的 wheel 內含 `opus.dll`，接入層會在使用時暫時改寫 `find_library` 指向它。
2. `IOT_TTS_PROVIDER=edge`。內建的 `windows` 與 `deterministic` provider 都只輸出**靜音**，設備會連上但不會有聲音；`GET /api/esp/status` 會以 `tts.produces_audio=false` 明說這件事，而不是讓它變成玄學問題。
3. 開啟 Dashboard →「設備接入」頁，打開接入開關，把頁面上顯示的 **OTA 地址**填進設備（必須是區域網 IP；填 `127.0.0.1` 會讓設備連回它自己）。
4. 設備上電後會先請求 OTA 取得 WebSocket 位址與 token，接著建立 WS 連線，出現在「已接入設備」。

**身分怎麼決定**：每一輪語音都會先跟已登記的聲紋樣本比對（`IOT_ESP_VOICEPRINT=1`，預設開啟），順序是
**聲紋命中 → 設備綁定的帳號 → `IOT_ESP_DEFAULT_USER`**。因此：

- 同一個使用者可以在任意多台設備上使用——身分跟著**聲音**走，不跟著設備走；
- **綁定不是必要條件**，只是「還沒登記聲紋／這次沒認出來」時的後備預設值；
- 聲紋命中視為**已驗證**身分，即使 `IOT_MEMORY_REQUIRE_IDENTITY=1` 也能取用自己的長期記憶與語言設定；
  僅靠綁定則仍只是一個「宣稱」，在该開關下不會放行個人記憶；
- 沒有任何已登記聲紋時會直接略過比對（不做嵌入運算），所以開啟這個功能本身不增加每輪成本。

比對沿用 `POST /api/voiceprint/identify` 完全相同的一組樣本、同一個 provider 版本與門檻，
命中結果會寫進 `device_events`（`speaker_identified`）並即時推到後台的 `speaker.identified` 事件。
聲紋的登記入口是 PC 模擬器頁的三步聲紋登記（`/simulator`）。

**講完話後自動待機**：小智固件的 `OnIncomingJson` 只認 `notify/tts/stt/llm/mcp/system/alert/custom`，
**沒有 `listen` 分支**——服務端下發 `{"type":"listen"}` 只會被印成 "Unknown message type"，不會改變狀態。
設備回到待命（`kDeviceStateIdle`）的唯一服務端可控路徑是**關閉音訊通道**（固件收到 `OnAudioChannelClosed` 才轉 Idle）。
因此本層的待機流程是：靜默到期 → 送出告別語（`IOT_ESP_STANDBY_NOTICE`）→ 關閉 WS → 設備回到待命、等待下一次喚醒詞。

計時器有兩個，故意分開——這是過去「回答完就一直卡在聆聽」的根因：

- `IOT_ESP_STANDBY_SECONDS`（預設 60）：**只**由「聽到人聲」（VAD 命中或設備送上文字）刷新。
  設備在聆聽期間會持續推靜音 Opus 幀，若用「收到任何封包」刷新，計時器永遠不會到期；
- `IOT_ESP_IDLE_TIMEOUT_SECONDS`（預設 300）：純連線層的死鏈看門狗，socket 完全不來訊息才觸發，
  故意設得比固件自身的 120 秒通道逾時長，避免和固件搶著收尾。

**回應速度、插話打斷與語音結束**（2026-10-04 補，設計見 [接入方案 §12](docs/ESP32_ESP_INTEGRATION_PLAN.md)）：
舊路徑是嚴格串行的「等完整回答 → 逐句合成 → 逐句發送」，這也是「說得慢／插話打斷不了／長句說一半就斷」的根因。現在改成：

- **串流回答**：LLM 走 SSE（`IOT_ESP_LLM_STREAM=1`），模型每寫出一個完整句子就立刻送去合成播放，
  所以**第一句在模型還沒寫完時就已經在出聲**。首句不受最小長度限制——首音延遲才是使用者真正感受到的延遲。
  非 200 或連線中斷且**尚未吐出任何內容**時自動退回非串流，已吐出的部分不會重來。
- **兩級流水播放器**（`playback.py` 的 `TtsPlayer`）：合成執行緒與播放協程並行，**句 n+1 的合成與句 n 的播放重疊**；
  單句合成失敗只丟那一句（錯誤推到後台 `display.state.audio_error`），不再拖垮整段回答。
- **超長句必切**：`IOT_ESP_TTS_MAX_CHARS`（預設 48）封頂，超過就在軟斷點（逗號、頓號、括號）切開，
  避免一個無標點的長文變成一個巨大請求、撞上 provider 逾時後整句被丟。
- **可打斷**：固件的 `OnIncomingJson` **沒有 `listen` 分支**，所以服務端能中斷播放的**唯一**一條消息是
  `tts state=stop`；本層在打斷時（設備送來 `abort`，或語音說「停」）一律先無條件下發它，再取消合成／播放。
- **語音結束對話**：整句精確匹配（**上限 12 字**，刻意如此——子串規則會在「我想結束這段關係」上誤掛電話）：
  說「停／別說了／安靜」→ 本地回一句、**不呼叫模型**、繼續聆聽；說「退下／結束／拜拜」→ 告別語 → 關閉 WS → 設備回待命。
- **喚醒詞靜默期**：`listen detect` 後固件自播提示音，`IOT_ESP_WAKE_WORD_HOLD_SECONDS`（預設 0.8 s）
  內忽略 VAD 斷句，避免把提示音當成「使用者說完了」。
- **元數據不朗讀**：prompt 要求模型在結尾附 ` ```json …``` ` 或裸 JSON（emotion / risk / remember）；
  串流切分器會在第一個標記處截斷，並把緊貼其前的半截句砍到最後一個完整終止符之後。
- **表情 emoji 也不朗讀**：prompt 另外要求在正文**開頭**只放一個白名單 emoji（決定螢幕表情）；
  切分器在放出第一句時把它剝掉，逐字串流、黏在首句、批次切分三種形態都剝，句中的 emoji 不動。

**「為什麼還是慢」——實測帳單**（`python scripts/measure-latency.py`，可自行複測）：

| 階段 | 實測 | 性質 |
|---|---|---|
| VAD 結束靜音判定（`IOT_VAD_SILENCE_MS`） | ~800 ms | 可調 |
| ASR（SenseVoice / sherpa-onnx） | 178–1200 ms | 本地模型，跑 **CPU** |
| LLM 首句可合成（串流） | 1494–2066 ms | **外部 API** |
| TTS 首塊（edge-tts） | 791–2077 ms（波動很大） | **外部服務** |
| ffmpeg 解碼 | 190–385 ms | 本地 |

鏈路裡最大的兩塊（edge-tts 首塊、DeepSeek 首句）都在服務外部：edge-tts 從本機連微軟的首塊延遲
會隨外網在 0.8–2.1 s 之間抖。想再快，最有效的一步是換一個國內的串流 TTS（需要憑證），
其次是把 edge-tts 的 MP3 邊收邊餵 ffmpeg（約省 0.4–0.9 s/句）。

**兩個容易踩的坑**（都會讓「看起來實作了」的功能其實從不工作）：

1. **固件的工具名是點分路徑**（`self.audio_speaker.set_volume`），而模型 API 只接受 `[A-Za-z0-9_-]`，
   直接送會拿到 **HTTP 400**。必須經過 `mcp.sanitize_tool_name()`（→ `self_audio_speaker_set_volume`）。
2. **有工具就不能等於關掉串流**。固件必定上報工具，若把「工具迴圈」放在串流前面，串流就永遠走不到，
   而測試（工具集為空）永遠是綠的。現在兩者共存：正文照常邊流邊合成，若某輪回了 `tool_calls`
   就執行後再流一輪，最後一輪撤掉工具以保證以「話」收尾。
3. **表情不能靠「使用者心情」猜**。設備的表情只在 `{"type":"llm","emotion":…}` 上（`tts` 不帶情緒），
   官方是**讓模型在自己的回覆開頭放一個 emoji，服務端掃那個 emoji** 決定表情。
   本層原本用三值情感分類（positive/negative/neutral）判**使用者**的心情，真機 20 輪全落在
   `neutral`，表情從頭到尾沒動過。現在照官方：提示詞給白名單（21 個），模型開頭放一個 emoji，
   `protocol.face_message()` 讀它；切分器會把開頭 emoji 剝掉，**不會被念出來**。
   兩處刻意不同：模型忘了放 emoji 時退回心情判定而不是無條件 `🙂`；風險回合（`attention`/`urgent`）
   用 😔／😱 蓋掉模型自己的選擇。見 [方案 §14](docs/ESP32_ESP_INTEGRATION_PLAN.md)。

**語音控制設備本身（音量／亮度／主題）**：固件把這些能力以 **MCP** 工具的形式暴露，
服務端必須在同一條 WebSocket 上驅動 JSON-RPC：`initialize` → `tools/list` → `tools/call`。
本層在歡迎訊息之後自動完成握手（`services/device_gateway/xiaozhi/mcp.py`），把工具註冊給 LLM，
並同時兼容舊的 `iot` 描述符協議（每個 method 會展開成 `<device>_<method>`）。
`IOT_ESP_TOOLS=0` 可整體關閉。兩條路徑都能讓「把音量調到 60」真正生效：

1. **規則直達**（`voicecmd.py`）：明確的設置句（有目標詞＋數值／大小聲標記）不經過模型，
   直接呼叫設備工具並由本地生成確認語，因此**不依賴模型是否支援 function calling**；
2. **模型工具呼叫**：其餘對話把設備工具交給 LLM，走 function calling 迴圈（上限 3 輪／30 秒），
   最後一輪不帶工具，強制模型用口語收尾。

設備詳情頁（`/dashboard/device/{id}`）會列出固件上報的工具清單，可逐一填參數手動執行；
`tools_status.reason` 會如實回報「固件未響應握手」等情況，而不是讓它變成玄學問題。

新增的設備面端點（不需後台登入，靠 `device-id` 標頭與可選的 token）：

- `WS /xiaozhi/v1/`：語音／文字協議（`hello`、`listen`、`abort`、`iot`、`mcp`、`ping`）
- `POST /xiaozhi/ota/`、`GET /xiaozhi/ota/`、`GET /xiaozhi/ota/download/{filename}`

新增的後台端點（一律要 admin session，寫入另需 CSRF）：

- `GET /api/esp/status`、`GET /api/esp/network`、`GET/POST /api/esp/settings`
- `GET /api/esp/firmware`、`POST /api/esp/firmware`、`DELETE /api/esp/firmware/{filename}`
- `GET /api/esp/ota-requests`
- `GET /api/devices/{id}`、`GET /api/devices/{id}/sessions`、`GET/POST /api/devices/{id}/commands`
- `POST/DELETE /api/devices/{id}/bind`、`GET /api/devices/{id}/token`
- `WS /ws/device-observe`：後台訂閱設備即時事件（丟給慢消費者的最舊事件並計數，絕不對設備反壓）

新增的後台頁面：

- `/dashboard/devices`：接入開關、待機秒數與提示語、設備工具總開關、局域網地址／OTA URL 一鍵複製、待接入設備綁定、固件上傳與刪除
- `/dashboard/device/{device_id}`：即時狀態機、字幕流、指令下發（播報／打斷／結束）、設備工具清單與手動執行、會話歷史、綁定管理

`scripts/` 之外的假設備客戶端在 `simulator/esp_client.py`，可在沒有真機時驗證整條鏈路：

```powershell
python simulator\esp_client.py --ota --ws --say "我今天很難過"
```

**已知邊界**：VAD 內建實作是能量式（`IOT_VAD_PROVIDER=energy`），要求更準的斷句需另接模型；說話人身分靠聲紋（預設 provider 為 ECAPA，首次使用需下載模型；未登記聲紋時退回管理員設定的設備綁定），OAuth/配對碼流程未實作；MQTT+UDP 傳輸未實作（本層只做 WebSocket）。**語音控制設備設定需固件支援 MCP 工具**——舊版固件不上報工具時，`/api/devices/{id}` 的 `tools_status` 會回報原因，對話仍可正常進行，但「調音量」這類指令只能口頭回應。完整設計與取捨見 [ESP32 接入方案](docs/ESP32_ESP_INTEGRATION_PLAN.md)。

## 測試與驗收

```powershell
pip install -r requirements-dev.txt      # 只多了 pytest，執行期相依仍在 requirements.txt
python -m pytest tests -q                # 375 passed, 1 skipped（本機實測，含 ESP 網關）
python -m compileall -q services simulator scripts
```

需要嵌入模型的那幾個測試在模型不存在時會自動 skip，所以沒下載模型也能跑完整套。

> **倉庫不含測試**：`tests\` 與驗收報告都列在 `.gitignore`，所以**從 GitHub 下載的副本沒有這些檔案**，在那裡執行 `python -m pytest tests -q` 會得到 `file or directory not found: tests`（exit code 4）。上面的數字是在保留測試的開發工作區跑出來的。服務本身不依賴 `tests\`，下載後照樣能安裝與啟動。

想驗收長期記憶的實際行為，最直接的方式是跑服務並用 Dashboard／模擬器對話：說一件事 → 隔一輪問它 → 使用資料頁的「長期記憶」區塊看層級、分數與命中次數的變化。三個具體腳本：

```text
短期記憶：連續問兩輪，第二輪問「你剛剛說了什麼？」→ 應該重述上一輪自己的建議
安全記憶：說「我不想活了，我想跳樓」→ 看 risk_events 是否 urgent、memory_chunks 是否多一列
          key=risk:safety（approved=1）；之後說「我又想不開了」→ 該列 hits 應 +1
知識庫　：問「什麼是首因效應？」→ citations 應出現 沟通手册 p26；問「怎麼換汽車輪胎？」
          → citations 應為空，且回答不假稱手冊寫過
```

設計、門檻依據與實測數字見 [長期記憶飛輪](docs/MEMORY_FLYWHEEL.md) 與 [RAG 知識庫計畫](docs/RAG_KNOWLEDGE_PLAN.md)，PC 階段的人工步驟見 [PC 驗收](docs/PC_ACCEPTANCE.md)。

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
| [RAG 知識庫計畫](docs/RAG_KNOWLEDGE_PLAN.md) | 語料清洗／切分／門檻校準、OCR 落地、重排器與四次嘗試的實測數字 |
| [ESP32 接入方案](docs/ESP32_ESP_INTEGRATION_PLAN.md) | 三條路線比較、協議映射、資料模型、Dashboard 新增與風險紅線 |
| [Git 流程](docs/GIT_WORKFLOW.md) | 分支、提交與上游 pin 的處理 |

`docs/superpowers/` 是當時的規劃紀錄，不是現況；日期化的歷史快照見 `docs/PROJECT_PROGRESS_2026-09-14.html`。

## 上游與 ESP 後續

上游版本固定在 commit `6afc54a17def47578a4b3efc4680873689d3168b`。官方 server 預設 WS 8000、OTA/HTTP 8003；MQTT gateway 另用 1883/TCP、8884/UDP、8007/API。ESP 到貨後才啟動 gateway、OTA 和真機聯調，並以區域網 IP 取代 localhost。
