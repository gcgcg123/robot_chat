> **⚠ 歷史規劃文件（2026-09-13）**：本檔是當時的規劃／規格紀錄，保留原樣以利追溯。
> 其中的個人路徑（`C:/Users/gcgcg/...`）指向撰寫當時的機器，**在本儲存庫無效**；
> 「目前不是 Git repository」「已下載的模型」等敘述也可能已經改變。
> 現況請看 [IMPLEMENTATION_STATUS.md](../../IMPLEMENTATION_STATUS.md)。

# 使用者管理與聲紋識別設計規格

**日期：** 2026-09-13  
**狀態：** 方案 B 已獲同意；本詳細規格待使用者審閱，尚未實作  
**範圍：** LOCAL-3 情感陪伴機器人

**語音／螢幕補充規格：** [ESP 雙向語音、字幕與機器人表情](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/specs/2026-09-13-esp-voice-display-design.md)。此補充明確要求後端 TTS、裝置播放、逐句字幕及角色展示，細化下文工作包。兩份規格的執行分界與檔案清單以 [兩階段動工計劃](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/plans/2026-09-14-two-stage-implementation.md) 為準。

## 1. 目標

在現有 FastAPI + SQLite + 原生 Dashboard 上建立使用者管理與個人資料頁，讓管理員可以從 Dashboard 建立使用者、登記個人資料和聲紋；之後每次收到 PC 麥克風或 ESP 音訊時，系統可辨識目前說話者、執行 ASR、情緒與緊急程度分析，並將結果歸檔到對應使用者。

ESP 尚未到貨時，PC 麥克風是唯一的硬體替代入口。真實 ESP 的 MQTT/UDP、Xiaozhi WebSocket 或其他 gateway 只在後續 adapter 階段接入，不改變本規格定義的音訊處理介面。

ESP 是雙向對話終端：laptop 的文字回覆必須經 TTS 回傳到喇叭，螢幕同步顯示當句字幕與機器人表情。PC 階段用麥克風、喇叭／耳機和虛擬小螢幕一起模擬，不能只驗證語音上傳。

## 2. 已確認的邊界

- Dashboard 是後台檢視與註冊入口，不提供瀏覽器文字聊天輸入框。
- DeepSeek API key 只存在後端環境或 Windows DPAPI 加密儲存，不回傳給前端。
- Whisper 只負責 ASR；聲紋模型獨立負責說話者識別，兩者並行處理。
- 聲紋識別是個人化對話用途，不是門禁、金融或法定身份驗證。
- 第一版使用本地聲紋模組與 SQLite；保留與上游 `voiceprint-api` 對接的 adapter 邊界。
- 個人資料、聲紋向量和情緒資料屬敏感資料，必須提供刪除／停用能力，且 Dashboard 不顯示原始聲紋向量或原始音訊。
- 緊急狀態是風險提示，不是醫療診斷；需要保存判斷原因並允許人工確認。

## 3. 方案選擇

### 3.1 採用：本地整合聲紋 MVP

聲紋服務作為目前 FastAPI 專案內的一個清楚分層模組。它接收標準化音訊，輸出 embedding、候選使用者、相似度分數和門檻判定。SQLite 保存使用者與聲紋 metadata；embedding 使用 Windows DPAPI 加密後保存，不能由 Dashboard API 直接讀取。模型 runner 與業務 API 分離，必要時可使用獨立 Python 環境，不要求更換現有 ASR 環境。

理由：目前沒有 ESP、沒有必要先引入 MySQL/Docker；本方案可以直接支援 PC 麥克風驗證，也能在日後以 adapter 取代為上游 voiceprint-api。

### 3.2 上游參考方式

參考 `xiaozhi-esp32-server` 的 `voiceprint-integration.md` 與 `voiceprint_provider.py`：

- register／identify／health 三類接口；
- `speaker_id` 對應使用者；
- similarity threshold；
- ASR 與聲紋並行；
- 辨識失敗時回傳未知說話人。

不直接搬用上游的 MySQL schema、靜態 speakers 設定或管理介面，因為本專案需要從 Dashboard 動態新增使用者並保存性別、年齡及風險資料。

本次參考的是使用者下載的本地副本，沒有重新核對 GitHub 最新提交。參考來源：

- [上游聲紋文件](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/xiaozhi-esp32-server-main/xiaozhi-esp32-server-main/docs/voiceprint-integration.md)
- [上游聲紋 provider](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/xiaozhi-esp32-server-main/xiaozhi-esp32-server-main/main/xiaozhi-server/core/utils/voiceprint_provider.py)

文件中的 `voiceprint-api` 是另一個專案；僅有目前下載的 Xiaozhi 資料夾不代表其推論服務或模型已經安裝。實作時若複用程式碼，需記錄來源版本、核對授權並保留必要聲明；不直接執行參考文件內的部署或刪除指令。

### 3.3 現況與本次新增功能的差別

目前 `users` 表只有 ID、顯示名稱和建立時間，且會隨 heartbeat／文字 replay 建立 demo 使用者；尚無真正的聲紋註冊流程。情緒標籤目前來自文字關鍵字，並非聲音情緒模型或臨床評估；`/health` 也不代表 ASR 已實際推論成功。本規格定義的是新功能，不能將現有靜態「已配置」卡片當成驗收證據。

## 4. 使用者體驗

### 4.1 使用者列表

左側導航在「總覽」和「情緒分析」之間新增「使用者」。列表提供：

- 新增使用者按鈕；
- 姓名、自述性別、自述年齡及資料確認日期；
- 聲紋狀態（未註冊、註冊中、已註冊、已停用）；
- 最近心情；
- 最近對話時間；
- 最近互動裝置和 online/stale/offline 狀態；裝置在線不表示本人在場；
- 緊急狀態徽章；
- 搜尋、狀態篩選、最近活動排序。

點擊一列進入 `/dashboard/users/{user_id}` 個人頁。

列表保留未完成註冊、已停用、已刪除聲紋但保留帳戶的使用者，明確區分「刪除聲紋」與「刪除整個使用者」。既有 demo 使用者加上 simulator 標記，不自動升級為已完成聲紋註冊的真人。

### 4.2 新增使用者與聲紋註冊 wizard

1. 管理員點擊「新增使用者」，選擇 PC 麥克風；ESP 到貨後才開放選擇已授權 ESP。
2. 顯示個人資料與聲紋用途、保存及刪除說明。取得同意後建立草稿使用者和綁定該裝置的註冊工作 `enrollment_id`。拒絕聲紋同意時可建立無聲紋帳戶，但不能自動識別。
3. 使用者點擊「開始」並授予瀏覽器麥克風權限。Dashboard 按鈕不能繞過權限或遠端秘密開啟 PC 麥克風。
4. 語音提示依次詢問姓名、性別、年齡；ASR 轉錄填入表單，由本人／管理員確認與更正後才保存。性別允許男、女、自訂、不透露；年齡可不提供。不得從音色推測性別或年齡。
5. 姓名容易出現同音字，因此表單編輯是必要功能，不是聊天文字框。年齡保存為 `age_at_registration` 和 `age_recorded_at`；列表標明「登記時年齡」，不根據出生年份虛算精確歲數。
6. 播放並顯示隨機朗讀提示，先收集三段，每段建議 5–10 秒；品質不足時最多補錄至五段。個資回答不自動兼作聲紋樣本，避免過短或混入其他人的聲音。
7. 瀏覽器協商實際支援的 MediaRecorder 格式並提交真實 MIME type；後端解碼、重採樣為 16 kHz、Mono、PCM16。不能將 WebM 檔案僅改副檔名當成 WAV。初版兼容音訊上傳備援。
8. 後端檢查有效人聲長度、音量、削波及樣本間說話者一致性，輸出具體重錄原因。最低有效人聲先定為每段三秒，實作時用測試音訊核對品質門檻；不宣稱能可靠分離多人混合語音。
9. 對每段向量 L2 正規化，平均後再正規化形成候選模板。另錄一段不參與模板建立的獨立驗證語音，與候選模板及已存在的其他使用者比較；接受門檻和適用的第一、二名差距均合格才提交聲紋，否則重錄或人工處理疑似重複帳戶。
10. 個資確認與聲紋驗證都通過才顯示「註冊完成」。重新註冊在新模板成功前保留舊模板，取消／逾時清理候選資料；刪除聲紋不自動刪除聊天紀錄。

註冊提示使用後端共用 TTS，PC 播放生成的音訊並顯示提示句；瀏覽器 speechSynthesis 僅可作 UI 初測備援，不能代替後端音訊下行驗收。播放時暫停收音，播放完成後才錄音，先驗證半雙工流程。完整對話播放、字幕與角色屬音訊整合階段必需項，具體依語音／螢幕補充規格。

### 4.2.1 註冊工作控制

狀態依序為 `pending → awaiting_device → collecting_profile → collecting_samples → verifying → completed`，另有 `failed`、`canceled`、`expired`。每個工作包含 `user_id`、`device_id`、到期時間與樣本 ID，同一裝置同時只允許一個註冊工作。

- PC 在使用者允許收音後回報 ready；ESP 後續由 gateway 發送含工作 ID 的指令，收到裝置 ACK 才錄音。指令格式是本專案 adapter 的責任，不假定原版 Xiaozhi 韌體已支援。
- 工作預設十分鐘到期，ready 等待最多三十秒；逾時顯示原因，可由管理員重試，不在頁面持續假裝正在錄音。
- 樣本以工作 ID、樣本 ID 去重；斷線重送不能增加註冊次數或覆蓋另一人的聲紋。
- 註冊用語音不計入一般對話、情緒及風險統計，不送到 DeepSeek。停止／取消後立即關閉瀏覽器音軌。

### 4.3 個人資料頁

頁面包含：

- 基本資料：姓名、自述性別、登記時年齡、備註、同意及資料確認時間；
- 聲紋卡片：狀態、樣本數、品質分數、模型名稱／版本、註冊時間、重新註冊與刪除；
- 裝置卡片：最近互動裝置、最近 heartbeat、裝置狀態、是否 simulator；
- 情緒卡片：最近一次對話推估的情緒及時間、近 7／30 日趨勢、情緒事件數量；無近期資料顯示資料不足，不寫成此刻真實心情；
- 對話時間軸：時間、ASR 文字、情緒、回覆、模型、延遲、聲紋分數；
- 風險卡片：`none`、`attention`、`urgent`，觸發原因、時間及人工確認狀態。

## 5. 系統架構與資料流

```text
PC mic / ESP audio
        |
        v
Audio ingress + VAD + quality gate
        |
        +--------------------+--------------------+
        |                    |
        v                    v
Speaker embedding       faster-whisper ASR
        |                    |
        v                    v
Voiceprint matcher      transcript
        |                    |
        +---------+----------+
                  v
      identity + transcript + device
                  |
                  v
        emotion / risk analysis
                  |
                  v
        DeepSeek dialogue response
                  |
                  v
 SQLite event/read model + Dashboard API
```

身份判定規則：

1. 沒有可用音訊時拒絕該段。聲紋品質不夠但 ASR 仍有可用文字時，允許訪客對話，不因此停止文字中的風險訊號檢查。
2. 最高相似度低於門檻時，`decision = unknown` 且 `user_id = null`。
3. 最高與第二名差距不足時，`decision = ambiguous` 且 `user_id = null`，不得自動歸檔到任何人。
4. 成功識別時保存 `voiceprint_id`、模型版本、分數、門檻和辨識耗時。
5. ASR 或 DeepSeek 失敗時保留事件狀態與錯誤類型，不丟失 heartbeat 或聲紋結果。

上方是分析與資料保存路徑；同一回覆還需送到 TTS 斷句合成，再由 PC／ESP adapter 下發音訊、字幕與表情。輸出參照補充規格，並分開記錄已生成、已發送和實際播放完成。

第一版 Dashboard 列表採輪詢；PC 裝置的音訊／字幕事件另用即時通道，ESP 使用相容通訊 adapter，不能以 Dashboard 的輪詢頻率控制語音播放。

「即時識別」是取得足夠有效語音後判定，不承諾一開口就知道姓名。每輪保存獨立辨識結果；只有接受的身份才能讀取該人的歷史或 RAG 記憶。未知、模糊、多人重疊或辨識服務出錯均不得沿用上一位使用者身份。`device_id` 代表裝置，`user_id` 代表說話者，一台 ESP 可供多人使用；heartbeat 宣告的 user_id 不作身份證據。

伺服器負責從聲紋結果決定 `user_id`，不能信任 PC／ESP 任意上傳的身份字串。原有 `/api/chat` 的手動指定身份只保留為受限 demo 測試，不能繞過正式音訊入口讀取真人資料。

## 6. API 合約（規劃）

### 使用者

```text
GET    /api/users
POST   /api/users
GET    /api/users/{user_id}
PATCH  /api/users/{user_id}
DELETE /api/users/{user_id}
```

`POST /api/users` 建立草稿，`PATCH` 保存確認後的 `display_name`、`gender`、`age_at_registration`、`profile_note`。同意動作保存版本、時間和操作者。年齡允許空值，提供時需為 0–120 整數。管理員可選擇不登記聲紋；禁止讓前端提交或取得 embedding。`DELETE` 是有確認步驟的完整資料刪除，停用使用 `PATCH`。

### 聲紋

```text
POST   /api/users/{user_id}/enrollments
GET    /api/enrollments/{enrollment_id}
POST   /api/enrollments/{enrollment_id}/ready
PATCH  /api/enrollments/{enrollment_id}/profile
POST   /api/enrollments/{enrollment_id}/samples
POST   /api/enrollments/{enrollment_id}/verify
POST   /api/enrollments/{enrollment_id}/complete
POST   /api/enrollments/{enrollment_id}/cancel
POST   /api/voiceprint/identify
DELETE /api/users/{user_id}/voiceprint
GET    /api/users/{user_id}/voiceprint/status
```

samples／verify 使用 multipart 音訊、真實 MIME type 和樣本 ID；verify 音訊不加入模板訓練。complete 只提交已通過驗證且個資已確認的工作。瀏覽器聲紋識別結果僅作管理測試；正式對話由服務端重新驗證。

識別接口回傳 `matched`（布林）、`user_id`（接受時為 UUID，否則 null）、`score`、`threshold`、`top_two_margin`、`model_name`、`model_revision` 及 `decision`。無法推論時 score 為 null，不能用零冒充測量值。`decision` 為 accepted、unknown、ambiguous、insufficient_audio 或 unavailable；`model_revision` 從實際安裝 manifest 讀取來源提交／檔案雜湊，不用任意版本字串代替。

### 個人頁資料

```text
GET /api/users/{user_id}/conversations
GET /api/users/{user_id}/emotion-trend
GET /api/users/{user_id}/risk-events
POST /api/risk-events/{event_id}/acknowledge
POST /api/risk-events/{event_id}/resolve
```

### 統一音訊入口

```text
POST /api/audio/process
```

此接口接收音訊、`device_id`、session metadata 和可選語言，回傳 identity、transcript、emotion、risk、reply、latency 及事件 ID，保留作單輪測試入口。PC／ESP 的即時音訊回傳 adapter 接入同一主流程事件，另行實作協議映射，不直接存取 SQLite；此 HTTP 接口本身不等於 Xiaozhi 裝置協議相容。

每次音訊提交包含唯一 `request_id` 和 `session_id`，服務端去重，不能因重試重複計費／寫入對話。上傳限制初版為單段最多六十秒、十 MB；錯誤區分格式不支持、無人聲、處理逾時及各模型失敗。emotion、risk、ASR 都另帶執行狀態，不把失敗回報為中性或無風險。

個人紀錄列表分頁（預設 20、最多 100），時間以 UTC 保存、Dashboard 依 Asia/Hong_Kong 顯示。「今日」和「近 24 小時」查詢必須真的按相應時間範圍過濾，不能沿用目前全量統計而只改標籤。

## 7. 資料模型

保留現有 `users`、`conversations`、`emotion_events`、`conversation_analysis`、`devices`、`device_events`，擴充或新增：

```text
users
- user_id (PK)
- display_name
- gender
- age_at_registration nullable
- age_recorded_at nullable
- profile_note
- status (draft / active / disabled；與聲紋狀態分開)
- created_at
- updated_at

consent_events
- consent_id (PK)
- user_id (FK)
- purpose (profile / voiceprint / cloud_dialogue)
- consent_version
- decision (granted / withdrawn)
- actor_id
- created_at

enrollment_sessions
- enrollment_id (PK)
- user_id (FK)
- device_id
- state
- profile_confirmed_at nullable
- candidate_template_ref nullable
- verification_decision nullable
- expires_at
- created_at
- updated_at

enrollment_samples
- sample_id (PK)
- enrollment_id (FK)
- prompt_id
- embedding_ref
- speech_duration_ms
- quality_json
- decision
- created_at

voiceprints
- voiceprint_id (PK)
- user_id (unique FK)
- embedding_ref (受限儲存引用，不是 API 回傳欄位)
- sample_count
- quality_score
- model_name
- model_revision
- matcher_config_version
- status
- enrolled_at
- updated_at

voiceprint_events
- event_id (PK)
- user_id nullable
- device_id nullable
- decision
- score
- threshold
- top_two_margin nullable
- session_id
- request_id
- model_revision
- latency_ms
- created_at

risk_events
- event_id (PK)
- user_id nullable
- conversation_id nullable
- risk_level
- reason
- detector_version
- analysis_status
- evidence_turn_ids
- review_status (open / acknowledged / resolved)
- acknowledged_by nullable
- resolution_note nullable
- acknowledged_at nullable
- created_at
```

另擴充會話／輪次與操作紀錄：`dialogue_sessions` 保存開始／最後活動／結束時間，`audio_turns` 保存 request ID、各階段狀態和耗時；`audit_events` 保存管理動作的操作者、目標及時間，不複製私密對話。識別不成功的 `conversations.user_id` 為 null，透過 session 關聯訪客資料，不把所有 unknown 使用者合成同一個人或共用記憶。

需要版本化 SQLite migration 重建目前非空 user_id 欄位，保留既有 ID、對話及時間，升級前備份並驗證回復方式。不能用清空資料庫代替 migration。註冊工作與模板的提交需原子性；對每個裝置的在途註冊工作及每個 request ID 建立唯一約束。

原始 WAV 預設只在非同步目錄短期暫存，單次推論完成即清除；崩潰遺留暫存最多一小時。註冊只保留工作期間加密的候選向量，完成／取消／十分鐘逾時後清理。除錯保留錄音必須另行取得同意，預設關閉，啟用時 TTL 固定二十四小時。

## 8. 模型與門檻策略

- ASR 沿用已下載的 `whisper-large-v3-turbo-ct2`；不要因加入聲紋而重複下載 Whisper。
- 首選待驗證模型為 `speechbrain/spkrec-ecapa-voxceleb`，另一可替換 provider 為上游使用的 3D-Speaker。開發階段先核對模型與程式授權、來源、Windows／Python 3.13 相容性及一次真實 embedding 推論，通過後把來源 revision、雜湊和套件版本寫入模型 manifest；未通過不得顯示模型已就緒。
- 若套件不支援目前 Python，先採獨立 Python 3.11 聲紋 runner，由啟動器一併管理；不能為了聲紋覆蓋目前已能啟動的 ASR 虛擬環境。此為相容性檢查後的條件分支，不代表目前已安裝或驗證。
- 相似度門檻不可直接照搬上游預設 0.4，也不是概率或「82% 身份正確」。使用獨立錄音校準接受門檻與第一、二名差距，保存版本化設定；只有一位候選人時不能檢查第二名差距，仍需滿足絕對門檻並顯示驗證樣本不足。
- 先將聲紋放 CPU、ASR 優先 CUDA，避免 RTX 2060 顯存競爭；兩條任務在受限工作執行緒／runner 執行，不能阻塞 FastAPI 事件迴圈。ASR 模型只初始化一次，不沿用目前每次上傳重新載入的做法。
- CUDA／cuDNN 不可用時明確回報並切到 CPU int8；切換後須實際推論驗證。服務狀態區分 configured、loading、ready、degraded、error，回報實際 device、compute type、模型版本及等待隊列。
- 普通話、粵語和中英混合分開測試；不假定語言偵測概率就是逐字轉錄準確度。第一輪同時記錄冷啟動與暖機後 ASR、聲紋、LLM、全流程耗時，再決定回應速度目標，不先宣稱即時延遲已達標。

## 9. 緊急狀態策略

情緒推估、風險提示及聲紋身份是三個獨立結果。負面心情不等於緊急事件，也不從聲音辨識性別、健康或精神疾病。第一版以轉錄文字和經確認的會話上下文作為分析輸入：

1. 本地版本化規則先檢查明確求救或迫切危險陳述，保存來源輪次；一般負面詞只作候選，不單憑它下診斷。
2. LLM 在啟用雲端同意時以固定 JSON schema 補充情緒、話題摘要、風險級別及可核對的文字依據；對引用、否定和第三人稱故事加入測試。ASR／LLM 生成內容均作資料，不允許其改寫身份或操作管理接口。
3. 規則或經驗證的 LLM 分析提示 urgent 時建立「需立即人工確認」事件，記錄來源和分歧；不要求兩者一致才顯示警示，也不因後續服務失敗自動消除已有警示。
4. DeepSeek 逾時、結構錯誤或無可用文字時標記 `analysis_status = unavailable / partial`。沒有分析不能顯示「正常」或「確定無危險」，有效本地警示仍保留。
5. 保存 `risk_level` 與獨立 `review_status`。人工 acknowledge 只表示已查看，resolve 需備註並留稽核紀錄；既有事件不隨新的一輪正常對話自動清除。
6. 未知說話人的警示進入總體事件列表，身份標為未確認，不任意寫進另一人的資料頁。介面說明存在漏報／誤報，並非持續有人值守的緊急服務；不自動聯絡家屬、警方或第三方。

對話紀錄分析先實現逐輪情緒、話題、文字摘要、回應來源、時間／時長和風險事件。日／週摘要由既有紀錄按需生成並緩存，保留引用輪次；不將 LLM 摘要當成新增事實。基本的每人 RAG／長期記憶列入無 ESP 的第一階段：以本地 embedding、SQLite metadata／向量及明確 owner scope 實作，先不引入大型向量資料庫；聲紋未確認或同意撤回時只能取共享資料，刪除後快取失效。這不等於醫療知識或無限制自動記憶，後續只在需要時擴充檢索規模。

## 10. 安全與隱私

- 重新產生曾經公開的 DeepSeek key；不要把 key 放入原始碼、前端、`.env` 提交內容、截圖或 Taskboard。
- 聲紋資料採最小化保存；刪除聲紋時清理模板、候選向量及識別快取，不再參與任何新比對。保留帳戶和歷史對話只在使用者選擇「僅刪聲紋」時進行，留下不含向量的管理操作紀錄。
- 完整刪除帳戶需明確確認，刪除其聲紋、個資、關聯對話、分析與風險資料；備份中的資料另外按保留策略到期。不得承諾僅刪本機檔案即可清除 OneDrive 歷史版本。
- Dashboard API 不回傳原始音訊、embedding、Authorization header 或完整 API key。
- 新的個資／聲紋功能在收集真人資料前即增加管理員登入、伺服器端授權、CSRF／Origin 驗證及上傳限制。管理 cookies 使用 HttpOnly、SameSite；HTTPS 時啟用 Secure。ESP token 與管理憑證分離，不能以「有 CORS」代替身份驗證。
- PC 初期只使用 `127.0.0.1`。LAN 部署須由管理員授權後啟用，使用 HTTPS 的 Dashboard 及特定裝置憑證／允許範圍；非 localhost 的 HTTP 頁面可能無法取得瀏覽器麥克風權限。
- 專案位於 OneDrive；`.gitignore` 不會阻止 OneDrive 同步。計劃將新增敏感執行資料放到 `C:/Users/gcgcg/AppData/Local/IoTGroup5`，並設帳戶 ACL；聲紋向量另以 DPAPI 加密。現有 SQLite 只在驗證備份及 migration 後切換，新程式不得默默建立空資料庫；不在本次文件階段搬移或刪除任何現有資料。
- 說話者向量和原始錄音不送 DeepSeek。對話前取得雲端文字處理同意；拒絕時只能使用本地規則及明示的本地回覆模式，不偷偷呼叫雲端。個資不必整包送進提示詞；只提供當輪必要上下文。
- 個人資料頁提供刪除／停用操作和操作紀錄。
- 不以聲紋結果單獨觸發高風險外部行動。

## 11. 測試與驗收

### 單元測試

- 使用者建立、更新、停用與刪除；
- 自述性別、年齡、同意狀態及語音轉錄後的人工確認；
- 聲紋樣本過短、無人聲、爆音和噪音拒絕；
- 重複註冊與重新註冊；
- 門檻接受、拒絕與 ambiguous；
- 風險 JSON schema 驗證、無法分析狀態及雲端故障仍保留本地警示；
- 工作逾時、取消、裝置 busy、ACK 不到與重複樣本；
- 刪除／重新登記後快取失效，原模板在新模板失敗時仍可用；
- API response 不含 key、embedding 或 raw audio。

### 整合測試

- 建立工作 → 個資確認 → samples → verify → complete → identify → `/api/audio/process` → conversation；
- 未知使用者仍可得到安全的 fallback 對話，但不得寫入已註冊使用者；
- ASR／聲紋／DeepSeek 任一服務失敗時，事件狀態可追蹤；
- 使用者頁能正確讀取情緒趨勢、對話和風險事件；
- 一台裝置 A 說話後 B 接著說話不讀取 A 的私密記憶，錯誤身份由管理員更正時能重算個人統計；
- migration 保留舊資料、今日／24 小時的實際時間過濾及訪客資料隔離；
- 以契約樣本測試 gateway 邊界；真正 ESP payload 與韌體指令必須等硬體到貨驗證。

### 人工驗收

- 建議五位組員中四位註冊、第五位先作未註冊測試，再輪替；至少兩位只能作功能 smoke test，不能宣稱準確率；
- 安靜、不同距離和輕微背景噪音；
- 每位使用者三至五段註冊樣本，另錄獨立驗證與測試語音，不用相同錄音同時校準和宣稱測試成績；
- 觀察誤認、拒識、ASR 文字、延遲和 Dashboard 更新；
- 確認刪除聲紋後不再成功匹配。

記錄 false accept（錯認）、false reject（拒識）、unknown、ambiguous 的實際次數與樣本數，並測試錄音重播風險。隨機句子只能降低簡單重播風險，不是防偽／活體檢測證明。人聲採集需本人操作麥克風；無法用自動化假音訊取代真人驗收。

## 12. 實作階段與完成條件

| 階段 | 交付內容 | 完成條件 |
|---|---|---|
| 1 使用者管理 | 資料備份／migration、安全儲存及登入、列表、資料頁、個資確認與聲紋狀態 | 新舊資料均可查閱；搜尋及個人路由可用；未授權不可存取真人資料；未實作卡片明示狀態 |
| 2 麥克風註冊 | 模型相容性核對、語音填個資、共用 TTS 提示與字幕、工作控制、品質檢查、enrollment | 可同意／拒絕／取消；三段樣本加獨立驗證後提交；模型 manifest 和實際推論通過 |
| 3 辨識與語音對話 | 身份與 ASR 並行、後端 TTS、PC 回覆播放、逐句字幕、2D 角色、打斷及事件 | 無需貼文字即可完成語音對話；不串帳；字幕跟隨播放；取消後舊音訊不復活；清楚顯示各階段失敗 |
| 4 個人分析及風險 | 個人趨勢、對話摘要、風險事件與人工確認 | 時間範圍正確；摘要附來源；雲端故障不清除警示；訪客事件獨立可見 |
| 5 ESP adapter | 授權裝置、註冊命令／ACK、MQTT／UDP gateway、TTS 音訊、字幕及本地角色素材 | 到貨後驗證收音、播放、真實螢幕字型／動畫、打斷、斷線、重連及不同麥克風的聲紋偏差 |

採用不同的小規格／實作計劃分別完成各階段，先交付可驗收的使用者模組，不把所有功能同時塞進 `services/dialogue/app.py`。ESP 接入後應用 ESP 麥克風補錄／重新驗證，PC 註冊效果不能當成真機識別效果。

每一階段都要先有測試，再加入功能；ESP 尚未到貨前只能宣稱 PC simulator 驗證完成，不能宣稱真機通訊完成。

### 12.1 程式責任劃分（未建立的檔案）

- `services/users/`：使用者 API、資料讀寫及 profile schema。
- `services/enrollment/`：註冊工作狀態、裝置指令、樣本及提交規則。
- `services/voiceprint/`：模型 provider、matcher、模板保護及 manifest。
- `services/audio/`：格式標準化、品質檢查、ASR 常駐模型與音訊流程。
- `services/tts/`、`services/device_gateway/` 與 `simulator/`：語音合成、音訊／字幕／表情事件映射及 PC 虛擬終端，詳補充規格。
- `services/analysis/`：情緒、結構化摘要、風險規則及事件。
- `services/security/`、`services/storage/`：管理授權、同意、migration 與敏感資料路徑。
- 現有 `services/dashboard/`：保留外觀，增加使用者列表／個人頁／註冊 wizard，修正狀態與時間統計語意。
- 現有 `scripts/`、`configs/`：沿用一鍵啟動，增加新模型／資料路徑檢查；不讓使用者額外手動開多個服務。
- `tests/users/`、`tests/enrollment/`、`tests/voiceprint/`、`tests/audio/`、`tests/security/`：各模組契約與回歸測試。

### 12.2 規格階段的交付邊界

本次只保存這份規格，不下載聲紋模型、不重啟服務、不搬移資料、不建立上述程式模組。使用者審閱規格後，下一步是按階段寫可執行計劃，再依已授權範圍實作與驗證。實際儲存庫目前不是 Git repository，這份文件僅保存在本地，未初始化 Git 或建立提交。

## 13. 不在本階段範圍

- 不直接部署上游 MySQL voiceprint-api；
- 不把 Dashboard 變成文字聊天客戶端；
- 不保存長期原始音訊資料；
- 不做醫療診斷、危機介入或自動聯絡第三方；
- 不在沒有實機的情況下宣稱 MQTT/UDP 已驗證。
