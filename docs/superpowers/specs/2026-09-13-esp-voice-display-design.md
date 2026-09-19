> **⚠ 歷史規劃文件（2026-09-13）**：本檔是當時的規劃／規格紀錄，保留原樣以利追溯。
> 其中的個人路徑（`C:/Users/gcgcg/...`）指向撰寫當時的機器，**在本儲存庫無效**；
> 「目前不是 Git repository」「已下載的模型」等敘述也可能已經改變。
> 現況請看 [IMPLEMENTATION_STATUS.md](../../IMPLEMENTATION_STATUS.md)。

# ESP 雙向語音、字幕與機器人表情補充規格

**日期：** 2026-09-13  
**狀態：** 根據使用者補充需求修訂，待審閱；尚未實作  
**主規格：** [使用者管理與聲紋識別](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/specs/2026-09-13-user-registry-voiceprint-design.md)

## 1. 本次明確補充的目標

ESP 不只是麥克風上傳器，而是有麥克風、喇叭和螢幕的雙向語音終端。收到使用者對話後，laptop 產生回覆、合成語音並傳回 ESP；ESP 播放聲音，同時顯示對應字幕和機器人形象。TTS、播放、字幕與表情是核心驗收項，不能用 Dashboard 出現文字代替。

示例（回覆文字是設計示意，不是模型實測結果）：

1. 使用者說「我今天心情不太好」；裝置顯示正在聆聽和收音狀態。
2. ASR 完成後顯示使用者字幕；聲紋同步辨識，未知身份不沿用上一人的資料。
3. 生成回覆時顯示思考狀態；沒有足夠資訊時不把這句話直接標成緊急事件。
4. 機器人說「聽起來今天不太順利。你願意跟我說說發生了甚麼嗎？」；螢幕依播放句子更新字幕，角色呈現溫和、專注的表情。
5. 播放完成後才結束說話動畫，回到可再次收音的狀態；Dashboard 保存身份、文字、分析和實際播放狀態。

「使用者情緒」與「機器人表情」獨立保存：使用者難過時，機器人宜顯示關心，不必模仿哭泣，更不能因預設開心表情而出現不合情境的大笑。

## 2. 已核對的小智參考內容

本地 server 來源是使用者下載的 `xiaozhi-esp32-server-main`，未將其視為 GitHub 最新 server 版本。另於本次線上讀取 `78/xiaozhi-esp32` 韌體，核對 commit `236deb266a88ade000cc85e20c0ccacc765e0ce3` 的顯示處理；它不是目前已燒入使用者 ESP 的版本。

| 來源 | 核對到的行為 | 本專案參考方式 |
|---|---|---|
| server `core/handle/sendAudioHandle.py` | `stt`、`tts.start`、`tts.sentence_start`、`tts.stop`，以及分包 Opus 音訊 | 參考語音／字幕回傳與發送節奏，不宣稱已有逐字對齊 |
| server `core/utils/textUtils.py` | 發出含 `emotion` 的 `llm` 訊息 | 採白名單表情映射；不直接沿用無標記即 happy 的預設 |
| server `core/handle/abortHandle.py` | 中斷旗標、清理佇列、通知停止 | 參考打斷語音的取消路徑 |
| server `core/handle/helloHandle.py` | 讀取 audio_params 與 features | 協商音訊與裝置能力，不硬套所有硬體一種格式 |
| server `main/digital-human` | PC 網頁測試、TTS/STT 接收與 Live2D 動作 | 參考 PC 模擬器的事件處理，不搬入文字聊天框或相機功能 |
| ESP 韌體 `main/application.cc` | `stt` 顯示 user 文字、`sentence_start` 顯示 assistant 文字、`llm.emotion` 呼叫 SetEmotion、接收音訊進解碼佇列 | 韌體字幕與表情整合依據 |
| ESP 韌體 `main/display/` | LCD／OLED／LVGL 顯示實作及表情資源結構 | 選擇與真實開發板及螢幕相符的顯示 adapter |

可追溯來源：

- [本地語音回傳程式](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/xiaozhi-esp32-server-main/xiaozhi-esp32-server-main/main/xiaozhi-server/core/handle/sendAudioHandle.py)
- [本地表情事件程式](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/xiaozhi-esp32-server-main/xiaozhi-esp32-server-main/main/xiaozhi-server/core/utils/textUtils.py)
- [本地 PC 數位人模組](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/xiaozhi-esp32-server-main/xiaozhi-esp32-server-main/main/digital-human/README.md)
- [ESP 韌體訊息處理](https://github.com/78/xiaozhi-esp32/blob/236deb266a88ade000cc85e20c0ccacc765e0ce3/main/application.cc)
- [ESP WebSocket 協議](https://github.com/78/xiaozhi-esp32/blob/236deb266a88ade000cc85e20c0ccacc765e0ce3/docs/websocket_zh.md)
- [ESP MQTT／UDP 協議](https://github.com/78/xiaozhi-esp32/blob/236deb266a88ade000cc85e20c0ccacc765e0ce3/docs/mqtt-udp_zh.md)（後續真機接入時再逐項核對）

server 根 LICENSE 是 MIT，但這不表示 Live2D SDK、人物模型、字型、GIF 等所有附帶素材都可以任意搬用。採用資源前逐項核對授權與大小；不把大體積網頁 Live2D 移植到 ESP，也不將整張圖片逐幀經 MQTT 傳送。

## 3. 分工與方案選擇

- **laptop**：身份與 ASR、同意範圍內的 DeepSeek 呼叫、文字整理／斷句、TTS、回傳事件、資料記錄；沿用主規格的個人資料隔離。
- **ESP**：收音及編碼、解碼播放、播放緩衝、畫面排版、字幕更新、角色動畫、按鍵／喚醒與網路狀態；無需在 ESP 跑 Whisper 或 LLM。
- **Dashboard**：註冊、個人資料、裝置與播放健康資訊；不是使用者對話前端。
- **PC 模擬器**：PC 麥克風、喇叭／耳機及虛擬小螢幕，代替 ESP 的輸入與輸出，不只是錄音上傳工具。

推薦採用「後端共用 TTS + 輕量 2D 裝置角色」。只有瀏覽器 speechSynthesis 的方案較快，但無法證明 ESP 所需的音訊下行可用，只能作 UI 初步測試；全套 Live2D 適合 PC 展示，不能作 ESP 第一版能力承諾。

## 4. TTS 與真正的語音回傳

Whisper 將聲音轉文字，DeepSeek 在本方案生成文字，還需要第三項 TTS（文字轉語音）。不假定 DeepSeek 的文字 API 會直接返回可播放音訊。

1. 首版優先評估 laptop 的 Windows 本地中文 TTS 作 CPU provider，生成伺服器端 WAV／PCM，不新增 GPU 大模型。實作時必須列出實際可用語音並做中文合成／播放檢查，沒有語音包不能顯示已就緒。
2. 上游 EdgeTTS 可作另一個候選 provider，但它依賴外部網路，不是離線模型，也不能假定使用者所在地一定可連；若啟用須取得第三方 TTS 文字傳輸同意。語音不合適時才進一步選本地中文 TTS 模型，不在本次規格階段下載。
3. 普通話、粵語與中英混合的輸出能力要分開確認；繁體字幕不代表語音已是粵語。聲音、語速、音量由設定管理；不使用真人聲紋去做聲音克隆。
4. LLM 的可朗讀正文整理後，以完整句子作合成單位。第一版可在完整文字返回後逐句合成／下發；下一步再讓 LLM 流式文字累積成可提交的句子，避免每個 token 都呼叫 TTS。
5. 已提交朗讀的句子不再事後改寫；字幕與 TTS 共用同一份 speech_text，Markdown、emoji 控制標記及不可朗讀資料先移除。必要的風險／輸出檢查在提交朗讀前執行，不能播完後才假裝可撤回。
6. PC 播放後端產出的句子音訊，ESP 由 adapter 重採樣／Opus 編碼後回傳；同一段回覆只合成一次，不讓 PC 與 ESP 各跑一套不同的 TTS 邏輯。
7. TTS 失敗時畫面保留文字並標記「語音暫不可用」，不能記成已播放。離線提示可用隨程式分發的固定短音訊；包含私人對話的 TTS 暫存不作長期公共資源。

## 5. 字幕流與播放同步

首版的「字幕流」定義是隨音訊逐句顯示，不是打字機特效或逐字卡拉 OK。

- 使用者字幕：Whisper 完成一次有效語段後顯示最終結果。現有 Whisper Turbo 推論不等於已提供真正的逐字即時 ASR；增量 ASR 另作優化。
- 機器人字幕：每個已合成句子與其音訊綁定，依序呈現；長句按畫面分行或分頁，支援繁體中文、標點、英文，避免溢出。
- PC 模擬器依實際音訊播放開始切換當句字幕，不在 LLM 文字剛抵達時一次顯示整段。
- 小智原版以 sentence_start 到達時更新畫面，屬句級事件近似同步。若要精準跟隨裝置播放時鐘，需要擴充韌體的句子佇列／播放回報；不能只加後端 JSON 欄位就聲稱原版支援。
- 逐字高亮需要 TTS 詞級時間戳或額外 forced alignment；沒有此能力就不顯示偽造時間軸。首版不承諾逐字對齊。
- 正常完成時先讓播放佇列播完再回到 idle；使用者打斷時才立刻取消並清除尚未播放的資料。不能把「伺服器發完」當成「使用者聽完」。

## 6. 螢幕與角色

畫面分為頂部狀態區、中央機器人圖像、底部字幕區；實際比例、字數及動畫幀率依螢幕尺寸和記憶體測量後決定。

| 裝置狀態／情境 | 畫面與聲音行為 |
|---|---|
| idle | 中性待機角色、可說話提示，閒置後隱藏敏感字幕 |
| listening | 聆聽姿態、收音標記；有音量數據時才顯示真實音量條 |
| thinking | 思考表情、處理中提示，不假裝正在播放 |
| speaking | 當句字幕、說話動作；口部張合可依本地播放振幅，不宣稱音素級唇形 |
| 關懷回應 | 溫和／專注表情，不用使用者的負面標籤直接選哭泣動畫 |
| enrollment | 顯示朗讀句子、步驟和已通過樣本數，播放提示後再開始錄音 |
| interrupted / disconnected / error | 停止過期動畫與字幕更新，顯示可理解的重試提示 |

內部 robot_expression 白名單為 neutral、listening、thinking、speaking、caring、happy、error；這是本專案的表現層語意，不宣稱是原版小智所有合法 emotion 名稱。adapter 映射到裝置資源可支持的名稱，沒有 caring 等素材時用 neutral，未知值也回到 neutral。

角色圖片／有限幀動畫優先預先放在 ESP Flash／適合的資源分區，對話期間只傳表情 ID。彩色 TFT 可評估小型 sprite／GIF，單色 OLED 先用單色表情；字型需覆蓋繁體中文字，記憶體不足時以實測選取字集或相容的字形機制。不以 PC 頁面畫得出動畫當成 ESP 幀率／記憶體已驗證。

## 7. 協議與取消機制

保留 `POST /api/audio/process` 作單輪處理／測試入口；新增專門的即時事件 adapter 將相同處理流程輸出到 PC 或 ESP。Dashboard 列表仍可輪詢，但音訊與字幕不靠 Dashboard 的定時刷新傳送。

- PC 首版採按鍵錄音／語段上傳，透過授權的 WebSocket 事件通道接收字幕、狀態與音訊段通知；MediaRecorder 的 WebM container 不能直接當作小智 Opus packet。
- PC 音訊資源只允許所屬會話讀取、短期有效、no-store；不把私人語音放到靜態公開網址。WS 同樣驗證登入／Origin／裝置權限。
- ESP 參考 Xiaozhi 的 WS 或 MQTT 控制 + UDP 音訊協議，保留使用者要求的 MQTT／UDP 真機驗收；PC WS 是先行測試，不是取消 MQTT／UDP。
- 參考上游 gateway 文件：ESP 的 MQTT／UDP 經 gateway 接到 Xiaozhi WebSocket。不能把 ESP 直接指向現在的 HTTP 上傳端點就稱為協議相容。
- 每輪只由一個主流程執行身份、ASR、LLM、TTS；接入上游通訊層時不能同時觸發兩套完整流程而重複回覆或計費。
- 音訊格式以 hello 協商為準，上傳 ASR 正規化是 16 kHz 單聲道，不代表下行 TTS 永遠相同取樣率。60 ms Opus frame 是已讀上游的參考值，不是所有硬體強制值。

本專案內部事件均攜帶 session_id、turn_id、event_id；句子事件另有 segment_id 和 sequence。這些追蹤欄位不能當成原版韌體已支援的擴充。相容 adapter 映射到 stt／tts／llm 及音訊封包；需要播放 ACK 或更細粒度順序時，先做能力協商及韌體修改。

打斷首版以按鍵／停止鈕實作：裝置先停止本地播放，後端取消當輪 LLM／TTS、清空佇列並讓舊輪次事件失效。所有延遲返回音訊和表情都檢查輪次，不能在新使用者發話後繼續播舊答案。正常結束與 abort 不共用無條件清空緩衝的實作。

初期採半雙工，播放提示／回答時不將喇叭聲送入 ASR 或聲紋。自然插話／全雙工需要另外驗證 AEC、VAD 與硬體回授，不能只開啟麥克風就宣稱支持。UDP 接入要測遺失、亂序、重複包及有限緩衝；控制訊息和音訊跨通道不能假定天然同步。

## 8. PC 模擬器與 Dashboard 改動

在獨立 `/simulator` 裝置測試頁提供：啟動／停止、PC mic 選擇、音量、虛擬螢幕、字幕、角色、打斷按鈕和各階段耗時；不提供文字聊天 composer。Dashboard 只提供進入測試頁與觸發授權註冊工作的入口。

註冊工作沿用主規格 enrollment_id；語音詢問姓名／性別／年齡時，裝置顯示問題並朗讀，回覆經 ASR 填到管理表單，由本人／管理員確認。註冊與一般對話不混入統計。

裝置頁新增 TTS provider、音訊輸入／輸出狀態、取樣率、display 能力、目前 turn_id、字幕句次、表情名稱、最近錯誤。記錄 generated、sent、playback_started、playback_completed、interrupted、failed；原版 ESP 無 ACK 時只能標成已發送／完成播放未確認，不虛報「播放成功」。無法取得的電量／屏幕資訊顯示不支援，不產生假數值。

新增職責為 `services/tts/`（TTS provider 與句子音訊）、`services/device_gateway/`（訊息映射）、`simulator/`（PC 裝置頁與播放狀態），以及對應單元／契約測試。這些是規劃中的位置，本次沒有建立程式模組。

## 9. 開發順序與驗收

主規格階段 1 的使用者列表／個人頁保持優先。階段 2 先建立共用 TTS provider，供註冊語音提示使用；階段 3 沿用它擴充一般對話回傳，拆成以下可獨立驗收的工作：

1. **TTS 下行**：後端將測試句生成音訊，PC 真正播出；檢查語言、語速、格式及無可用語音的錯誤。
2. **虛擬螢幕**：相同句子音訊帶動字幕／2D 角色；驗證多句、長句、繁體字、emoji 過濾。
3. **完整語音對話**：PC 收音 → 身份／ASR → DeepSeek → TTS → 播放／字幕／表情；無需手動貼文字。
4. **取消與異常**：第一句播放中按停止；後續音訊／字幕不得復活；測試斷線、TTS 失敗、下一位說話者接續。
5. **監控與隱私**：Dashboard 區分已生成／已發送／已播放，未知身份不讀他人記錄，字幕閒置可清除，私人音訊不公開。

階段 5 才是 ESP 真機：確認板型／麥克風／喇叭／螢幕 → 選 firmware/display adapter → MQTT／UDP 連線 → 收音與回放 → 字幕與資源 → 打斷／重連／聲紋再驗證。先保留 WS 診斷備援，但最終按專案要求驗收 MQTT／UDP。

真人測試至少包含「我今天心情不太好」、較長回覆、粵語／普通話實際需求、背景噪音、音量很低和播放中打斷。記錄首段可播放時間、句間停頓、字幕對播放的偏移與掉音次數；在尚未實測前不宣稱零延遲或精確唇形。

## 10. 硬體資訊與未驗證事項

使用者目前提供的套裝是「DNESP32S3 開發板 + 2.4 吋 LCD 模塊」。線上核對的小智韌體在 `main/boards/alientek/atk-dnesp32s3` 有相符的板級目標；該參考配置包含：ESP32-S3、ST7789、320×240 彩色 LCD、ES8388 音訊 codec、**硬體 I2S 輸入與輸出均為 24 kHz**、Boot 按鍵，以及可選 OV2640 DVP 攝像頭。後端 ASR 仍會把上傳音訊正規化為 16 kHz mono；兩個取樣率是不同邊界。參考程式的 LCD 腳位是 SCLK 12、MOSI 11、DC 40、CS 21；音訊 codec I2C 是 SDA 41、SCL 42；這些數字只適用該參考板檔案，不能直接當成使用者手上板子的接線證明。

因此暫定硬體策略如下：

- 第一版顯示以 320×240、ST7789、LVGL／SpiLcdDisplay 為目標；角色使用小型預存 2D sprite／有限幀動畫，字幕區預留約三至四行繁體中文，實際字體大小以 Flash／PSRAM 測試決定。
- 語音回傳以板上 ES8388 或賣家實際 codec 為目標，先確認喇叭、麥克風、I2S 腳位、輸入／輸出採樣率和是否有 AEC；沒有確認前，PC 虛擬裝置仍是唯一可宣稱的端到端測試平台。
- 2.4 吋 LCD 若是獨立模組，需確認 SPI 電壓、背光供電、觸控控制器及排線方向；不要因尺寸相同就套用 ST7789 或參考 GPIO。
- 攝像頭不是目前情感對話必要元件；除非使用者明確要影像功能，先不把 OV2640、相機權限和影像上傳納入第一版，以免與語音、字幕和聲紋爭用 PSRAM／頻寬。

燒錄前必須從 PCB 絲印、賣家原理圖或商品詳細頁確認：完整型號／revision、Flash、PSRAM、LCD 控制器與解析度、觸控、音訊 codec、麥克風／喇叭型號、I2S／SPI／I2C 腳位、電源電壓、按鍵及韌體啟動方式。不能僅因「有螢幕」就假定支持彩色動畫或特定 LVGL 驅動，也不能把網路圖片當成實際硬體規格。

本次只讀取原始碼與更新規格，沒有啟動上游 digital-human、安裝 TTS、下載圖像／模型、燒錄 ESP 或更改正在運行的服務。軟體第一階段的基本 RAG／個人記憶不依賴 ESP，依兩階段計劃先在 PC simulator 實作；真機硬體工作則等 ESP 到貨後進入第二階段。
