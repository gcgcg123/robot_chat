# 情感陪伴機器人：兩階段動工計劃

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task after authorization. Steps use checkbox (`- [ ]`) syntax for tracking. 本輪只制定計劃，不執行產品開發。

**Goal:** 先完成可用 PC 麥克風驗收的完整軟體系統，再於 ESP 到貨並確認硬體後，完成韌體與真機整合。

**Architecture:** laptop 共用一套身份、ASR、RAG、DeepSeek、TTS 及資料服務；PC 與 ESP 只是不同的語音輸入／輸出 adapter。Dashboard 是管理後台，PC simulator 是沒有 ESP 時的語音終端。先固定業務接口與事件契約，第二階段不重寫後端。

**Tech Stack:** Windows、既有 Python 3.13 / FastAPI / SQLite / 原生 HTML-CSS-JS、faster-whisper + Whisper large-v3-turbo CT2、DeepSeek 官方 API、CPU 聲紋／embedding／TTS；ESP 階段使用相符版本 ESP-IDF、小智 ESP32-S3 板級參考、LCD 顯示及 MQTT + UDP gateway。

**Spec:** [使用者與聲紋規格](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/specs/2026-09-13-user-registry-voiceprint-design.md)、[雙向語音與螢幕規格](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/specs/2026-09-13-esp-voice-display-design.md)。原規格的五項開發工作在本計劃重新歸入兩個總階段；基本 RAG／個人記憶前移至第一階段。

## Global Constraints

- 專案根目錄保持 `C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place`；本輪只新增／校正文檔，不改服務、模型、資料庫或韌體。
- Dashboard 是後台檢視與註冊入口，不提供瀏覽器文字聊天輸入框。
- Whisper 只負責 ASR；聲紋模型獨立負責說話者識別，兩者並行處理。
- 聲紋識別是個人化對話用途，不是門禁、金融或法定身份驗證。
- 性別與年齡由本人自述；年齡保存為 `age_at_registration`、`age_recorded_at`，允許不提供。
- 沿用已下載的 `models/asr/whisper-large-v3-turbo-ct2`，不重複下載完整版 Whisper。
- 管理員登入／授權、同意與敏感資料保護必須先於真人聲紋登記；API key、embedding、原始錄音不送前端或 DeepSeek。
- 執行期敏感資料計劃放到 `C:/Users/gcgcg/AppData/Local/IoTGroup5`；先備份及驗證 migration，再切換，不能默默建立空資料庫。
- PC 初期只使用 `127.0.0.1`。真人錄音須由本人允許麥克風；不得自動開啟麥克風或繞過瀏覽器權限。
- 註冊工作十分鐘到期、ready 等待最多三十秒；上傳单段最多六十秒、十 MB；有效聲紋人聲最低先定三秒。
- 原始 WAV 推論完成即清除，崩潰遺留最多一小時；另行同意的除錯保留預設關閉、TTL 二十四小時。
- UTC 保存時間，Dashboard 用 Asia/Hong_Kong 顯示；分頁預設 20、最多 100。
- 未知／模糊／辨識失敗的 `user_id = null`，不沿用上一人的資料或私人記憶；裝置在線不代表使用者在場。
- 首版半雙工、按鍵打斷、句級字幕、輕量 2D 角色；不承諾逐字對齊、全雙工 AEC、Live2D 或聲紋防重播。
- 緊急狀態是風險提示，不是醫療診斷；人工確認不等於事件解決，不自動聯絡任何第三方。
- MQTT + UDP 保留為真機驗收要求；PC WebSocket 或本機協議測試不等於 ESP 已通過。
- 目前專案不是 Git repository。執行時用版本化備份與工作紀錄保護變更，不自動初始化 Git、不宣稱已提交。

---

## 一、現在的起點

截至 2026-09-14 的程式檢查：現有一鍵啟動、Dashboard、SQLite、文字對話／音訊轉錄接口和模擬 heartbeat 可以沿用。**使用者完整註冊、真正聲紋辨識、PC 麥克風互動頁、後端 TTS、RAG、字幕角色和 ESP 韌體整合仍是本次待做工作。** 既有設計文件不代表功能已經完成。

先修正四項基礎問題：

1. 現有測試會經全域 app／啟動器子程序碰到預設資料庫；先隔離測試資料，再跑基準測試。
2. ASR 現在每次請求載入模型；改成常駐、有界佇列、非阻塞工作流程及可驗證的 CUDA → CPU int8 降級。
3. `/health` 的「有設定 key」不是模型可用證據；分開存活、就緒、模型實測和最近外部 API 狀態。
4. 修正「今日／近 24 小時」實際使用全量資料的統計，並防止 demo heartbeat 被當成真人身份。

## 二、第一階段：沒有 ESP，也先把完整軟體做完

詳細檔案、接口及測試步驟見 [第一階段執行清單](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/plans/2026-09-14-phase1-software.md)。以下是依賴順序，不是另外增加十個總階段。

| 順序 | 工作包 | 實際交付與驗收重點 |
|---|---|---|
| P1-01 | 安全基礎與測試隔離 | 測試不碰正式 DB、不消耗 DeepSeek；備份／migration／回復；登入、授權、同意、安全儲存路徑 |
| P1-02 | 使用者管理 | 使用者列表、搜尋／分頁、新增／編輯／停用／刪除；點擊進個人頁，未有資料的卡片明示空狀態 |
| P1-03 | 模型與 ASR 穩定化 | 重用 Turbo、常駐推論、GPU／CPU 降級、音訊格式轉換／品質檢查、真實 readiness／延遲 |
| P1-04 | 共用 TTS 與播放事件 | laptop 生成中文音訊；句子、音訊、字幕使用同一正文；私人音訊存取受限，失敗有明確狀態 |
| P1-05 | 聲紋與註冊服務 | CPU 聲紋模型、加密模板、三至五樣本、獨立驗證、取消／逾時／重登記、未知身份隔離 |
| P1-06 | PC 麥克風與註冊精靈 | 獨立 `/simulator`；授權 mic → 語音問個資 → 人工確認 → 樣本 → 驗證；半雙工真實播放 |
| P1-07 | DeepSeek、RAG 與個人記憶 | 官方 API、對話上下文、中文本地 embedding、共享知識與個人記憶隔離、來源可追溯、撤回／刪除生效 |
| P1-08 | 完整語音、字幕與角色 | mic → 身份／ASR → 分析／RAG／LLM → TTS → 聲音＋320×240 虛擬畫面；逐句字幕、停止及舊輪次失效 |
| P1-09 | 個人分析與風險後台 | 最近心情及時間、趨勢、對話／摘要／時長、裝置、告警與人工處理；分析失敗不能冒充正常 |
| P1-10 | 裝置契約、啟動器與總驗收 | PC／ESP 能力契約、MQTT／UDP 本機測試與故障注入、模擬標示、所有 runner 一鍵管理、操作／驗收手冊 |

### 第一階段完成後，你可以怎樣用

1. 雙擊「一鍵啟動」，登入 Dashboard。
2. 進「使用者」→「新增使用者」→選 PC 模擬裝置；使用者開啟模擬頁並允許 mic。
3. 系統用聲音問姓名、性別、年齡；你確認同音字／資料，再錄聲紋樣本及獨立驗證句。
4. 切換「對話測試」，直接對 PC mic 說話，不必貼文字。
5. 電腦真正播出機器人回覆，虛擬螢幕同步顯示當句字幕與表情。
6. Dashboard 查看這位使用者的對話、推估情緒、裝置、耗時和風險；換另一人或未知說話者，不應串帳。

### 第一階段明確驗收門檻

- [ ] 全套自動測試使用臨時資料／假 provider，正式資料不變；真人／真模型／計費測試另列清楚。
- [ ] 至少兩位自願參與者能完成 PC 註冊與相互切換；另有未註冊者拒識測試。兩人結果只是功能驗證，五人分組測試才用於初步誤認／拒識統計。
- [ ] 每位三至五段註冊音訊之外，另錄驗證、門檻校準及留出測試，不能用同一錄音證明準確度。
- [ ] 普通話、粵語、中英混合分項記錄；輸出語言按本機實際 TTS 能力驗證，不把繁體字幕當成粵語 TTS。
- [ ] 回覆由後端合成且實際播放；長字幕不溢出；中斷後舊音訊／字幕不復活。
- [ ] RAG 測試可指出引用文件／輪次，B／未知者不能取到 A 的私人記憶；刪除和撤回後不可再檢索。
- [ ] DeepSeek／聲紋／TTS 故障有分項狀態，本地 urgent 訊號不因雲端故障消失。
- [ ] 模擬裝置、原版無播放 ACK 的裝置均不虛報已播放；沒有 ESP 時真機狀態明示「未接入／未驗證」。
- [ ] 一鍵啟動／停止／開發模式可管理全部已安裝元件，重啟保留用戶與紀錄。

真人錄音與聽感驗收需要你／組員在場操作；我可以完成程式、測試和引導，但不能用生成錄音代替真人聲紋驗收。

## 三、第二階段：ESP 到貨後才動韌體與真機

詳細硬體門檻、韌體路徑及驗收見 [第二階段執行清單](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/plans/2026-09-14-phase2-esp.md)。啟動條件是你通知「ESP 已到貨，可以接入」，並提供 USB 連接與硬體確認所需資料。

| 順序 | 工作包 | 實際交付與驗收重點 |
|---|---|---|
| P2-01 | 實板辨識與回復準備 | 型號／revision、Flash／PSRAM、LCD、codec、mic／speaker、原理圖、USB 埠與電源；保全原韌體／回復方式 |
| P2-02 | 板級韌體與周邊點亮 | 基於相符的小智 DNESP32S3 target；關閉不需要的相機；先測啟動、LCD／繁中字型、mic、喇叭、按鍵 |
| P2-03 | 真實通訊 | Wi-Fi／受控 LAN、裝置憑證、MQTT 控制＋UDP 音訊；能力協商、序號／緩衝／斷線重連 |
| P2-04 | 完整語音與螢幕 | ESP 註冊 ready ACK、採音、辨識、回覆播放、字幕角色；播放 ACK、停止與舊資料丟棄 |
| P2-05 | 校準、穩定與展示 | 真 mic 補錄／聲紋校準、噪音距離測試、長時間運行、斷網／重啟、操作手冊及展示腳本 |

### 現在能確定與不能確定的硬體資訊

小智韌體已核對的來源 commit 為 `236deb266a88ade000cc85e20c0ccacc765e0ce3`，相符參考路徑是 `main/boards/alientek/atk-dnesp32s3/`。來源配置是 ST7789 320×240、ES8388，**硬體 I2S 輸入與輸出均為 24 kHz**；後端 ASR 另正規化成 16 kHz。來源同時呼叫相機初始化，不能假定無相機套裝可直接燒入原版。

以上是參考程式，不是你實物的測量結果。未確認商品 revision 前，不固定 GPIO 接線、COM 埠、Flash offset 或承諾既有二進位可用。mic、喇叭是否隨套裝附送也須核對；只有板與 LCD 不一定就是完整語音終端。

### 第二階段完成標準

- [ ] 不使用 PC mic，直接對 ESP 說話與註冊；laptop 後台記錄同一套 user / session / turn。
- [ ] ESP 喇叭真的播放 TTS，實體 LCD 的繁體字幕可讀、表情符合狀態，播放完成以 ACK 或實測證據確認。
- [ ] 網路實測證明 MQTT 控制與 UDP 音訊生效，不以 HTTP 模擬替代。
- [ ] 停止、斷線、重連與輪換說話者不重播舊話、不串身份；無法確認時以未知身份處理。
- [ ] 留有可復現的韌體版本、相符工具鏈、已驗證設定、燒錄／回復指令、接線圖和展示檢查表。

## 四、模型與服務資源安排

| 用途 | 方案 | 第一期執行時的行動 |
|---|---|---|
| ASR | 現有 Whisper large-v3-turbo CT2 + faster-whisper | 核對完整性／來源／實際 CUDA 推論，不重複下載；顯存不足或 CUDA 失敗再測 CPU int8 |
| LLM | DeepSeek 官方 API | 沿用可配置 base URL／model，現有設定是 `deepseek-chat`；依帳戶實際可用模型與官方文件核對，不自行寫成 v4-pro；key 使用安全設定 |
| 聲紋 | `speechbrain/spkrec-ecapa-voxceleb` 候選 | 授權／套件相容性核對後下載並做真 embedding；CPU 優先，必要時獨立 Python 3.11 runner |
| RAG embedding | `BAAI/bge-small-zh-v1.5` 候選 | 核對 model card／license／revision；CPU、小規模 SQLite metadata＋本地向量檢索，不另搭大型向量資料庫 |
| TTS | 優先 Windows 本地中文語音 | 列出實際 voice，確認能輸出音訊及中文聽感；不可用時評估 sherpa-onnx 的中文 VITS 本地 provider，以相容性及模型授權通過為採用門檻 |
| EdgeTTS | 非預設的線上備援 | 只有取得第三方文字傳輸同意、確認網路可用後才啟用，不把它宣稱為離線或在國內必定可用 |

ASR 優先使用 RTX 2060；聲紋、embedding、TTS 先放 CPU，降低顯存爭用。模型版本、來源、檔案 SHA-256、授權、實際 device／dtype／測試結果寫入 manifest。普通話輸出是首個 TTS 驗收項；若要求粵語但現有候選不足，要明示缺口並提出實測通過的聲音選項，不悄悄換語言。

**這份計劃沒有觸發任何模型下載或 API 呼叫。** 第一期開始後才執行相容性核對、必要下載及經同意的真人／雲端測試。

## 五、交付節奏與可維護性

第一階段依序給你四個可驗收版本：

- **A：可安全管理用戶**（P1-01～02）：登入、列表、個人頁、資料保護。
- **B：可用 mic 註冊**（P1-03～06）：ASR／TTS／聲紋、語音個資與獨立驗證。
- **C：可完整語音陪伴**（P1-07～09）：RAG、回覆播放、字幕角色、個人分析及風險。
- **D：可交接 ESP**（P1-10）：裝置契約與本機通訊測試、啟動器、整體驗收手冊。

新增模組按 users、enrollment、audio、voiceprint、tts、memory、analysis、gateway 分工，避免把所有功能塞進 `services/dialogue/app.py`。保留 BAT 啟動和開發模式；有新 runner 時由啟動器管理，不要求你手動開多個終端。每個工作包留下測試／變更紀錄；不將真實錄音、個資、密鑰放進版本化原始碼備份。

## 六、本次交付與下一步

本次僅交付這份總覽、兩份分階段清單及既有規格的必要校正；没有開始改程式、移動資料、安裝模型或刷機。

建議你確認本計劃後，先授權 **第一階段**，從 P1-01 安全基礎開始，沿 A → B → C → D 交付；這份計劃本身不當成實作授權。第二階段保留到 ESP 到貨後，由你通知再進行。執行可在本任務內按清單逐項推進；若你希望改為多代理分工，可在授權時明確指定。
