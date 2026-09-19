# ZIP 安裝與啟動修正（2026-09-15）

## 這次修正了甚麼

- 模型下載：直接執行實際的 hf.exe／huggingface-cli.exe 路徑，修正 FileInfo 沒有 Source 屬性導致的 BadExpression。中文及空格路徑已加入測試。
- 心跳：以本次 run_id 的成功回應確認就緒，最多等待 30 秒；舊的成功記錄不能代替本次啟動結果。
- 連線：localhost 心跳直接連接本機，不受 HTTP_PROXY 等環境代理設定干擾。連線故障及 HTTP 5xx 最多嘗試三次；401／403 不會被當成網路重試。
- 診斷：即時輸出心跳日誌，並記錄 UTC 時間、run_id、程序 ID、HTTP 狀態及退出碼，不輸出 token。
- 已有服務：再次啟動會檢查最新心跳回報；失效時只重啟受啟動器管理的心跳程序。

第二張截圖的歷史失敗原因無法由當時提供的成功日誌證實；本次加入上述可驗證的啟動條件與診斷，並保留既有本機憑證自動更新。

## 其他電腦如何更新

1. 關閉舊版本：執行原資料夾的一鍵停止.bat。
2. 下載最新 GitHub ZIP 並完整解壓縮；不要直接在壓縮檔中執行 BAT。
3. 在新資料夾執行一鍵安裝並啟動.bat。原始碼 ZIP 不包含模型、虛擬環境或密鑰；首次需要下載依賴及模型，並設定該電腦的 API key／Dashboard 密碼。
4. 模型可從同一台電腦的舊資料夾複製整個 models/asr/whisper-large-v3-turbo-ct2 資料夾至新版本，避免重新下載。不要只複製 model.bin，也不要跨電腦複製 .venv 或加密密鑰檔。
5. 安裝成功後，日常使用一鍵啟動.bat。

使用者資料預設仍保存在該電腦的 %LOCALAPPDATA%/IoTGroup5，與解壓縮資料夾分開。不要刪除該資料目錄。

## 再遇到錯誤要查看甚麼

請在失敗後、重新啟動之前保存以下檔案；重啟會更新日誌：

- logs/startup-error.json：啟動失敗原因、時間、run_id 及退出碼。
- logs/heartbeat-status.json：本次心跳 starting／retrying／ready／error 和 HTTP 狀態。
- logs/heartbeat-error.log、logs/heartbeat.log：即時程序訊息。
- logs/service-error.log、logs/service.log：後端啟動及請求記錄。

heartbeat-error.log 為空不一定是故障；正常時可為空。判斷就緒需看本次 heartbeat-status.json 的 ready 與服務請求是否成功。若 Python 甚至未能開始執行，則以 startup-error.json 的退出碼協助定位，不能一律歸因於 token。

## 驗證範圍

使用本機 Windows PowerShell、中文及空格路徑、無模型的 ZIP 形狀測試目錄，驗證兩種 CLI 的指令路徑及完整性檢查。下載測試使用小型可執行測試替身，沒有重新下載整個 ASR 權重；其他電腦的 Hugging Face 網路可達性仍需在該電腦實測。

心跳測試涵蓋正常回應、503 後恢復、401、環境代理干擾、舊就緒記錄、過期本機憑證、重複啟動及停止清理。
