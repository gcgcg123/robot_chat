> **⚠ 歷史規劃文件（2026-09-14）**：本檔是當時的規劃／規格紀錄，保留原樣以利追溯。
> 其中的個人路徑（`C:/Users/gcgcg/...`）指向撰寫當時的機器，**在本儲存庫無效**；
> 「目前不是 Git repository」「已下載的模型」等敘述也可能已經改變。
> 現況請看 [IMPLEMENTATION_STATUS.md](../../IMPLEMENTATION_STATUS.md)。

# 第一階段：無 ESP 軟體 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans after user authorization. Steps use checkbox (`- [ ]`) syntax for tracking. 本文件是未執行的開發清單，測試範例不是本輪測試結果。

**Goal:** 在 PC 上完成安全的使用者註冊、聲紋辨識、語音對話、RAG、TTS／字幕／角色與管理後台，交付可接 ESP 的接口。

**Architecture:** 保留現有 FastAPI／SQLite／Dashboard；app 只負責組裝獨立模組，所有音訊入口共用一條 turn pipeline。慢速模型置於有界 worker／獨立 runner，PC 以授權 WebSocket 接收句子與播放事件，後台以 read model 查詢。

**Tech Stack:** Windows、Python 3.13、FastAPI、SQLite、faster-whisper、DeepSeek 官方 HTTP API、原生 Web Audio／MediaRecorder、pytest；模型相容性需要時另設 Python 3.11 runner，前端測試使用 Node 內建 test runner。

**Spec:** [主規格](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/specs/2026-09-13-user-registry-voiceprint-design.md)、[語音顯示規格](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/specs/2026-09-13-esp-voice-display-design.md)、[兩階段總覽](C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place/docs/superpowers/plans/2026-09-14-two-stage-implementation.md)。

## Global Constraints

- 本計劃須獲授權才執行；不得因閱讀清單而啟動服務、採集錄音或下載模型。
- 專案根目錄 `C:/Users/gcgcg/OneDrive/Desktop/IoT_group5/project_place`；下文程式路徑以此為基準，新增檔案全部是規劃位置。
- 沿用已下載的 `models/asr/whisper-large-v3-turbo-ct2`，不重複下載 Whisper。
- 敏感執行資料 `C:/Users/gcgcg/AppData/Local/IoTGroup5`，先備份／migration，再切換；原始音訊推論後刪除、崩潰遺留最多一小時；同意後除錯音訊 TTL 二十四小時。
- Dashboard 不提供文字聊天框；PC mic 必須使用者點擊並授權；TTS 必須由後端生成再真正播放。
- 登入、授權、CSRF／Origin、profile／voiceprint／cloud_dialogue 分用途同意先於真人資料；未知身份不可繼承他人記憶。
- 上傳最多十 MB／六十秒，聲紋有效人聲最低先定三秒，三至五註冊樣本另加獨立驗證；註冊十分鐘到期、ready 最多等三十秒。
- 自述年齡 0–120 整數或空值，保存登記時間；不以聲音推測性別／年齡；UTC 保存、Asia/Hong_Kong 顯示；列表預設 20、最多 100。
- 半雙工、按鈕打斷、逐句字幕，播放 generated／sent／started／completed 分開；裝置在線不代表使用者在場。
- 聲紋不是安全認證、情緒不是診斷；無可用分析不能顯示正常，不自動聯絡第三方。
- 目前不是 Git repository：每項通過後保存變更檔案清單及測試結果，不執行 git init／commit。將來使用者啟用 Git 才改成小提交。

## 執行方法與檔案責任

每個工作包依「紅測試 → 確認失敗原因 → 最小實作 → 回歸／人工檢查 → 紀錄」推進；子步驟分次完成，不把模型下載、真人驗收和外部計費藏在 unit test。以下 Python 範例為核心規則及代表性測試，需與列明的 API／錯誤案例一併實作，不以範例覆蓋率代替完整验收。

所有測試命令從專案根目錄執行。`python` 必須是啟動器實際使用的專案 interpreter：執行前讀取 `scripts/launcher-common.ps1` 並核對 `python -c "import sys; print(sys.executable)"`，不要修改系統全域 Python。第一個基準測試只在 P1-01 隔離完成後執行。

| 目錄／檔案 | 職責 |
|---|---|
| `services/dialogue/app.py` | create_app、router／dependency 組装，保持現有啟動入口 |
| `services/storage/`、`services/security/` | 設定、DB／migration、備份／授權／同意／稽核 |
| `services/users/`、`services/enrollment/` | 個資 CRUD、註冊狀態機及原子提交 |
| `services/audio/`、`services/voiceprint/`、`services/tts/` | 解碼／ASR、身份判定、合成／私人媒體 |
| `services/memory/`、`services/analysis/` | RAG／個人記憶、文字情緒／風險／摘要 |
| `services/dialogue/pipeline.py`、`services/dialogue/deepseek.py` | 單輪 orchestration、雲端合約與取消 |
| `services/device_gateway/`、`simulator/` | 共用事件、能力映射、PC mic／播放器／虛擬螢幕 |
| `services/dashboard/` | 使用者頁、註冊精靈入口、裝置／分析呈現；不直接調模型 |
| `configs/`、`scripts/`、`docs/`、`tests/` | 可配置運行、BAT 生命周期、操作文檔、隔離測試 |

## P1-01：測試隔離、安全儲存與管理授權

**Files**

- Modify: `tests/conftest.py`、`tests/test_project_launcher.py`、`tests/dashboard/test_api.py`、`services/dialogue/app.py`、`services/dashboard/read_model.py`、`scripts/launcher-common.ps1`。
- Create: `services/storage/settings.py`、`services/storage/database.py`、`services/storage/migrations.py`、`services/security/auth.py`、`services/security/consent.py`、`services/security/audit.py`、`scripts/backup-runtime.py`、`scripts/migrate-runtime.py`。
- Test: `tests/storage/test_isolation.py`、`tests/storage/test_migrations.py`、`tests/security/test_auth.py`、`tests/security/test_consent.py`。

**Interfaces**

- `RuntimeSettings(data_dir: Path, testing: bool = False)`，`database_path` 固定為 data_dir 下的 `emotional_robot.sqlite3`；正式設定優先讀取 `IOT_DATA_DIR`，保留顯式 `DATABASE_PATH` 相容入口。
- `create_app(settings: RuntimeSettings | None = None, providers: dict | None = None) -> FastAPI`；import 不建立 DB、不載模型、不呼叫網路。lifespan 才執行 migration／啟動已配置 provider。
- `open_database(path: Path) -> sqlite3.Connection`，連線啟用 foreign_keys；`migrate(conn) -> int` 返回 schema version。
- `require_admin(request) -> str` 回傳 actor_id 或 HTTP 401/403；`require_device(request, device_id: str) -> str` 驗證 token 綁定裝置；`has_consent(conn, user_id, purpose) -> bool` 依最後一次明確決定判斷。
- `GET /health` 只作存活；`GET /api/system/status` 須管理權限，回傳 configured/loading/ready/degraded/error 及最近測試時間，不含秘密。
- `POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/session`；cookie HttpOnly／SameSite，修改 API 需 CSRF token／Origin，HTTPS 才使用 Secure。登入限速、session 過期，無公開預設密碼。

- [ ] 先改測試引導，避免收集 test 時 import 全域 app 就寫入正式 DB；隔離程式碼置於 `tests/conftest.py`，保留既有 sys.path 設定：

```python
import os
import tempfile

_runtime = None

def pytest_configure(config):
    global _runtime
    _runtime = tempfile.TemporaryDirectory(prefix="iot-tests-")
    os.environ["IOT_TESTING"] = "1"
    os.environ["IOT_DATA_DIR"] = _runtime.name
    os.environ["DATABASE_PATH"] = os.path.join(_runtime.name, "emotional_robot.sqlite3")
    os.environ["DEEPSEEK_API_KEY"] = ""

def pytest_unconfigure(config):
    if _runtime is not None:
        _runtime.cleanup()
```

測試 fixture 每例建立獨立 `RuntimeSettings(tmp_path, testing=True)` 與 fake providers；啟動器子程序也顯式傳入該測試的 `DATABASE_PATH`／`IOT_DATA_DIR`，不能只改 port／logs。testing 模式的 provider 禁止真網路，清 key 不是唯一保護：啟動器也不得在 testing 模式解密真人 key。

- [ ] 寫入以下紅測試並執行 `python -m pytest tests/storage/test_isolation.py -q`，預期缺少 factory／settings 時失敗，而非碰到正式 DB：

```python
from fastapi.testclient import TestClient
from services.dialogue.app import create_app
from services.storage.settings import RuntimeSettings

def test_app_uses_only_explicit_test_runtime(tmp_path):
    settings = RuntimeSettings(data_dir=tmp_path / "isolated", testing=True)
    app = create_app(settings=settings, providers={})
    assert not settings.database_path.exists()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/users").status_code == 401
    assert settings.database_path.exists()
```

- [ ] 完成 factory、授權和測試依賴注入；備份採 SQLite backup API 而非運行中直接複製主檔，核心程式：

```python
def backup_database(source, destination):
    import sqlite3
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup_integrity_failed")
```

`scripts/migrate-runtime.py` 先列 source／destination 並拒絕覆蓋非空目標；在維護窗口停止專案服務，備份、版本化 migration、外鍵／行數／ID／時間比對成功才切換設定。新增 consent、sessions、audio_turns、audit；重建 conversations 的可空 user_id 而不改舊 ID。保留舊資料和設定供回復，驗證新路徑後由使用者決定何時清理 OneDrive 舊副本／歷史版本，不自動刪除。DPAPI secret 亦先驗證新位置解密，再切換；測試故障時仍可回到舊設定。

- [ ] 執行 `python -m pytest tests/storage tests/security tests/test_project_launcher.py tests/dashboard/test_api.py -q`；加入 CSRF 缺失403、未登入401、裝置 token 不能管理用户403、撤回 consent、備份損壞拒絕切換、舊版資料 migration 重跑冪等、測試子程序不讀 DPAPI 等案例。最後才跑現有 baseline `python -m pytest -q`，只記錄真實結果。
- [ ] 將備份位置、資料行數核對、回復演練、基準測試及安全設定寫進 `docs/PHASE1_WORKLOG.md`；此時仍未採集真人資料。

## P1-02：使用者列表、個人頁與生命週期

**Files**

- Create: `services/users/schemas.py`、`services/users/repository.py`、`services/users/routes.py`、`services/dashboard/users.js`、`services/dashboard/user_detail.js`。
- Modify: `services/dialogue/app.py`、`services/storage/migrations.py`、`services/dashboard/index.html`、`services/dashboard/dashboard.js`、`services/dashboard/dashboard.css`、`services/dashboard/read_model.py`。
- Test: `tests/users/test_profiles.py`、`tests/users/test_api.py`、`tests/users/test_deletion.py`、`tests/test_dashboard_assets.py`。

**Interfaces**

- `POST /api/users` 建立 draft；`GET /api/users?q=&status=&page=1&page_size=20` 回 `{items,total,page,page_size}`；`GET/PATCH/DELETE /api/users/{user_id}`。DELETE 要求管理員再次確認，提交 `{confirm_user_id: user_id}`，不以姓名模糊匹配刪除。
- `ProfileInput(display_name, gender, age_at_registration, profile_note)`；gender 接受男／女／自訂／不透露，空值不由模型猜測；age 更新時服務端保存 `age_recorded_at`。
- `/dashboard/users/{user_id}` 直達刷新可用。`DELETE /api/users/{user_id}/voiceprint` 只刪聲紋；PATCH status=disabled 使模板立即停止參與辨識。
- `delete_user_data(conn, user_id: str) -> dict[str,int]` 刪個資、同意、模板、記憶、對話／風險等關聯資料並清快取；僅保留不含個資的刪除操作稽核。跨檔案清理用可重試 cleanup job，不能回報成功但仍可檢索到舊模板。

- [ ] 寫紅測試，執行 `python -m pytest tests/users/test_profiles.py -q`：

```python
import pytest
from pydantic import ValidationError
from services.users.schemas import ProfileInput

def test_self_reported_age_is_optional_and_bounded():
    assert ProfileInput(display_name="測試甲", gender="不透露").age_at_registration is None
    with pytest.raises(ValidationError):
        ProfileInput(display_name="測試甲", gender="不透露", age_at_registration=121)
    with pytest.raises(ValidationError):
        ProfileInput(display_name="測試甲", gender="不透露", age_at_registration=20.5)
```

- [ ] 實作 schema 核心及上述 CRUD，不自動把 demo 使用者升級為真人已註冊：

```python
from pydantic import BaseModel, Field

class ProfileInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    gender: str = Field(default="不透露", max_length=40)
    age_at_registration: int | None = Field(default=None, ge=0, le=120, strict=True)
    profile_note: str = Field(default="", max_length=1000)
```

列表提供搜尋、狀態／最近活動排序、simulator 標記；個人頁先呈現真實基本資料與空狀態，情緒卡寫「最近一次推估＋時間」，設備 online 不寫「本人在線」。姓名／摘要使用 textContent 防注入；建立個資表單不等於文字對話框。

- [ ] 補 CRUD API 測試，檢查404、輸入422、頁數／上限、停用與「只刪聲紋」不刪歷史、「刪帳戶」不可殘留可檢索資料；無權限一律不回傳個資。
- [ ] 執行 `python -m pytest tests/users tests/dashboard tests/test_dashboard_assets.py -q`；以測試 DB 手動驗證直達個人頁、返回列表、空／長姓名、小螢幕與確認刪除取消。
- [ ] 保存版本 A 的操作步驟與截圖（只用虛構資料），將實際測試結果記入工作紀錄。

## P1-03：ASR、音訊品質及模型生命週期

**Files**

- Create: `services/audio/normalize.py`、`services/audio/quality.py`、`services/audio/asr.py`、`services/audio/worker.py`、`services/models/manifest.py`、`scripts/check-models.py`、`configs/models.json`。
- Modify: `services/dialogue/app.py`、`requirements.txt`、`services/dashboard/read_model.py`。
- Test: `tests/audio/test_normalize.py`、`tests/audio/test_quality.py`、`tests/audio/test_asr.py`、`tests/models/test_manifest.py`。

**Interfaces**

- `normalize_audio(content: bytes, mime_type: str) -> NormalizedAudio`；dataclass 欄位 `pcm16: bytes, sample_rate: int, duration_ms: int`，輸出 mono／16k；不能靠副檔名猜 container。
- `check_quality(audio: NormalizedAudio, enrollment: bool) -> dict`，含 `accepted, reason, speech_duration_ms, clipping_ratio, rms`；實際 VAD 結果才作有效人聲，無測量為 null。
- `AsrService(model_path: str, model_factory, device: str="auto")`；`transcribe(audio: NormalizedAudio, language: str | None) -> dict` 返回 text／language／status／device／compute_type／latency_ms；同一 service 重用模型。
- `select_asr_runtime(cuda_available: bool) -> tuple[str,str]`；auto 初選 CUDA float16，失敗只針對已分類 CUDA／OOM 問題重試 CPU int8，其他檔案／格式錯誤不得偽裝 GPU 故障。
- 共用 `ModelManifest` 記錄 name、source、revision、license、sha256_files、package_versions、device、compute_type、verified_at、verification_status；預設未驗證，無真推論不標 ready。

- [ ] 建立紅測試並執行 `python -m pytest tests/audio/test_asr.py -q`：

```python
from services.audio.asr import select_asr_runtime

def test_asr_cpu_fallback_is_explicit():
    assert select_asr_runtime(False) == ("cpu", "int8")
    assert select_asr_runtime(True) == ("cuda", "float16")
```

- [ ] 實作常駐 provider 與基本選擇，再以 fake factory 補測「兩次 transcribe 只載一次」／CUDA 失敗後只載一次 CPU：

```python
def select_asr_runtime(cuda_available: bool) -> tuple[str, str]:
    return ("cuda", "float16") if cuda_available else ("cpu", "int8")
```

模型載入和推論置於專用 worker，ASR 同時執行初定一個、等待佇列最多四個；超額回429、每輪處理上限120秒且可取消，避免阻塞 heartbeat／WS。取消不能強殺正在使用的 GPU 物件，丟棄過期結果並釋放佇列資源。格式錯誤415、檔案過大413、無人聲422、等待超時504；最長秒數依解碼後長度再檢查。

- [ ] 核對已安裝 Turbo CT2 檔案／revision、授權與套件，不下載第二份；加入 manifest 校驗及真正 warm-up 狀態。測 WAV／WebM MIME、立體聲／24k重採樣、空音訊、超長、削波、噪音。normalize 保持聲紋使用音量與品質資訊，不過度降噪改變音色。
- [ ] 執行 `python -m pytest tests/audio tests/models -q`；獲同意後用非私人測試錄音實測 CPU／CUDA、冷暖延遲，獨立記錄普通話／粵語／中英混合轉錄，不把語言概率當準確率。
- [ ] 在 `docs/MODEL_MANIFEST.md` 記錄真實相容性及測試；若 GPU 失敗但 CPU 成功顯示 degraded，不改写成 GPU ready。

## P1-04：後端 TTS、句子與受限音訊資源

**Files**

- Create: `services/tts/provider.py`、`services/tts/windows.py`、`services/tts/segments.py`、`services/tts/media_store.py`、`services/device_gateway/contracts.py`、`tests/tts/test_segments.py`、`tests/tts/test_media_access.py`、`tests/tts/test_provider.py`。
- Modify: `services/dialogue/app.py`、`configs/launcher.json`。

**Interfaces**

- `TtsProvider.synthesize(text: str, voice: str) -> SynthesizedAudio`；dataclass 含 `pcm16: bytes, sample_rate: int, channels: int, duration_ms: int, provider: str`。實際輸出格式校驗後才建音訊資源，不假定 Windows voice 固定24k。
- `split_speech(text: str) -> list[str]`；先移除 Markdown／控制表情標記，保留可讀正文再依句號／問號／驚嘆號切分，過長句按標點分段。`speech_text` 同時用於 TTS 與字幕。
- `DeviceEvent`：`protocol_version=1, event_id, device_id, session_id, turn_id, type, payload`；句子 payload 另有 `segment_id, sequence, speech_text, audio_ref, robot_expression`。
- types 固定：`turn.started`、`stt.final`、`tts.segment`、`tts.end`、`turn.interrupted`、`turn.failed`、`playback.started`、`playback.completed`。`tts.end` 是生成／發送結束，不是播放完成。
- `GET /api/media/{audio_ref}` 驗證會話所有權，Cache-Control no-store；短期媒體10分鐘到期或輪次結束60秒後清理，以較早者為準，abort立即撤銷；音訊 URL 不含長期 device token。

- [ ] 寫红測試、執行 `python -m pytest tests/tts/test_segments.py -q`：

```python
from services.tts.segments import split_speech

def test_sentences_share_the_spoken_caption_text():
    assert split_speech("先慢慢來。願意說說嗎？") == ["先慢慢來。", "願意說說嗎？"]
    assert split_speech("**你好**。") == ["你好。"]
```

- [ ] 以固定語音 provider stub 實作句子及媒體權限，核心切句規則：

```python
import re

def split_speech(text: str) -> list[str]:
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", text).strip()
    return [part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]?", plain) if part.strip()]
```

再补 Markdown連結／控制標記／多餘空白／長句／空字串測試和清理，不將 raw model event 直接當朗讀正文。

- [ ] 列出 Windows 實際中文 voice，驗證能輸出非空 WAV／PCM而非只「有安裝」；CPU worker 合成。若缺少合適聲音，核對 sherpa-onnx 中文 VITS 模型卡與輸出 sample rate，在獨立 provider 先做非敏感句子合成，通過才納入 manifest。EdgeTTS 不自動啟用。語言不合格明示阻塞項，不以文字替代聲音驗收。
- [ ] 執行 `python -m pytest tests/tts -q`；驗證非所屬會話403、過期404、TTS失敗不發completed、音訊清理可重跑；真人點擊播放測試句，確認聽感和音量。`speechSynthesis` 不作此項通過依據。
- [ ] 將 voice、實際格式、耗時／離線能力記錄工作紀錄；給 P1-06 註冊提示共用，不另做第二套 TTS。

## P1-05：聲紋模型、門檻與註冊狀態機

**Files**

- Create: `services/voiceprint/provider.py`、`services/voiceprint/matcher.py`、`services/voiceprint/templates.py`、`services/voiceprint/runner.py`、`services/enrollment/service.py`、`services/enrollment/routes.py`、`services/enrollment/schemas.py`、`configs/voiceprint.json`。
- Modify: `services/storage/migrations.py`、`services/dialogue/app.py`、`configs/models.json`。
- Test: `tests/voiceprint/test_matcher.py`、`tests/voiceprint/test_templates.py`、`tests/enrollment/test_state_machine.py`、`tests/enrollment/test_api.py`。

**Interfaces**

- `SpeakerProvider.embed(audio: NormalizedAudio) -> list[float]`；CPU，模型來源候選 `speechbrain/spkrec-ecapa-voxceleb`。相容性不符時用獨立 Python 3.11 環境／loopback runner，runner token 不可用於管理 API。
- `IdentityResult` dataclass：`decision: str, user_id: str|None, score: float|None, threshold: float|None, top_two_margin: float|None, model_revision: str|None`。decision 僅 accepted／unknown／ambiguous／insufficient_audio／unavailable。
- `decide_identity(scores: list[tuple[str,float]], threshold: float, min_margin: float) -> IdentityResult`；分數不是身份概率，無結果時 score=null。
- 註冊 API 沿主規格 `/api/users/{id}/enrollments` 與 `/api/enrollments/{id}/{ready,samples,verify,complete,cancel}`，另 GET工作、PATCH profile。樣本 multipart 使用 sample_id／prompt_id／真 MIME；ready／samples 綁定被授權 device。
- `complete` 僅允許確認個資＋至少三有效樣本＋獨立驗證 accepted；同裝置一在途工作409、重複sample_id原樣返回、驗證不足409、過期410、他人裝置403。新模板提交失敗保留舊模板。

- [ ] 寫紅測試並執行 `python -m pytest tests/voiceprint/test_matcher.py -q`。以下0.7／0.05只是合成分數的單元測試參數，絕不可直接當真人生產門檻：

```python
from services.voiceprint.matcher import decide_identity

def test_close_candidates_never_choose_a_user():
    result = decide_identity([("a", .81), ("b", .80)], threshold=.7, min_margin=.05)
    assert (result.decision, result.user_id) == ("ambiguous", None)

def test_below_threshold_does_not_reuse_previous_speaker():
    result = decide_identity([("a", .2)], threshold=.7, min_margin=.05)
    assert (result.decision, result.user_id) == ("unknown", None)
```

- [ ] 實作排序／絕對門檻／差值判斷；accepted 才設定 user_id；沒有第二名 margin=null並明示評估資料不足。模板逐段 L2 normalize、均值再 normalize，DPAPI 加密儲存，刪除／停用／重登記使快取失效。

```python
def classify_scores(best: float, second: float | None, threshold: float, min_margin: float) -> str:
    if best < threshold:
        return "unknown"
    if second is not None and best - second < min_margin:
        return "ambiguous"
    return "accepted"
```

- [ ] 實作狀態機：pending→awaiting_device→collecting_profile→collecting_samples→verifying→completed；failed/canceled/expired 不接受新樣本。ready30秒、工作10分鐘、提示先播完再錄；個資回答不兼作聲紋样本。verify 音訊不得併入模板，樣本一致性異常要求重錄，不宣稱可分離多人。上傳／候選向量原子提交及清理列入 migration／工作到期流程。
- [ ] 核對模型授權／revision、Windows Python／Torch／torchaudio 相容性，必要時隔離 runner；完成真 embedding 與 manifest。執行 `python -m pytest tests/voiceprint tests/enrollment -q`，覆蓋品質不合格、重試去重、ACK不到、取消、重登記失敗保留舊模板、撤回 consent、錯誤provider無分數和刪除生效。
- [ ] 以分離的真人校準／留出錄音估計門檻與 top-two margin，保存版本／樣本數／FA／FR；樣本不足顯示「實驗性／未充分校準」。工作紀錄不可放原始音訊、向量或真姓名。

## P1-06：PC 麥克風模擬器與語音註冊精靈

**Files**

- Create: `simulator/index.html`、`simulator/simulator.css`、`simulator/microphone.js`、`simulator/player.js`、`simulator/state.mjs`、`simulator/app.js`、`services/dashboard/enrollment.js`、`services/device_gateway/pc.py`。
- Modify: `services/dashboard/users.js`、`services/dialogue/app.py`。
- Test: `tests/simulator/test_pc_session.py`、`tests/frontend/simulator.test.mjs`、`tests/enrollment/test_pc_flow.py`。

**Interfaces**

- `GET /simulator` 需登入；`POST /api/simulator/sessions` 建立裝置 kind=pc 的短期配對會話，回 `device_id, session_id, expires_at`；WS `/api/device/events` 驗證 session／Origin與裝置能力。
- PC hello 明示 mic／speaker／display／playback_ack／enrollment_v1；錄音需本地允許，Dashboard 點新增只派工作，不保證 mic 已開。
- `mayRecord(state: string) -> boolean`；`Microphone.start()`、`Microphone.stop()` 在 `simulator/microphone.js` 定義，stop 關閉所有 MediaStream tracks；錄音過程有真音量與可見提示。
- `Player.enqueue(segment)`、`Player.stop(turnId)` 在 `simulator/player.js` 定義；取後端音訊，實際 playback 開始／ended 才發 ACK，不依下載完成判斷。

- [ ] 加入 Node 純狀態紅測試，執行 `node --test tests/frontend/simulator.test.mjs`：

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import { mayRecord } from '../../simulator/state.mjs';

test('half duplex forbids recording during playback', () => {
  assert.equal(mayRecord('speaking'), false);
  assert.equal(mayRecord('listening'), true);
  assert.equal(mayRecord('enrollment_prompt'), false);
});
```

- [ ] 實作狀態核心並完成 UI／裝置握手：

```javascript
export function mayRecord(state) {
  return state === 'listening' || state === 'enrollment_recording';
}
```

MediaRecorder.isTypeSupported 選真格式，後端解碼；關頁、取消、裝置斷線即 stop tracks。首版按住／按鍵錄音，不啟用常駐背景監聽。為瀏覽器 autoplay 限制提供一次「啟用聲音／麥克風」點擊；權限拒絕、無裝置、被占用有可理解的重試提示。

- [ ] 製作註冊精靈：同意→選裝置→ready→TTS依序詢問個資→ASR填表→本人／管理員確認→朗讀樣本→獨立驗證→完成。註冊不呼叫 DeepSeek、不混進一般對話統計；確認表單保留同音姓名修正，性別／年齡不強迫提供。
- [ ] 執行 `python -m pytest tests/simulator tests/enrollment/test_pc_flow.py -q` 與 Node 測試；真人手動測允許／拒絕mic、聲音未解鎖、取消／超時、三段＋獨立樣本、斷線重送、同裝置busy，核對實际提示播放。
- [ ] 交付版本 B：將錄音／權限步驟、耳機建議、語音註冊重試原因及音軌停止證據寫入 `docs/PC_MIC_TEST_GUIDE.md`。

## P1-07：DeepSeek、基本 RAG 與個人記憶

**Files**

- Create: `services/dialogue/deepseek.py`、`services/memory/schemas.py`、`services/memory/repository.py`、`services/memory/retriever.py`、`services/memory/embedder.py`、`services/memory/routes.py`、`services/memory/prompt.py`、`configs/memory.json`、`data/knowledge/README.md`。
- Modify: `services/storage/migrations.py`、`services/dialogue/app.py`、`configs/models.json`、`services/dashboard/user_detail.js`。
- Test: `tests/memory/test_scope.py`、`tests/memory/test_retrieval.py`、`tests/memory/test_deletion.py`、`tests/dialogue/test_deepseek.py`。

**Interfaces**

- `MemoryChunk` dataclass：`chunk_id, owner_user_id: str|None, text, source_id, source_kind, approved: bool, active: bool`；owner=null 只表示管理員核准的共享知識，不表示所有訪客私人資料。
- `visible_chunk(chunk: MemoryChunk, identity: IdentityResult) -> bool`；SQL 查詢先做 owner／active／approved 篩選，再做 cosine，不能先全域 top-k 後才掩蓋名字。
- `retrieve(identity, query: str, limit: int=4) -> list[MemoryChunk]`；不 accepted 只可取共享知識，未知對話不建立可跨輪取回的私人記憶。`Embedder.encode(texts: list[str]) -> list[list[float]]` CPU；候選 bge-small-zh-v1.5，revision／維度／正規化規則一併記錄。
- `DeepSeekClient.reply(messages: list[dict], request_id: str) -> dict` 回 text／實際 model／usage／status；帳戶實際 model ID 配置化，timeout、429、401、5xx、結構不合法分開。取消後不再提交新TTS；不因超時盲目重送可能已計費的請求。
- `GET/POST /api/users/{id}/memories`、`PATCH/DELETE /api/users/{id}/memories/{memory_id}` 只允許管理員；偏好／穩定事實由本人明示或人工確認才 approved，LLM候選不自動變事實。記憶同意另留用途紀錄，不以聲紋同意代替。

- [ ] 建立紅測試並執行 `python -m pytest tests/memory/test_scope.py -q`：

```python
from services.memory.schemas import MemoryChunk
from services.memory.retriever import visible_chunk
from services.voiceprint.matcher import IdentityResult

def test_unknown_cannot_read_private_memory():
    identity = IdentityResult("unknown", None, .2, .7, None, "unit-test")
    chunk = MemoryChunk("c1", "alice", "只給甲的資料", "s1", "memory", True, True)
    assert visible_chunk(chunk, identity) is False
```

- [ ] 實作安全檢索核心、schema／SQL索引與本人記憶刪除：

```python
def visible_chunk(chunk, identity):
    if not chunk.active or not chunk.approved:
        return False
    return chunk.owner_user_id is None or (
        identity.decision == "accepted" and chunk.owner_user_id == identity.user_id
    )
```

小規模先用 SQLite metadata＋CPU向量 cosine；私人向量與記憶放非同步目錄受限儲存、向量以 DPAPI保護。共享資料首版限管理員核准 UTF-8 txt/md，上傳限1MB，切段約300字、重疊50字、top4，僅作起始設定以檢索測試調整。記錄檔案 hash／來源／chunk，禁止任意抓網頁或上傳未審核醫療建議。

- [ ] 接官方 API，保留安全 DPAPI key；先核對實際可用模型，不宣稱 `deepseek-v4-pro`。拒絕 cloud_dialogue 時零外部呼叫，unknown 須會話明確同意且不帶前人上下文；允許時只送必要文字，不送錄音／向量／整包個資。引用資料包在不可信資料區，不能覆寫身份、system規則或觸發管理操作。回覆保存 source_id／turn_id，檢索不足明示，不捏造引用。
- [ ] 執行 `python -m pytest tests/memory tests/dialogue/test_deepseek.py -q`；加入 A→B、accepted→unknown、已停用、刪記憶／撤同意後快取失效、prompt injection、無召回、429／401／timeout、明確拒絕雲端零請求。假 HTTP 回傳單元測試不得用真 key；經同意另跑一次短雲端 smoke 並記錄 model／耗時，不記 key。
- [ ] 以核准測試知識＋虛構個人記憶完成可追溯展示；真人資料只在有同意後使用，記錄此次 RAG 已實作的範圍與限制，不宣稱治療能力。

## P1-08：統一語音流程、取消、虛擬字幕與角色

**Files**

- Create: `services/dialogue/pipeline.py`、`services/dialogue/turns.py`、`services/dialogue/routes.py`、`simulator/screen.js`、`simulator/playback_state.mjs`、`simulator/assets/ASSET_LICENSES.md`。
- Modify: `services/dialogue/app.py`、`services/device_gateway/contracts.py`、`services/device_gateway/pc.py`、`simulator/app.js`、`simulator/player.js`、`simulator/simulator.css`。
- Test: `tests/dialogue/test_audio_pipeline.py`、`tests/dialogue/test_cancel.py`、`tests/frontend/playback.test.mjs`、`tests/security/test_identity_boundary.py`。

**Interfaces**

- `POST /api/audio/process` multipart `request_id, session_id, device_id, audio, language?`；身份由後端判定，不接受客戶端 user_id 作真人證據。session／device 綁定權限，重試同 request_id 回同 turn，不重複寫入／計費。
- `process_turn(request, providers) -> TurnResult`；包含 `turn_id, identity, transcript, emotion, risk, reply, sources, stages, latency_ms`。stages 分 ASR／identity／analysis／LLM／TTS 的狀態，P1-09 加入實際分析 provider前不假報成功。
- `POST /api/turns/{turn_id}/abort` 只限所屬裝置／管理員；`TurnRegistry.begin(session_id, request_id) -> str` 與 `cancel(turn_id) -> None`、`is_active(turn_id) -> bool` 控制延遲結果。
- `shouldApplyEvent(activeTurnId, event) -> boolean` 用於 PC；`tts.segment` 句序入隊，真正播放開始切字幕／speaking，ended發ACK，所有segments completed後才idle。

- [ ] 建立紅測試並執行 `node --test tests/frontend/playback.test.mjs`：

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import { shouldApplyEvent } from '../../simulator/playback_state.mjs';

test('late captions cannot revive an aborted turn', () => {
  assert.equal(shouldApplyEvent('new-turn', {turn_id: 'old-turn'}), false);
  assert.equal(shouldApplyEvent(null, {turn_id: 'old-turn'}), false);
});
```

- [ ] 實作核心、音訊入口及有界並行 ASR／voiceprint：

```javascript
export function shouldApplyEvent(activeTurnId, event) {
  return activeTurnId !== null && activeTurnId === event.turn_id;
}
```

pipeline先品質→身份／ASR並行→核對consent／受限記憶→本地風險與LLM→朗讀前輸出檢查→逐句TTS→事件／保存。只有accepted可帶該人上下文；未知每輪預設無私人跨輪記憶，換身份重建上下文，不能以 device_id串接。舊 `/api/chat` 限管理員＋demo mode＋demo資料，不能繞過正式身份隔離。

- [ ] 實作320×240邏輯畫布，上方連線／身份確認狀態，中間2D角色，下方三至四行字幕；角色語意 neutral/listening/thinking/speaking/caring/happy/error，未知表情neutral，關懷不默認happy。採原創簡單圖形或逐項授權資源並留license；長句可分頁但不捏造字級時間戳；idle30秒隱藏敏感字幕。PC顯示效果不等於ESP字型／記憶體通過。
- [ ] 執行 `python -m pytest tests/dialogue tests/security/test_identity_boundary.py -q` 及 Node 測試。驗證request重送、同裝置單輪排他、下一人換身份、LLM遲到／TTS失敗、播放中停止、WS重連不重播舊輪次、media撤銷、generated≠completed；真人以耳機完成一句負面心情→關懷語音→字幕，再測連續多句。
- [ ] 工作紀錄保存各階段耗時與失敗注入結果；全流測量首音延遲、整輪耗時、播放時長，先建立基準，不預先保證固定秒數。

## P1-09：個人分析、風險與準確的 Dashboard

**Files**

- Create: `services/analysis/schemas.py`、`services/analysis/risk.py`、`services/analysis/summary.py`、`services/analysis/routes.py`、`services/dashboard/analytics.js`、`configs/risk_rules.json`。
- Modify: `services/dialogue/pipeline.py`、`services/dashboard/read_model.py`、`services/dashboard/user_detail.js`、`services/dashboard/dashboard.js`、`services/storage/migrations.py`。
- Test: `tests/analysis/test_risk.py`、`tests/analysis/test_summary.py`、`tests/dashboard/test_time_ranges.py`、`tests/dashboard/test_user_detail.py`。

**Interfaces**

- `analyze_local(text: str) -> dict` 回 `risk_level, evidence, detector_version, status`；僅明確文字規則，不聲稱聲音情緒模型。LLM結果固定schema，`analysis_status`可ok／partial／unavailable。
- `merge_risk(local_level: str, llm_level: str | None, llm_status: str) -> dict`；任一經驗證urgent保留警示，不要求兩者同意，失敗也不抹掉已存在事件。
- 主規格 user conversations／emotion-trend／risk-events；risk acknowledge只改review_status=acknowledged，resolve需管理員備註；不自動因後續正常對話消除。
- `GET /api/users/{id}/summary?period=day|week` 按需生成、缓存帶来源turn IDs，雲端同意／成本提示；`PATCH /api/conversations/{id}/identity` 管理員人工更正且留舊／新歸屬、原因，不偽造聲紋分數，重算統計及失效摘要／記憶。
- 對話時長分 session起止、有效語音長度、模型耗時、播放時長，不能互相替代；idle五分鐘結束session，狀態時間UTC，今日以香港本地午夜換算UTC查詢。

- [ ] 寫紅測試、執行 `python -m pytest tests/analysis/test_risk.py -q`：

```python
from services.analysis.risk import merge_risk

def test_cloud_outage_keeps_local_urgent_signal():
    result = merge_risk("urgent", None, "unavailable")
    assert result["risk_level"] == "urgent"
    assert result["analysis_status"] == "partial"
```

- [ ] 實作風險合併核心及規則版本／來源保存：

```python
def merge_risk(local_level, llm_level, llm_status):
    rank = {"none": 0, "attention": 1, "urgent": 2}
    level = max([local_level, llm_level or "none"], key=rank.__getitem__)
    return {"risk_level": level, "analysis_status": "ok" if llm_status == "ok" else "partial"}
```

呼叫前schema驗證級別；ASR／本地都無有效文字時analysis_status=unavailable且risk_level=null，不走上面有有效local結果的分支。加入否定／引述／第三人故事测试，一句「心情不好」不能直接等於urgent。所有事件顯示「需人工確認、可能誤判」，未識別者警示放總列表不塞進他人頁。

- [ ] 接真資料卡片：最近心情＋分析來源／時間、七／三十日趨勢、對話時間軸／source／模型／播放狀態、風險處理紀錄。用參數化SQL實作今日／24h／個人篩選和分頁；demo與註冊音訊不混入真人統計。無電量／sensor值顯示不支援。
- [ ] 執行 `python -m pytest tests/analysis tests/dashboard -q`；時間測試包含香港午夜邊界、23h59m／24h01m、空資料、停用／刪除、更正歸屬、重算摘要、llmJSON壞格式、ack不resolve。人工核對圖表分母／時間區间／個人路由與長文字安全呈現。
- [ ] 交付版本 C，展示一名已註冊者、一名未知者及本地urgent＋雲端失敗案例；使用虛構風險資料，不讓演示告警被誤認真人事件。

## P1-10：裝置契約、MQTT／UDP 本機預驗證、一鍵整合

**Files**

- Create: `services/device_gateway/capabilities.py`、`services/device_gateway/xiaozhi_adapter.py`、`services/device_gateway/mqtt_udp.py`、`simulator/protocol_peer.py`、`configs/mosquitto.local.conf`、`scripts/test-protocol.ps1`、`docs/PC_ACCEPTANCE.md`、`docs/DEVICE_PROTOCOL.md`、`docs/PHASE1_HANDOFF.md`。
- Modify: `services/device_gateway/events.py`、`services/device_gateway/mock_gateway.py`、`simulator/heartbeat.py`、`configs/launcher.json`、`scripts/setup-project.ps1`、`scripts/start-project.ps1`、`scripts/stop-project.ps1`、`scripts/launcher-common.ps1`、`docs/STARTUP_GUIDE.md`、`docs/IMPLEMENTATION_STATUS.md`、`README.md`。
- Test: `tests/device_gateway/test_contracts.py`、`tests/device_gateway/test_mqtt_udp.py`、`tests/test_project_launcher.py`、`tests/simulator/test_end_to_end.py`。

**Interfaces**

- `validate_capabilities(payload: dict) -> dict`：輸入／輸出codec、sample_rate、frame_ms、display width/height、playback_ack、enrollment_v1；不支持值回明確錯誤，未上報不造假。
- `playback_state(sent: bool, ack: str | None) -> str`；原版無ACK只能sent_unconfirmed，新韌體協商後才有completed。PC與MQTT/UDP adapter共同消費P1-04事件，不各跑一套LLM。
- `DEVICE_PROTOCOL.md` 固定內部v1與上游參考commit；在實作前完整讀該commit的mqtt-udp／websocket文件及gateway程式，再用真實protocol fixtures建立encode/decode測試。採用上游加密／session／nonce／序號規則，不自行發明不相容封包、不把WebM當裸Opus。
- 既有 `mock_gateway.py` 保持明確demo身份；`mqtt_udp.py` 是新實際通訊adapter。本地broker只綁loopback、獨立測試port與token；LAN／防火牆修改留P2。MQTT只載控制，UDP音訊有限緩衝，stale／duplicate／wrong-session丟棄。

- [ ] 建紅測試、執行 `python -m pytest tests/device_gateway/test_contracts.py -q`：

```python
from services.device_gateway.capabilities import playback_state

def test_no_ack_is_not_playback_success():
    assert playback_state(True, None) == "sent_unconfirmed"
    assert playback_state(True, "completed") == "completed"
```

- [ ] 實作核心／協商與事件映射，未知表情neutral，缺裝置功能明示unsupported：

```python
def playback_state(sent, ack):
    if ack in {"started", "completed", "interrupted", "failed"}:
        return ack
    return "sent_unconfirmed" if sent else "not_sent"
```

ACK接收層先驗證device/session/turn/segment所有權、句序、狀態合法及去重，不能只信字串。fixtures記錄來源commit／授權；以Python模擬peer完成loopback控制＋音訊上／下行、丟包／亂序／重複／過期session，並測新v1擴充協商。這是協議預驗證，不記為ESP通過。

- [ ] 啟動器管理API、必要CPU runner、可選本地broker/gateway；PID／port必須驗證屬於本專案，不殺其他Python。一般啟動不下載大模型、不預設打付費健康請求；首次設定才列明需要的元件與下載。開發reload不要反覆重載所有模型，將耗時runner隔離；停止後關本專案背景程序並保留DB。背景Windows helper使用Hidden。
- [ ] 執行 `python -m pytest -q`、`node --test tests/frontend/*.test.mjs`；將需本地broker的測試標記protocol_integration，經明確啟動獨立broker後用 `powershell -NoProfile -File scripts/test-protocol.ps1` 執行，所有DB與媒體隔離。人工用BAT啟動／重複點擊／停止／再次啟動驗證，真人錄音與雲端測試在操作表中另行逐項確認。
- [ ] 交付版本 D：模型manifest、測試通過／失敗清單、真人FA／FR次數、語言／延遲、軟體模組版本、裝置契約、ESP未驗證欄位、啟動／修改／錯誤處理文檔。更新 IMPLEMENTATION_STATUS 只寫實證，不用歷史「18 passed」代替本輪結果。

## 總驗收與停止點

第一階段必須包含PC真實錄音與後端音訊播放，不以mock通過作真人或聲紋准确性證明。若使用者尚未在場，交付狀態寫「軟體自動測試完成，真人驗收待操作」，不能宣稱第一階段全部驗收完成。

只有同時滿足總覽第一階段驗收門檻，才標記 Phase 1 accepted；ESP保持未連接／未驗證。此時停止新增硬體功能，等待使用者通知到貨。沒有ESP不阻止P1-01～P1-10的軟體開發，但實際TTS語言、模型相容性或真人測試缺口需明確回報，不能默默刪除驗收項。
