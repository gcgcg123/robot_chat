# 修復與驗收執行方案（2026-09-15）

> 來源：2026-09-15 對 `E:\project\robot_chat` 的程式碼／腳本／文件／執行時審查，含一次真實啟動冒煙測試（登入 → 對話 → 風險事件落庫 → Dashboard）。
> 適用分支：`lqq`（執行前有 5 個未提交的 `scripts/*.ps1` 改動與未跟蹤的 `.claude/`）。
> 執行方式：依 A → B → C →（決策點）D → E → F 順序推進。每個任務都是「改完就能自證」的獨立單元，建議一個任務一個 commit。

## 摘要（TL;DR）

| 階段 | 內容 | 任務 | 預估工時 | 風險 |
|---|---|---|---|---|
| A | 讓專案可重現（測試依賴、啟動腳本去硬編碼、文件同步） | A1–A3 | 1.5 h | 低 |
| B | 修掉會誤導人的正確性缺陷（Dashboard 欄位／時間窗、死碼、登入比較、心跳代理） | B1–B5 | 1.5 h | 低 |
| C | 安全加固（聲紋模板加密、金鑰管理、明文 key 退役、`.claude` 忽略） | C1–C3 | 2.5 h | 中 |
| D | 把「聲紋」從演示接成真實鏈路（需先做決策） | D1–D3 | 3 h ～ 2 d | 高 |
| E | 執行健壯性（SQLite WAL、TTS 明確化、錄音現代化） | E1–E3 | 0.5 h ～ 4 h | 中 |
| F | 驗收與交付 | F1–F3 | 1 h | 低 |

**相依關係**：A1 → 其餘所有驗證；A2 → 一鍵啟動相關驗收；C1 → D1（聲紋鏈路必須先有可信的金鑰/信封）；D1 → D2；B/C 與 D 可並行。

---

## 0. 執行前準備（一次性，5 分鐘）

### 0.1 固化現有未提交改動

先確認目前工作區狀態，再決定要不要開分支：

```powershell
git -C E:\project\robot_chat status --short
git -C E:\project\robot_chat switch -c codex/hardening-2026-09-15
git -C E:\project\robot_chat add scripts
git -C E:\project\robot_chat commit -m "chore: snapshot local launcher changes before hardening"
```

注意：**不要**使用 `git add -A`。未跟蹤的 `.claude/` 要先做完 C3（加入 `.gitignore`）再決定是否提交。

### 0.2 驗收環境注意事項

`tests/test_project_launcher.py` 與所有使用 `tmp_path` 的用例需要能寫 `%TEMP%` 並啟動 PowerShell 子行程。在受限沙箱（例如 Codex 工具沙箱）中會出現 `PermissionError: [WinError 5]` 與啟動器測試失敗，**這是環境限制，不是程式缺陷**。請在一般 PowerShell 視窗執行 F 階段的驗收命令。

### 0.3 統一變數（後續命令都用到）

```powershell
$py = 'D:\ProgramData\Anaconda_envs\envs\robot_chat\python.exe'
$proj = 'E:\project\robot_chat'
```

---

## 階段 A：讓專案可重現

### A1｜補齊開發依賴，讓「測試全綠」這句話可重現

**問題**：專案環境沒有安裝 `pytest`，`requirements.txt` 也未列出；`docs/IMPLEMENTATION_STATUS.md` 聲稱「71 passed」，在乾淨環境無法重現。

**涉及檔案**：`requirements-dev.txt`（新建）、`.github/workflows/ci.yml`

**步驟**

1. 新建 `requirements-dev.txt`：

```
-r requirements.txt
pytest>=8.2,<9
```

2. 修改 `.github/workflows/ci.yml` 的安裝步驟：

```yaml
      - name: Install dependencies
        run: python -m pip install -r requirements-dev.txt
```

3. 安裝並跑基線（需要網路）：

```powershell
& $py -m pip install -r "$proj\requirements-dev.txt"
Set-Location $proj
& $py -m pytest tests -q
```

**完成判據**

- `pytest` 能被匯入，且 `python -m pytest tests -q` 有明確的 passed/failed 統計。
- 在一般 PowerShell 環境應為全綠（目前測試總數 87）。若仍有失敗，先記錄測試名稱與完整錯誤，再進入 A2，不要先改業務程式碼。

**回滾**：刪除 `requirements-dev.txt` 並還原 `ci.yml`。

---

### A2｜啟動腳本去除硬編碼解譯器路徑

**問題**：5 個腳本把解譯器寫死為 `D:\ProgramData\Anaconda_envs\envs\robot_chat\python.exe`，讓 README 承諾的「ZIP 解壓即用／自動建立 `.venv`」失效，換機或換目錄即丟例外。

**涉及檔案**：`scripts/launcher-common.ps1`、`scripts/bootstrap-project.ps1`、`scripts/setup-project.ps1`、`scripts/start-project.ps1`、`scripts/provision-simulator.ps1`、`scripts/download-model.ps1`、`configs/launcher.json`

**步驟**

1. 在 `scripts/launcher-common.ps1` 末尾新增解析函式（解析優先序：環境變數 → 設定檔 → 專案 `.venv` → PATH 上的 `python`）：

```powershell
function Resolve-ProjectPython {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [switch]$Quiet
    )
    $candidates = @()
    if ($env:IOT_PYTHON) { $candidates += $env:IOT_PYTHON }
    $configPath = Join-Path $ProjectRoot "configs\launcher.json"
    if (Test-Path -LiteralPath $configPath) {
        $configured = [string](Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).python
        if (-not [string]::IsNullOrWhiteSpace($configured)) {
            $candidates += $(if ([System.IO.Path]::IsPathRooted($configured)) { $configured } else { Join-Path $ProjectRoot $configured })
        }
    }
    $candidates += (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    if ($Quiet) { return $null }
    throw "No Python interpreter found. Run the first-time setup, set IOT_PYTHON, or fill configs/launcher.json."
}
```

2. `configs/launcher.json` 增加欄位（保留你本機的 Anaconda 路徑作為「設定」而非「硬編碼」）：

```json
{
  "host": "127.0.0.1",
  "port": 8080,
  "open_browser": true,
  "heartbeat_enabled": true,
  "heartbeat_interval_seconds": 10,
  "simulator_device_id": "sim-device",
  "deepseek_base_url": "https://api.deepseek.com",
  "deepseek_model": "deepseek-chat",
  "python": ""
}
```

（把 `python` 設為 `D:\\ProgramData\\Anaconda_envs\\envs\\robot_chat\\python.exe` 可保留你目前的環境；留空則自動找 `.venv` 或 PATH。）

3. 各腳本改為呼叫解析函式：

| 檔案 | 原本 | 改為 |
|---|---|---|
| `scripts/start-project.ps1:28` | `$venvPython = "D:\...\python.exe"` | `$venvPython = Resolve-ProjectPython -ProjectRoot $projectRoot` |
| `scripts/setup-project.ps1:22` | 同上 | 同上（在 `-SkipInstall` 分支內） |
| `scripts/provision-simulator.ps1:16` | 同上 | 同上 |
| `scripts/bootstrap-project.ps1:27` | `$python = "D:\..."` | `$python = Resolve-ProjectPython -ProjectRoot $projectRoot` |
| `scripts/download-model.ps1:13-14` | `$localHf` / `$localLegacy` 硬編碼 | 見下方片段 |

`bootstrap-project.ps1` 目前**沒有** dot-source 共用函式，需在 `$projectRoot = Split-Path -Parent $PSScriptRoot` 之後補一行：

```powershell
. (Join-Path $PSScriptRoot "launcher-common.ps1")
```

`download-model.ps1` 改用解譯器所在環境目錄推導 CLI 位置，並保留 PATH 回退：

```powershell
$python = Resolve-ProjectPython -ProjectRoot $projectRoot
$envDir = Split-Path -Parent $python
$candidates = @(
    (Join-Path $envDir "Scripts\hf.exe"),
    (Join-Path $envDir "Scripts\huggingface-cli.exe")
)
$cli = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $cli) {
    $command = Get-Command hf -ErrorAction SilentlyContinue
    if (-not $command) { $command = Get-Command huggingface-cli -ErrorAction SilentlyContinue }
    if ($command) { $cli = $command.Source }
}
if (-not $cli) { throw "Hugging Face CLI is required. Install it with: python -m pip install huggingface_hub" }
& $cli download $RepoId --local-dir $TargetDir
```

4. `start-project.ps1` 的 `-CheckOnly` 分支改為不拋例外地回報：

```powershell
$resolved = Resolve-ProjectPython -ProjectRoot $projectRoot -Quiet
python_ready = [bool]$resolved
```

（`tests/test_project_launcher.py:143` 斷言 `report["python_ready"] is True`，語意保持「找到可用解譯器」。）

**完成判據**

```powershell
Set-Location $proj
& $py -m pytest tests/test_project_launcher.py -q
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-project.ps1 -CheckOnly
```

- 啟動器測試全綠；`-CheckOnly` 輸出 JSON 且 `python_ready` 為 `true`。
- 把 `configs/launcher.json` 的 `python` 清空、環境變數也不設時，仍能透過 `.venv` 或 PATH 找到解譯器（可在一台乾淨機器或新目錄驗證 ZIP 流程）。

**回滾**：`git revert` 該 commit；每個腳本改動彼此獨立。

---

### A3｜文件同步（消除漂移）

**涉及檔案**：`README.md`、`docs/IMPLEMENTATION_STATUS.md`、`docs/STARTUP_GUIDE.md`（如需）

**步驟**

1. `README.md:28` 的手動啟動範例把別台機器的舊路徑換成通用形式：

```powershell
cd <專案根目錄>
```

2. `README.md` 手動啟動段落補一句：本機若使用既有的 conda 環境，可設 `IOT_PYTHON` 或 `configs/launcher.json` 的 `python` 欄位，不必建立 `.venv`。

3. `docs/IMPLEMENTATION_STATUS.md` 的測試數字改為 A1 實測結果，並註明執行環境與日期，例如：

```markdown
- 自動化驗證（2026-09-15，Python 3.11 + pytest <版本>）：`python -m pytest tests -q` → **87 passed**。
```

後續任何一次改動後，請用同一行命令重新產生數字，不要沿用舊值。

**完成判據**：文件中不出現 `C:\Users\gcgcg` 或 `D:\ProgramData\Anaconda_envs` 之類的個人路徑（設定檔範例除外）。

```powershell
Select-String -Path $proj\README.md, $proj\docs\*.md -Pattern 'gcgcg|Anaconda_envs'
```

---

## 階段 B：修掉會誤導人的正確性缺陷

### B1｜Dashboard 裝置欄位對不上

**問題**：`services/dashboard/dashboard.js:11` 讀 `d.firmware_version` 與 `d.status_label`，但 `read_model.list_devices()` 回傳的是 `firmware` 與 `status`，導致裝置卡片永遠顯示「firmware 未提供」，狀態顯示英文。

**涉及檔案**：`services/dashboard/dashboard.js`

**步驟**

在 `dashboard.js` 的 `renderDevices` 前新增狀態標籤對照，並改寫該行：

```javascript
const deviceStatusLabel = {online:'在線', stale:'延遲', offline:'離線'};
```

```javascript
${esc(d.firmware||'firmware 未提供')} · 最後心跳 ${d.last_seen?new Date(d.last_seen*1000).toLocaleTimeString():'—'}
<span class="device-status ${esc(d.status)}">${esc(deviceStatusLabel[d.status]||d.status)}</span>
```

**完成判據**：跑起服務並讓心跳上報後，裝置列顯示 `simulator-0.1` 與「在線／延遲／離線」，不再出現「firmware 未提供」。

```powershell
& $py -m simulator.heartbeat --once --device-id verification-sim --local-data-dir "$env:LOCALAPPDATA\IoTGroup5"
```

---

### B2｜首頁卡片時間窗與標籤不符

**問題**：`index.html:38-39` 標示「今日對話／近 24 小時」，但 `read_model.dashboard_summary()`（`services/dashboard/read_model.py:93-94`）統計的是全時段累計值。

**涉及檔案**：`services/dashboard/read_model.py`、`services/dashboard/dashboard.js`

**步驟**

1. 在 `dashboard_summary()` 內計算時間窗起點並**新增**欄位（保留舊欄位以免破壞既有介面與測試）：

```python
    now_value = time.time() if now is None else now
    today_start = datetime.combine(
        datetime.fromtimestamp(now_value, timezone.utc).date(),
        datetime.min.time(),
        tzinfo=timezone.utc,
    ).timestamp()
```

```python
        "today_conversation_count": conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE created_at>=?", (today_start,)
        ).fetchone()[0],
        "active_user_count_24h": conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM conversations WHERE created_at>=?",
            (now_value - 86400,),
        ).fetchone()[0],
```

2. `dashboard.js` 的 `load()` 改讀新欄位：

```javascript
$('conversation-count').textContent=summary.today_conversation_count??0;
$('user-count').textContent=summary.active_user_count_24h??0;
```

**完成判據**

```powershell
& $py -m pytest tests/dashboard -q
```

- 既有測試全綠（`tests/dashboard/test_read_model.py` 仍斷言舊欄位 `conversation_count` / `active_user_count`）。
- 介面上「今日對話」只反映當日資料；插入一筆昨日對話後該數字不變。

---

### B3｜清除 `app.py` 的死碼

**問題**：`services/dialogue/app.py:63-89` 的 `detect_emotion` / `fallback_reply` / `deepseek_reply` 與 `services/dialogue/pipeline.py` 內的實作重複，且全專案無人呼叫（`rg` 已確認），是後續改錯的來源。

**步驟**

1. 刪除 `app.py` 第 63–89 行三個函式。
2. 確認 `httpx` 匯入是否仍被使用：

```powershell
Select-String -Path $proj\services\dialogue\app.py -Pattern 'httpx'
```

若僅剩 `import httpx` 一行，則一併刪除該匯入（`services/dialogue/deepseek.py` 有自己的 httpx）。

**完成判據**

```powershell
& $py -m compileall -q services simulator scripts
& $py -m pytest tests/dialogue tests/security -q
```

---

### B4｜登入密碼改用常數時間比較

**問題**：`services/dialogue/app.py:634` 使用 `!=` 比較，屬於非常數時間比較；內網 demo 可接受，但修一行即可。

**步驟**

1. `app.py` 頂部新增 `import hmac`（若尚未匯入）。
2. 將該行改為：

```python
        if not hmac.compare_digest(req.password, expected):
```

**完成判據**：`& $py -m pytest tests/security -q` 全綠；手動登入一次成功、錯密碼回 401。

---

### B5｜心跳在 loopback/內網時忽略系統代理

**問題**：`logs/heartbeat-error.log` 曾出現本機 `8080` 回 `502 Bad Gateway`；本機直連出現 502 通常是 `HTTP_PROXY`/`ALL_PROXY` 被 httpx 採用。

**涉及檔案**：`simulator/heartbeat.py`

**步驟**

```python
    response = httpx.post(url, json=payload, headers=headers, timeout=10, trust_env=False)
```

（`--local-data-dir` 已經限定 loopback，因此關閉環境代理是安全且正確的行為。若不想改程式碼，替代方案是在啟動腳本設 `NO_PROXY=127.0.0.1,localhost`。）

**完成判據**

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:9'   # 故意設一個壞代理
& $py -m simulator.heartbeat --once --device-id proxy-check --local-data-dir "$env:LOCALAPPDATA\IoTGroup5"
```

- 仍回 `{'ok': True, ...}`，且 `logs/heartbeat-error.log` 不再新增。

---

## 階段 C：安全加固

### C1｜聲紋模板改用 AES-GCM，並強制金鑰（VP2 信封）

**問題**：`services/voiceprint/storage.py:19` 用 `sha256(key+nonce)` 產生 32 位元組金鑰流循環 XOR，屬自製流密碼；且未設金鑰時 fallback 到硬編碼 `local-development-voiceprint-key`（`storage.py:14`）。

**涉及檔案**：`services/voiceprint/storage.py`、`requirements.txt`、`scripts/setup-project.ps1`、`scripts/start-project.ps1`、`tests/conftest.py`、`services/dialogue/app.py`（`/health`）

**前置**：需要新增依賴 `cryptography`（目前環境未安裝，此步需要網路）。

**步驟**

1. `requirements.txt` 追加：

```
cryptography>=42,<46
```

```powershell
& $py -m pip install -r $proj\requirements.txt
```

2. 改寫 `services/voiceprint/storage.py`（保留 VP1 讀取能力，既有登記不會失效）：

```python
"""Authenticated envelope for voiceprint templates (AES-GCM, VP2).

The database stores only ciphertext.  The key comes from
``VOICEPRINT_TEMPLATE_KEY``; legacy VP1 envelopes stay readable so existing
enrolments keep working after the upgrade.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX_V1 = b"VP1"
_PREFIX_V2 = b"VP2"
_DEV_KEY = b"local-development-voiceprint-key"
_AAD = b"voiceprint-template-v2"


def _key(require: bool = True) -> bytes:
    value = os.getenv("VOICEPRINT_TEMPLATE_KEY", "").encode("utf-8")
    if not value:
        if require:
            raise RuntimeError("voiceprint_template_key_required")
        value = _DEV_KEY
    return hashlib.sha256(value).digest()


def seal(values: list[float]) -> str:
    plain = (",".join(f"{float(v):.9g}" for v in values)).encode()
    nonce = secrets.token_bytes(12)
    cipher = AESGCM(_key()).encrypt(nonce, plain, _AAD)
    return base64.urlsafe_b64encode(_PREFIX_V2 + nonce + cipher).decode()


def open_sealed(token: str) -> list[float]:
    raw = base64.urlsafe_b64decode(token.encode())
    if raw[:3] == _PREFIX_V2:
        nonce, cipher = raw[3:15], raw[15:]
        if len(nonce) != 12 or not cipher:
            raise ValueError("invalid_template")
        plain = AESGCM(_key()).decrypt(nonce, cipher, _AAD)
        return [float(x) for x in plain.decode().split(",") if x]
    # Legacy VP1: read-only compatibility, never written for new templates.
    if len(raw) < 3 + 16 + 32 or raw[:3] != _PREFIX_V1:
        raise ValueError("invalid_template")
    body, tag = raw[:-32], raw[-32:]
    key = _key(require=False)
    if not hmac.compare_digest(hmac.new(key, body, hashlib.sha256).digest(), tag):
        raise ValueError("invalid_template")
    nonce, cipher = body[3:19], body[19:]
    stream = hashlib.sha256(key + nonce).digest()
    plain = bytes(byte ^ stream[i % len(stream)] for i, byte in enumerate(cipher))
    return [float(x) for x in plain.decode().split(",") if x]
```

3. `scripts/setup-project.ps1` 首次執行時產生 32 位元組金鑰並用 DPAPI 保存（沿用既有的 `admin.password` 模式），放在管理員密碼區塊之後：

```powershell
$voiceKeyPath = Join-Path $projectRoot "data\secrets\voiceprint.key"
if (-not (Test-Path -LiteralPath $voiceKeyPath)) {
    $bytes = New-Object 'System.Byte[]' 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $plainVoiceKey = [Convert]::ToBase64String($bytes)
    $secureVoiceKey = ConvertTo-SecureString $plainVoiceKey -AsPlainText -Force
    [System.IO.File]::WriteAllText(
        $voiceKeyPath,
        (ConvertFrom-SecureString $secureVoiceKey),
        (New-Object System.Text.UTF8Encoding($false))
    )
    $plainVoiceKey = $null
    Write-Host "Voiceprint template key generated and encrypted with Windows DPAPI." -ForegroundColor Green
}
```

4. `scripts/start-project.ps1` 啟動時注入（與 DeepSeek／管理員密碼同一區塊）：

```powershell
$voiceKeyFile = Join-Path $projectRoot "data\secrets\voiceprint.key"
if (-not $env:VOICEPRINT_TEMPLATE_KEY -and (Test-Path -LiteralPath $voiceKeyFile)) {
    $env:VOICEPRINT_TEMPLATE_KEY = Read-EncryptedSecret $voiceKeyFile
}
```

5. `tests/conftest.py` 讓測試具備金鑰（維持測試可重現）：

```python
    os.environ.setdefault("VOICEPRINT_TEMPLATE_KEY", "iot-tests-voiceprint-key")
```

6. `app.py` 的 `/health` 增加可觀測欄位，避免登記時才發現沒金鑰：

```python
        "voiceprint_key_configured": bool(os.getenv("VOICEPRINT_TEMPLATE_KEY", "").strip()),
```

並在 `complete_enrollment` 捕捉 `RuntimeError` 轉成明確的 503：

```python
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
```

7. 金鑰輪換（會使既有模板失效）：輪換後使用者需重新登記；若不願重新登記，可用舊金鑰讀出、新金鑰寫回：

```powershell
$env:VOICEPRINT_TEMPLATE_KEY = '舊金鑰'
& $py -c "from services.storage.database import open_database; from services.voiceprint.storage import open_sealed, seal; import os, sqlite3; c=open_database(os.path.join(os.environ['IOT_DATA_DIR'],'emotional_robot.sqlite3')); rows=c.execute('SELECT template_id,embedding_json FROM voiceprint_templates').fetchall(); os.environ['VOICEPRINT_TEMPLATE_KEY']='新金鑰'; print([(r[0], seal(open_sealed(r[1]))) for r in rows])"
```

（請先把舊金鑰讀出後再切換，正式環境建議直接重新登記並在文件記錄輪換日期。）

**完成判據**

```powershell
& $py -m pytest tests/test_enrollment_workflow.py tests/storage -q
```

- 新建模板的 `embedding_json` 以 `VP2` 開頭（base64 解碼後前 3 位元組為 `VP2`），且同一輸入兩次加密結果不同（nonce 隨機）。
- 未設定 `VOICEPRINT_TEMPLATE_KEY` 時登記回 503 `voiceprint_template_key_required`，而**不是**靜默用開發金鑰。
- 舊資料（VP1）仍可 `open_sealed` 成功。

**注意**：變更金鑰即失效既有模板，屬於破壞性操作；升級前請先 `scripts/backup-runtime.py` 備份資料庫。

---

### C2｜退役 `.env` 中的明文 API Key

**問題**：`.env` 目前存有一個 35 字元的真實 DeepSeek key，違反 `README.md:33`「不要把密鑰寫入 .env」的自我約定，且與專案的 DPAPI 通道（`data/secrets/deepseek.key`）並存。

**步驟**

1. 到 DeepSeek 主控台**輪換**該 key（舊 key 視為已洩漏）。
2. 用專案既有流程重新寫入加密檔（隱藏輸入）：

```powershell
Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
& powershell -NoProfile -ExecutionPolicy Bypass -File $proj\scripts\setup-project.ps1 -SkipInstall
```

3. 清空 `.env` 中的 `DEEPSEEK_API_KEY=`（保留其他非敏感設定），確認 `.env` 仍在 `.gitignore` 內：

```powershell
git -C $proj check-ignore -v .env
```

4. 新增防護檢查（可選，建議）`scripts/check-secrets.ps1`，在 CI 或啟動前掃描 `DEEPSEEK_API_KEY=sk-` 之類的明文。

**完成判據**

- `.env` 內 `DEEPSEEK_API_KEY` 為空；`/health` 在啟動腳本環境下回報 `deepseek_configured: true`（代表金鑰走的是加密檔）。
- `git log -p -- .env` 不存在任何真實 key（`.env` 從未被提交）。

---

### C3｜忽略本機 AI 工具設定

**步驟**

1. `.gitignore` 追加：

```
.claude/
```

2. 確認 `.claude/settings.json` 不會被提交（該檔案內含 `Bash(...)` 之類的本機命令白名單，不應進版控）。

**完成判據**：`git -C $proj status --short` 不再顯示 `.claude/`。

---

## 階段 D：把「聲紋」從演示接成真實鏈路（決策點）

> **先做決策**：D1+D2 是「讓身份判定真正生效」的工程改造；D3 是「換成真 embedding」。若本階段只做 D1，聲紋仍是 3 維佔位特徵，務必在 UI 與文件明確標示，避免演示時被追問。

### D1｜`/api/transcribe` 附帶服務端聲紋判定（可信 claim）

**問題**：`app.py:232` 與 `app.py:577` 直接硬編碼 `IdentityResult("accepted", req.user_id, 1.0, ...)`，等於「下拉框選中的人就是說話人」；`/api/voiceprint/identify` 雖已存在卻未接進對話鏈路。

**設計**：不要在 WebSocket 訊息裡信任前端傳來的 user_id。改為在**已收到音訊**的 `/api/transcribe` 內順帶做聲紋判定，回傳一次性 `claim_id`；WebSocket 只帶 `claim_id`，由伺服器查庫還原判定結果。

**涉及檔案**：`services/storage/migrations.py`（SCHEMA_VERSION 3 → 4）、`services/dialogue/app.py`、`services/dashboard/simulator.js`、`docs/PC_ACCEPTANCE.md`

**步驟**

1. 遷移新增資料表：

```sql
CREATE TABLE IF NOT EXISTS identity_claims (
    claim_id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, decision TEXT NOT NULL,
    template_id TEXT, best_score REAL, second_score REAL, provider TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_identity_claims_expiry ON identity_claims(expires_at);
```

2. `/api/transcribe` 在 ASR 結果之後加入（沿用 `/api/voiceprint/identify` 的模板查詢與 `identify_voiceprint`）：

```python
            vector = application.state.voiceprint_provider.embed(normalized.pcm16)
            templates = load_active_templates(conn)          # 抽成共用函式，與 identify 路由共用
            identity = identify_voiceprint(vector, templates)
            claim_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO identity_claims(claim_id,session_id,user_id,decision,template_id,best_score,second_score,provider,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (claim_id, session_id, identity.user_id, identity.decision, identity.template_id,
                 identity.best_score, identity.second_score, identity.provider, time.time(), time.time() + 120),
            )
            conn.commit()
            result["identity"] = {"claim_id": claim_id, "decision": identity.decision,
                                  "user_id": identity.user_id, "best_score": identity.best_score,
                                  "second_score": identity.second_score, "provider": identity.provider}
```

3. `simulator.js` 在收到轉寫結果後，把 `claim_id` 一併送出：

```javascript
socket.send(JSON.stringify({type:"chat",request_id:screen.requestId,text:transcript.text,
  user_id:selectedUser,device_id:session.device_id,identity_claim_id:transcript.identity?.claim_id}));
```

**完成判據**

- `/api/transcribe` 回應含 `identity`；`identity_claims` 每回合寫入一列。
- 未登記聲紋的使用者送音訊時，`decision` 為 `unknown`，不會被誤判為他人。

---

### D2｜伺服器端身份強制開關 `IOT_VOICEPRINT_ENFORCE`

**步驟**

1. WebSocket 與 `/api/chat` 都以 claim 還原身份，取代硬編碼：

```python
def resolve_identity(conn, claim_id: str | None, requested_user: str, session_id: str):
    if claim_id:
        row = conn.execute(
            "SELECT * FROM identity_claims WHERE claim_id=? AND expires_at>?", (claim_id, time.time())
        ).fetchone()
        if row:
            return IdentityResult(row["decision"], row["user_id"], row["best_score"],
                                  row["second_score"], row["template_id"], row["provider"])
    if os.getenv("IOT_VOICEPRINT_ENFORCE", "0") == "1":
        raise HTTPException(status_code=409, detail="voiceprint_claim_required")
    # 未啟用強制時維持 PC 模擬歸屬，但標記來源
    return IdentityResult("accepted", requested_user, 1.0, None, None, "simulator")
```

2. 啟用強制時，決策必須是 `accepted` 且 `user_id == 訊息中的 user_id`，否則回 `turn.failed{error:"voiceprint_not_verified"}` 並寫入 `audit_events`。
3. 把身份來源帶進事件與讀模型，讓 Dashboard 能顯示「模擬歸屬」或「已驗證」：

```python
payload["identity"] = {"decision": identity.decision, "provider": identity.provider, "user_id": identity.user_id}
```

4. 文件同步：`docs/PC_ACCEPTANCE.md` 第 6 步補上「目前為 3 維佔位特徵」的警語；`README.md` 的「PC 以所選使用者模擬對話歸屬」段落標明由 `IOT_VOICEPRINT_ENFORCE` 控制。

**完成判據**（新增測試）

```powershell
& $py -m pytest tests/dialogue tests/security -q
```

- `IOT_VOICEPRINT_ENFORCE=0`：行為與現在相同，但事件帶 `"provider":"simulator"`。
- `IOT_VOICEPRINT_ENFORCE=1` + 無 claim → 409／`turn.failed`；claim 屬他人 → 拒絕並留審計紀錄；claim 正確 → 正常對話。
- 新增測試檔：`tests/dialogue/test_identity_enforcement.py`。

---

### D3｜（可選）換成真實 embedding

**現況**：`services/voiceprint/provider.py:4` 只回傳 `[mean, rms, zcr]` 三個數，不具身份辨識能力，任何人的分數都會通過 `threshold=0.82`。

**步驟（PoC，1–2 天）**

1. 選型：ECAPA-TDNN（`speechbrain`）或 3D-Speaker；確認授權與模型大小（本機 RTX 2060 建議 ≤ 100 MB）。
2. 新增 provider 實作，保持 `embed(pcm16) -> list[float]` 契約不變，並在模板寫入 `model_version`（例如 `ecapa-v1`）。
3. 校準：準備 ≥ 5 位使用者、每人 ≥ 3 段音訊的 fixture，量測 FA/FR 後再決定 `threshold` 與 `min_margin`（`services/voiceprint/matcher.py` 已參數化）。
4. 把 `model_version` 納入 `identity_claims` 與 `/api/voiceprint/identify` 的查詢條件，避免新舊特徵混用。

**完成判據**：以 fixture 產生 FA/FR 報告並寫入 `docs/`；同一使用者的分數明顯高於他人，`unknown` 判定能實際擋下未登記的說話者。

---

## 階段 E：執行健壯性（可選但建議）

### E1｜SQLite 開啟 WAL

**問題**：心跳與對話並發寫入時可能出現 `database is locked`（目前僅靠 `busy_timeout=5000` 兜底）。

**步驟**：`services/storage/database.py` 的 `open_database()` 增加：

```python
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
```

**注意**：WAL 不適用於網路磁碟（OneDrive／SMB）；本專案資料目錄已改為 `%LOCALAPPDATA%`，符合前提。

**完成判據**：`& $py -m pytest tests/storage -q` 全綠；同時跑心跳與多次對話請求時，`logs/service-error.log` 不再出現 `database is locked`。

---

### E2｜TTS 現況明確化（或補上真的語音合成）

**問題**：`services/tts/windows.py:13,17` 任何情況都回傳 `DeterministicTts` 的靜音，真正發聲靠瀏覽器 `speechSynthesis`；`/api/media` 下發的 WAV 是靜音，容易被誤解為已實作。

**選項 A（30 分鐘，推薦先做）**：在 `docs/IMPLEMENTATION_STATUS.md`、`docs/PC_ACCEPTANCE.md` 與 simulator 頁面標註「伺服器端 TTS 為字幕佔位（靜音），播放由瀏覽器語音合成負責」。

**選項 B（2 小時）**：用 Windows SAPI 實作 `WindowsTtsProvider`：

```python
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.SetOutputToWaveFile('{output_path}');"
            "$t = [Console]::In.ReadToEnd(); $s.Speak($t); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       input=phrase, text=True, check=True)
```

產生的 WAV 可交給既有的 `normalize_audio()` 轉成 16 kHz 單聲道，再接回 `MediaStore`。

**完成判據**：選項 A → 文件中不再有歧義描述；選項 B → `GET /api/media/{id}` 取得的 WAV 有實際語音，且 PC 驗收第 7 步能用瀏覽器播放後收到 `playback.completed`。

---

### E3｜錄音前端現代化（或有意識地延後）

**問題**：`services/dashboard/simulator.js` 使用已廢棄的 `createScriptProcessor`，且流程是「錄完整段 → HTTP 上傳轉寫 → 文字再走 WebSocket」，非串流；真機聯調時延遲會很明顯。

**步驟**：改用 `AudioWorklet`（保留同一個 `encodeWav` 輸出契約），或明確在 `docs/` 記錄「P1 為批次式錄音，P2 改串流」並加上 30 秒上限的使用說明。

**完成判據**：`node --check services/dashboard/simulator.js` 通過；Chrome/Edge 下連續 3 輪對話無錄音殘留與記憶體成長。

---

## 階段 F：驗收與交付

### F1｜自動化驗收（每個 commit 前跑）

```powershell
Set-Location $proj
& $py -m pytest tests -q
& $py -m compileall -q services simulator scripts
node --check services/dashboard/simulator.js
node --check services/dashboard/esp-display.js
git diff --check
```

判據：測試全綠、compileall 無輸出、JS 語法檢查通過、無尾隨空白。

### F2｜端到端手工驗收（對照 `docs/PC_ACCEPTANCE.md` 既有 8 步）

在**一般 PowerShell 視窗**（非沙箱）依序執行：

```powershell
& powershell -NoProfile -ExecutionPolicy Bypass -File $proj\一鍵安裝並啟動.bat
```

或分步：

```powershell
& powershell -NoProfile -ExecutionPolicy Bypass -File $proj\scripts\provision-simulator.ps1 -DataDir "$env:LOCALAPPDATA\IoTGroup5" -DeviceId pc-sim
& powershell -NoProfile -ExecutionPolicy Bypass -File $proj\scripts\start-project.ps1
& powershell -NoProfile -ExecutionPolicy Bypass -File $proj\scripts\acceptance-check.ps1 -Password '<管理員密碼>'
```

手工檢查清單：

1. `/health` 回 `status: ok`，且 `admin_configured`、`voiceprint_key_configured` 皆為 `true`。
2. 登入 Dashboard，使用者列顯示中文狀態、裝置卡顯示 firmware 與「在線」。
3. 建立使用者 → 三步聲紋登記（每段試聽後才確認）。
4. PC 模擬器錄一段語音 → 字幕出現 → 收到回覆（有 key 用 DeepSeek，無 key 走 fallback）。
5. 說一句高風險語句（例如「我不想活了」）→ `risk_events` 出現 `urgent`，Dashboard 使用者頁可見到並能改 review 狀態。
6. 刪除使用者 → 對話／聲紋／風險事件一併消失，裝置仍在但解除關聯。

### F3｜交付與回滾

- 建議 commit 切分：A1 / A2 / A3 / B1-B5 / C1 / C2 / C3 / D1 / D2 / E*。
- 每個階段結束時記錄一次 `docs/IMPLEMENTATION_STATUS.md` 的實測數字。
- 回滾：任一 commit 可獨立 `git revert`；C1 與 D1 涉及資料庫結構，回滾前先跑 `scripts/backup-runtime.py` 備份。

---

## 附錄 A：缺陷 → 任務 → 驗證對照表

| 缺陷 | 任務 | 驗證 |
|---|---|---|
| 環境缺 pytest、測試不可重現 | A1 | `pytest tests -q` |
| 啟動腳本硬編碼 Anaconda 路徑 | A2 | `start-project.ps1 -CheckOnly`、`tests/test_project_launcher.py` |
| README 舊機器路徑、狀態數字過期 | A3 | `Select-String` 掃描 |
| 裝置卡永遠顯示「firmware 未提供」 | B1 | 心跳後看 Dashboard |
| 首頁時間窗標籤與資料不符 | B2 | `tests/dashboard`、手工檢查 |
| `app.py` 重複死碼 | B3 | `compileall`、`pytest tests/dialogue` |
| 登入非常數時間比較 | B4 | `pytest tests/security` |
| 心跳遇系統代理回 502 | B5 | 故意設壞代理仍成功 |
| 聲紋模板自製 XOR 加密、預設金鑰 | C1 | VP2 前綴、無金鑰回 503、VP1 可讀 |
| `.env` 明文 API key | C2 | `/health`、`git check-ignore` |
| `.claude/` 未忽略 | C3 | `git status --short` |
| 身份硬編碼 accepted 1.0 | D1、D2 | 新增 identity 測試 |
| embedding 僅 3 維 | D3 | FA/FR 報告 |
| 併發寫入可能鎖庫 | E1 | 併發心跳＋對話 |
| 伺服器端 TTS 無聲 | E2 | `/api/media` 音訊內容 |
| 錄音用已廢棄 API、非串流 | E3 | `node --check`、連續對話 |

## 附錄 B：本方案明確不做的事

- 不修改 `upstream/xiaozhi-esp32-server`（維持固定 commit `6afc54a17def47578a4b3efc4680873689d3168b`）。
- 不在本階段啟用 RAGFlow 或自訂知識庫（P1 刻意保留為 optional adapter）。
- 不實作 MQTT broker／OTA／UDP 音訊通道（屬 P2，需 ESP 硬體到位）。
- 不刪除 `data/emotional_robot.sqlite3` 或任何既有執行期資料；資料遷移一律先備份。
