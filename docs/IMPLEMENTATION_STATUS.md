# 實作狀態

## 已完成

- 固定並保留 xiaozhi-esp32-server 上游原始碼。
- 部署目錄骨架、環境變數模板、SQLite schema。
- DeepSeek OpenAI-compatible adapter（無密鑰時安全 fallback）。
- 已安裝 Python 3.13 虛擬環境與 `faster-whisper==1.2.1` / `ctranslate2==4.8.2`。
- 已下載可直接給 faster-whisper 使用的 CTranslate2 模型：`models/asr/whisper-large-v3-turbo-ct2`（來源 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`）。
- Whisper large-v3-turbo 的 faster-whisper adapter 端點。
- 文字模擬器與 Dashboard。
- 參考 `gcgcg123/emotional_chat` 的 MIT UI/情緒分析概念，重做為不含文字輸入框的 IoT 後台 Dashboard。
- 裝置 heartbeat read-model 與 online/stale/offline API。
- P1 核心模組：ASR resident worker、TTS 分句／media ownership、聲紋判定與 enrollment 狀態機、DeepSeek client、local scoped memory、optional RAGFlow adapter、turn orchestration、risk merge、DeviceEvent v1 與 MQTT/UDP loopback frame。
- Dashboard 使用者列表區塊與 PC simulator session API（不提供後台文字聊天框）。
- simulator heartbeat、裝置清單、情緒分布、系統狀態卡片與安全測試。

## 第一階段驗收狀態

- PC 端可由 `一鍵啟動.bat` 啟動；已完成登入、Dashboard、使用者建立、三段聲紋登記、PC 麥克風 WAV 錄音、ASR、情緒／風險分析、WebSocket 字幕事件與本機 fallback 對話。
- 自動化驗證：`python -m pytest tests -q` 為 **71 passed、6 warnings**；`python -m compileall -q services simulator scripts` 成功。
- 本地 Turbo CT2 模型已以 CPU `int8` 完成轉錄 smoke；CUDA 不可用時會明確降級，不阻塞 PC 驗收。
- RAG 客製知識庫未啟用。保留 scope／consent 邊界及 optional RAGFlow adapter，待專案自行定義資料、embedding、來源與治理規則。

## 尚未啟動

- 實際 DeepSeek 請求（需要由使用者在本機安全注入新密鑰）。
- RTX 2060 的 CUDA/cuDNN runtime 尚未在本機驗證；ASR 端點保留 CPU `int8` fallback，GPU 推理需後續安裝/驗證 CUDA 12 + cuDNN 9 runtime。
- 真人瀏覽器麥克風／喇叭聽感與播放 ACK 驗收（PC 端 PCM/WAV 錄音與 WebSocket 事件已實作）。
- 3D-Speaker／ECAPA 聲紋 embedding、模板加密與真人 FA/FR 校準。
- RAGFlow／embedding 外部部署與 DeepSeek 付費 smoke（本輪未呼叫）。
- MQTT/UDP broker、OTA、launcher ownership hardening、ESP 真機測試與 LCD 字幕／角色。
