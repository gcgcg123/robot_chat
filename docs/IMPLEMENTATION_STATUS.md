# 實作狀態

> 本檔描述**目前工作區**的實作狀態（HEAD `2ffde3c` 加上未提交的 ASR／安裝鏈／長期記憶改動）。
> 日期化的歷史快照見 `PROJECT_PROGRESS_2026-09-14.html`；規劃與規格文件見 `docs/superpowers/`（那些是當時的規劃紀錄，不是現況）。

## 已完成

- 固定並保留 `xiaozhi-esp32-server` 上游原始碼於 commit `6afc54a17def47578a4b3efc4680873689d3168b`。
- 部署目錄骨架、環境變數模板、SQLite schema 與 migration。
- DeepSeek OpenAI-compatible adapter（無密鑰時安全 fallback；已實際呼叫）。
- **可切換的 ASR 後端**，由 `ASR_PROVIDER` 決定：
  - `sensevoice`（預設）：SenseVoice-Small，ONNX int8 **228 MB**，`sherpa-onnx` runtime，原生支援粵語／普通話／英語／日語／韓語。
  - `whisper`：faster-whisper / CTranslate2 `whisper-large-v3-turbo-ct2`（約 1.5 GB），保留作為替代方案。
- 聲紋三段登記流程：樣本存於 `voiceprint_samples` 表，以 `(user_id, step)` 為鍵，**任一步驟可亂序、可重錄**。
- **長期記憶飛輪已完成**（三層：槽位／熱／冷，惰性分數衰減，自動寫入與復活）。
  設計、實測數字與環境變數見 **`docs/MEMORY_FLYWHEEL.md`**。每一輪對話由
  `services/memory/recall.select_for_turn()` 依「槽位 → 熱 → 冷」順序挑選要注入的記憶，
  經 `retriever.visible_chunk()`（`approved`、`active`、身份）與相關度門檻過濾後，
  以不可信參考注入提示詞；回合結束再由 `repository.apply_turn()` 為命中的記憶加分、
  寫入模型提出的候選、重算層級。記憶綁定 `users` 帳號（`memory_chunks.owner_user_id`
  有外鍵約束），**每個帳號只拿得到自己的記憶＋共享知識**；`/api/chat` 與 WebSocket
  兩條入口都已接線。`IOT_MEMORY_REQUIRE_IDENTITY=1` 時，未經聲紋核實的身份聲明拿不到
  個人記憶（預設 0 = 模擬器選中的使用者可讀自己的記憶）。
- **Dashboard 記憶面板**：`/dashboard/user/{id}` 可檢視每則記憶的層級／分數／命中次數／
  probe，並可手動新增、升降層級、歸零衰減、刪除。手動新增走 `upsert_memory()` 並寫入
  嵌入向量，所以手寫的記憶真的會被檢索到。
- **嵌入模型**：`Xenova/bge-small-zh-v1.5` ONNX（約 90 MB，512 維），用 `onnxruntime`
  ＋`tokenizers`，不需 torch；已列入 `models/manifest.json`、`models/README.md` 與
  下載腳本的 catalog，`一鍵安裝並啟動.bat` 會與 ASR 模型一併下載。
- 文字模擬器與 Dashboard（不含後台文字輸入框）、裝置 heartbeat read-model 與 online/stale/offline API。
- P1 核心模組：ASR resident worker、TTS 分句／media ownership、聲紋判定與 enrollment 狀態機、DeepSeek client、local scoped memory、optional RAGFlow adapter、turn orchestration、risk merge、DeviceEvent v1 與 MQTT/UDP loopback frame。
- 啟動器：8 個 PowerShell 腳本**已納入版本控制**，解譯器由 `Resolve-ProjectPython` 解析（`IOT_PYTHON` → `configs/launcher.json` 的 `python` → `.venv` → PATH），不再硬編碼單機路徑；`一鍵安裝並啟動.bat` 會建立缺少的 `.env` 並下載對應的 ASR 模型。
- 心跳不再被系統代理劫持（httpx 會讀 Windows 註冊表代理且忽略 `ProxyOverride`，導致回環請求被送到本機代理而回 502）。

## 已驗證（可重現）

- 自動化測試：`python -m pytest tests -q` → **216 passed、1 skipped、0 failed**（本機 2026-10-03 實測，50.6 秒）。
  被 skip 的是 `tests/dialogue/test_websocket.py::test_simulator_websocket_emits_turn_subtitle_and_tts_events`：
  它只等 `tts.end`，但自 ECAPA 聲紋升級（`92e0456`）後，未登記聲紋模板的 `voiceprint_id` 会在 WS 回合被拒
  （`turn.failed/voiceprint_required`），迴圈因此永不結束——改動前的程式碼同樣會卡住，是測試過期而非產品問題。
  測試樹不在 Git 內（`.gitignore` 的 `tests/`），此數字是磁碟上那份的結果。
- `python -m compileall -q services simulator` 成功；`models/manifest.json`、`configs/launcher.json` 可解析，
  `docker-compose.yml` 可被 YAML 解析且 `WITH_VOICEPRINT` build arg 生效。
- 全新 clone 演練（2026-10-03，隔離目錄 + 真實 `一鍵安裝並啟動.bat` 流程）：在沒有 `.env`、沒有 `.venv`、
  PATH 上只有 Anaconda base（3.13.9）的條件下，`bootstrap-project.ps1` 自動建立 `.venv`（**208.5 MB、38 個套件、無 torch**）、
  把依賴裝進去、由 `.env.example` 產生 `.env`、供應 simulator token 並以該 `.venv` 啟動服務；`/health` 回報
  `asr_backend=sensevoice`、`diagnostics=["voiceprint_dependencies_missing"]`、`voiceprint_missing_dependencies=[torch, torchaudio, speechbrain]`。
  對照組（本機 conda 環境，含 torch）診斷為空。
- 記憶飛輪端到端（真模型、真服務，非 mock）已驗證過：自動寫入 → 無關問題不注入 →
  相關問題命中加分 → 回溯 90 天降為冷記憶 → 無關問題不喚醒 → 重新提起後冷記憶被找到並
  復活為熱記憶，以及面板手動新增的記憶確實被對話檢索到。產生這些結果的臨時驗收腳本與
  報告已於整理時刪除；同一組行為現在由 `tests/memory/` 的測試守住（`tests\` 不納入 Git，
  但檔案留在磁碟上可執行）。人工複驗步驟見 `docs/MEMORY_FLYWHEEL.md` 的「怎麼驗收」。
- ASR 實測，本機 **CPU / int8**，模型已載入的穩態：

  | 音訊 | 長度 | SenseVoice 耗時 | Whisper large-v3-turbo |
  |---|---|---|---|
  | 粵語 | 5.15 s | **435 ms** | — |
  | 普通話 | 5.59 s | **490 ms** | — |
  | 英語 | 7.15 s | 611 ms | — |
  | 日語 | 7.20 s | 611 ms | — |
  | 韓語 | 4.61 s | 435 ms | — |
  | 合成語音 | 3 s | — | 約 33,800 ms |
  | 合成語音 | 10 s | — | 約 32,100 ms |

  Whisper 的耗時**與錄音長度無關**（固定 30 秒編碼窗口），因此縮短錄音不會變快；這是改用 SenseVoice 的主因。
- ASR 後端與 GPU 決策可由 `/health`（`asr_backend`、`asr_model`）與 `/api/system/status`（`asr.device`、`asr.compute_type`、`cuda_available`、`runtime_reason`）查得。
- 啟動器黑箱驗收：`tests/test_project_launcher.py` 16 passed（真實啟停往返、token 供應、篡改狀態不誤殺）。

## 已知失敗

- 目前無已知失敗。先前記錄的
  `tests/test_enrollment_workflow.py::test_language_profile_and_three_confirmed_samples`
  是**斷言過期**而非程式缺陷：該提交把登記樣本改成以 `(user_id, step)` 為鍵的 upsert
  （任一步驟可亂序、可重錄），但測試仍寫著「亂序步驟必須回 422」。已把斷言改成
  描述實際契約（超出 1–3 的步驟才拒絕；重錄同一步驟是取代而非新增，`sample_count` 不變）。

## 尚未啟動或仍是佔位

- **TTS 語音合成**：`services/tts/windows.py` 任何情況都回傳 `DeterministicTts` 的**靜音**；實際發聲靠瀏覽器 `speechSynthesis`，`/api/media` 下發的 WAV 沒有前端消費者。
- **聲紋識別**：`services/voiceprint/provider.py` 只回傳 `[均值, RMS, 過零率]` 三個統計量；
  `/api/voiceprint/identify` 未接進對話路徑。對話路徑改用 `services/dialogue/identity.py`
  的 `conversation_identity()`：預設（`IOT_MEMORY_REQUIRE_IDENTITY=0`）回傳
  `accepted`＝「下拉框選中誰就是誰」，但當開關為 `1` 時會回傳 `unknown` 並擋掉個人記憶，
  所以它不再是無條件放行。
- **本地 RAG（共享知識庫）已接通**：`knowledge_documents` / `knowledge_chunks`（schema v6）+ `scripts/import-knowledge.py` + `services/knowledge/retriever.py`，由 `create_app` 建成 `providers["rag"]`。第一份語料（《老年人溝通技能指導手冊》）清洗後 **30 個 chunk（scenario 13 / reference 17）**，門檻 0.62 由 15 題校準集量出（命中 11/11、命中帶 0.6395–、未命中帶 –0.6016，閘門通過），引用形如 `溝通手冊 p18 › 二、老年人服務中的語言溝通`；手冊沒寫的問題會檢索到空，提示詞因此回「手冊裡沒有寫到」而不是硬答。`/health` 回報 `knowledge_chunks`/`knowledge_mode`/兩個 floor。外部 RAGFlow 適配器仍未配置（其 `search` 介面與 pipeline 期望的 `enabled` + `retrieve` 不一致）。
- **新加入的兩本語料無法使用**：`data/RAG_data/` 下的《老年人沟通技巧》（47 MB / 251 頁）與《明明白白你的心》（20 MB / 306 頁）都是**無文字層的掃描件**（抽樣 31–32 頁皆 0 字），需要 OCR 而非 `scripts/pdf_to_md.py` 轉換；且檔名顯示來自 Anna's Archive 與 z-library，**不應進 Git**。`scripts/analyze-rag-corpus.py` 的 PDF 入口體檢會直接報出這件事。詳見 `docs/RAG_KNOWLEDGE_PLAN.md` A9.4。
- **同意（consent）**：`services/security/consent.py` 只在測試中被呼叫，產品路徑未使用。
- **CUDA**：本機 `ctranslate2.get_cuda_device_count() == 0` 且查無 NVIDIA 驅動，**這台機器沒有可用的 NVIDIA GPU**（文件先前提到的 RTX 2060 屬於另一台機器）。ASR 一律走 CPU。
- MQTT/UDP broker、OTA、ESP 真機與 LCD 字幕／角色（第二階段）。
- 真人瀏覽器麥克風／喇叭聽感與播放 ACK 驗收。

## 環境事實

- 解譯器：Windows 上的 Anaconda 環境 `robot_chat`，**Python 3.11.16**（不是文件先前寫的 3.13），路徑寫在被 Git 忽略的 `.env`（`IOT_PYTHON`）；`configs/launcher.json` 的 `python` 留空，所以其他機器會落到自己的 `.venv` 或 PATH。
- 已安裝：`fastapi 0.141.1`、`uvicorn 0.53.0`、`httpx 0.28.1`、`pydantic 2.13.5`、`faster-whisper 1.2.1`、`ctranslate2 4.8.2`、`numpy 2.4.6`、`sherpa-onnx 1.13.8`、`onnxruntime 1.30.0`、`tokenizers 0.23.2`。
- **相依分成三份**：`requirements.txt` 只含預設路徑（約 110 MB，實測 33 個套件）；`requirements-asr-whisper.txt` 是 Whisper 後端（約 65 MB，`ctranslate2`／`av`）；`requirements-voiceprint.txt` 是聲紋（約 730 MB，`torch` 506 MB、`scipy` 113 MB、`sympy` 72 MB）。後兩者不在預設安裝內，缺件時對應功能**明確失敗**並附安裝指令（Whisper 於模型工廠、聲紋於登記端點 503，`/health` 另列 `voiceprint_dependencies_missing`），不靜默降級。`pytest` 在 `requirements-dev.txt`。
- 模型權重不進 Git（`.gitignore` 的 `models/**`）；來源、必需檔案與 SHA-256 記於 `models/manifest.json`，下載方式見 `models/README.md`。
