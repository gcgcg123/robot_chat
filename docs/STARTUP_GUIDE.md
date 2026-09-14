# 情感陪伴機器人一鍵啟動指南

專案位置：

```text
C:\Users\gcgcg\OneDrive\Desktop\IoT_group5\project_place
```

## 第一次使用

1. 在 DeepSeek 控制台撤銷曾經貼到聊天或 `cmd.txt` 的舊 key，建立一個新 key。
2. 雙擊專案根目錄的 `首次設定.bat`。
3. 在提示出現後貼上新 DeepSeek API key，再按 Enter。

輸入內容不會顯示。key 會使用 Windows DPAPI 加密後保存到：

```text
data\secrets\deepseek.key
```

這個加密檔只能由目前的 Windows 使用者帳戶解密，也已被 `.gitignore` 排除。請勿再把 key 寫入 `cmd.txt`、`.env`、程式碼或 Git。

### ZIP 使用者的一鍵方式

從 GitHub 下載 ZIP 並解壓後，直接雙擊根目錄的 `一鍵安裝並啟動.bat`。腳本會自動建立 `.venv`、安裝依賴、下載缺少的 Xiaozhi 參考程式與 ASR 模型，然後執行首次安全設定和 simulator token provision。這需要 Python 3.10 或更新版本、Git 和網路連線。

### Docker 方式

安裝 Docker Desktop 後執行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-project.ps1 -Docker
```

第一次執行會建立未納入 Git 的 `.env.docker`；填入 `IOT_ADMIN_PASSWORD`（DeepSeek key 可留空，使用 fallback）後再執行一次。Docker 會使用 `iotgroup5-data` volume 保存執行資料，並以唯讀方式掛載本機 `models` 目錄。

## 平常一鍵啟動

雙擊：

```text
一鍵啟動.bat
```

啟動器會自動完成：

1. 讀取並解密 DeepSeek key。
2. 啟動 FastAPI 後端。
3. 等待 `/health` 通過。
4. 若已提供 simulator device token，啟動 heartbeat，暫時代替尚未到貨的 ESP。
5. 自動打開 `http://127.0.0.1:8080/dashboard`。

後端與 heartbeat 會在背景執行，不需要保留 PowerShell 視窗。

### 一鍵啟動的驗收前提

一鍵啟動會啟動可供 PC 驗收的後端與 simulator heartbeat。要看到受保護的 Dashboard 資料，至少要先配置：

- `IOT_ADMIN_PASSWORD`：Dashboard 管理員登入密碼；未配置時 `/api/auth/login` 會回傳 `503 admin_auth_not_configured`。
- simulator 的 device token：heartbeat 路由要求綁定 token 與 CSRF；未傳 `-DeviceToken` 時 heartbeat 會以 `401 Unauthorized` 結束，不能把裝置誤判為 online。

首次設定會保存管理員密碼；啟動器不會自動登入 Dashboard 或把 token 寫入程式碼。登入 API 是 `POST /api/auth/login`，成功後才能讀取 `/api/dashboard/summary`、`/api/devices`、`/api/conversations` 與 `/api/users`。若要顯示 simulator online，先執行：

```powershell
.\scripts\provision-simulator.ps1 -DataDir "$env:LOCALAPPDATA\IoTGroup5" -DeviceId sim-device
```

上述前提完成後，Dashboard 與 `/simulator` 可直接做 PC 驗收。模擬器會先同步 session/CSRF，再以瀏覽器 PCM/WAV 錄音送往 `/api/transcribe`；完成三段樣本後即可測試 WebSocket 字幕與 320×240 模擬 LCD。中文 TTS 聽感、聲紋 FA/FR、DeepSeek 外部請求與 ESP 硬體仍是後續人工驗收項目。

## 修改 Project 時

雙擊：

```text
開發模式.bat
```

開發模式會使用 `uvicorn --reload`。修改 Python 檔案後，後端會自動重新載入。修改 HTML、CSS 或 JavaScript 後，只需要重新整理 Dashboard。

常用修改位置：

```text
services\dialogue\app.py
services\dashboard\index.html
services\dashboard\dashboard.css
services\dashboard\dashboard.js
services\dashboard\read_model.py
```

## 一鍵停止

雙擊：

```text
一鍵停止.bat
```

它只會停止由本專案啟動器記錄的 FastAPI 和 heartbeat 程序，不會掃描或停止其他 Python 專案。

## 修改啟動設定

編輯：

```text
configs\launcher.json
```

內容：

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "open_browser": true,
  "heartbeat_enabled": true,
  "heartbeat_interval_seconds": 10,
  "simulator_device_id": "sim-device",
  "deepseek_base_url": "https://api.deepseek.com",
  "deepseek_model": "deepseek-chat"
}
```

- `port`：Dashboard 和 API 使用的埠。
- `open_browser`：啟動後是否自動打開 Dashboard。
- `heartbeat_enabled`：ESP 未到貨前是否啟動模擬裝置。
- `heartbeat_interval_seconds`：模擬裝置多久上報一次。
- `simulator_device_id`：Dashboard 顯示的模擬裝置 ID。
- `deepseek_model`：DeepSeek API 使用的正式模型 ID。

## 執行資料位置

```text
runtime\     啟動器記錄的程序 ownership state（PID、建立時間、命令與 run id）
logs\        後端與 heartbeat 日誌
data\secrets\ 加密的 DeepSeek key
```

以上目錄均不應提交到 Git。

## 常見問題

### 雙擊後提示找不到虛擬環境

先執行 `首次設定.bat`。它會檢查虛擬環境並安裝 `requirements.txt`。

### 8080 已被使用

先雙擊 `一鍵停止.bat`。若仍被占用，可修改 `configs\launcher.json` 的 `port`，例如改成 `8081`。

### Dashboard 顯示裝置離線

重新雙擊 `一鍵啟動.bat`。heartbeat 狀態規則：60 秒內為 online、60–300 秒為 stale、超過 300 秒為 offline。

### 要查看錯誤

查看：

```text
logs\service-error.log
logs\heartbeat-error.log
```

或使用 `開發模式.bat` 查看即時後端輸出。

## 手動啟動備援

一鍵工具無法使用時才需要：

```powershell
cd C:\Users\gcgcg\OneDrive\Desktop\IoT_group5\project_place
.\.venv\Scripts\Activate.ps1
$env:DEEPSEEK_API_KEY = "<新 key>"
uvicorn services.dialogue.app:app --host 127.0.0.1 --port 8080
```
