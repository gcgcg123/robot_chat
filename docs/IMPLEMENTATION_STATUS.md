# 實作狀態

> 本檔描述**目前工作區**的實作狀態（HEAD `2ffde3c` 加上未提交的 ASR／安裝鏈改動）。
> 日期化的歷史快照見 `PROJECT_PROGRESS_2026-09-14.html`；規劃與規格文件見 `docs/superpowers/`（那些是當時的規劃紀錄，不是現況）。

## 已完成

- 固定並保留 `xiaozhi-esp32-server` 上游原始碼於 commit `6afc54a17def47578a4b3efc4680873689d3168b`。
- 部署目錄骨架、環境變數模板、SQLite schema 與 migration。
- DeepSeek OpenAI-compatible adapter（無密鑰時安全 fallback；已實際呼叫）。
- **可切換的 ASR 後端**，由 `ASR_PROVIDER` 決定：
  - `sensevoice`（預設）：SenseVoice-Small，ONNX int8 **228 MB**，`sherpa-onnx` runtime，原生支援粵語／普通話／英語／日語／韓語。
  - `whisper`：faster-whisper / CTranslate2 `whisper-large-v3-turbo-ct2`（約 1.5 GB），保留作為替代方案。
- 聲紋三段登記流程：樣本存於 `voiceprint_samples` 表，以 `(user_id, step)` 為鍵，**任一步驟可亂序、可重錄**。
- 文字模擬器與 Dashboard（不含後台文字輸入框）、裝置 heartbeat read-model 與 online/stale/offline API。
- P1 核心模組：ASR resident worker、TTS 分句／media ownership、聲紋判定與 enrollment 狀態機、DeepSeek client、local scoped memory、optional RAGFlow adapter、turn orchestration、risk merge、DeviceEvent v1 與 MQTT/UDP loopback frame。
- 啟動器：8 個 PowerShell 腳本**已納入版本控制**，解譯器由 `Resolve-ProjectPython` 解析（`IOT_PYTHON` → `configs/launcher.json` 的 `python` → `.venv` → PATH），不再硬編碼單機路徑；`一鍵安裝並啟動.bat` 會建立缺少的 `.env` 並下載對應的 ASR 模型。
- 心跳不再被系統代理劫持（httpx 會讀 Windows 註冊表代理且忽略 `ProxyOverride`，導致回環請求被送到本機代理而回 502）。

## 已驗證（可重現）

- 自動化測試：`python -m pytest tests -q` → **105 passed、1 failed**（該失敗見下節）；`python -m compileall -q services simulator scripts` 成功。
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
- 啟動器黑箱驗收：`tests/test_project_launcher.py` 10 passed（真實啟停往返、token 供應、篡改狀態不誤殺）。

## 已知失敗

- `tests/test_enrollment_workflow.py::test_language_profile_and_three_confirmed_samples`
  在**乾淨 HEAD `2ffde3c` 上就已失敗**（`assert 200 == 422`）：該提交把登記樣本改為 DB 儲存並允許任意步驟重錄，但沒有同步更新「亂序步驟必須回 422」這條斷言。與 ASR／安裝鏈改動無關。

## 尚未啟動或仍是佔位

- **TTS 語音合成**：`services/tts/windows.py` 任何情況都回傳 `DeterministicTts` 的**靜音**；實際發聲靠瀏覽器 `speechSynthesis`，`/api/media` 下發的 WAV 沒有前端消費者。
- **聲紋識別**：`services/voiceprint/provider.py` 只回傳 `[均值, RMS, 過零率]` 三個統計量；且對話鏈路（`app.py` 的 `_chat` 與 WebSocket 分支）硬編碼 `IdentityResult("accepted", …)`，等於「下拉框選中誰就是誰」，`/api/voiceprint/identify` 未接進對話路徑。
- **RAG／長期記憶**：`process_text` 的 `memories` 呼叫端都傳 `None`、`rag_provider` 永遠是 `None`，因此 citations 恆為空；`RAGFlowProvider` 的介面（`search`）與 pipeline 期望的（`enabled` + `retrieve`）不一致。
- **同意（consent）**：`services/security/consent.py` 只在測試中被呼叫，產品路徑未使用。
- **CUDA**：本機 `ctranslate2.get_cuda_device_count() == 0` 且查無 NVIDIA 驅動，**這台機器沒有可用的 NVIDIA GPU**（文件先前提到的 RTX 2060 屬於另一台機器）。ASR 一律走 CPU。
- MQTT/UDP broker、OTA、ESP 真機與 LCD 字幕／角色（第二階段）。
- 真人瀏覽器麥克風／喇叭聽感與播放 ACK 驗收。

## 環境事實

- 解譯器：Windows 上的 Anaconda 環境 `robot_chat`，**Python 3.11.16**（不是文件先前寫的 3.13）。
- 已安裝：`fastapi 0.141.1`、`uvicorn 0.53.0`、`httpx 0.28.1`、`pydantic 2.13.5`、`faster-whisper 1.2.1`、`ctranslate2 4.8.2`、`numpy 2.4.6`、`sherpa-onnx 1.13.8`。
- **`pytest` 仍未列入 `requirements.txt`**（見 `docs/superpowers/plans/2026-09-15-hardening-and-acceptance.md` 的 A1）；本檔的測試數字是在專案解譯器上以外部 pytest 執行得到。
- 模型權重不進 Git（`.gitignore` 的 `models/**`）；來源、必需檔案與 SHA-256 記於 `models/manifest.json`，下載方式見 `models/README.md`。
