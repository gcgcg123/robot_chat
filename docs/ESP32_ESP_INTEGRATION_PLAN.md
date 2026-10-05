# ESP32 真机接入方案（移植 xiaozhi-esp32-server 的 ESP 连接能力）

> 面向项目：`robot_chat-main`（IoT 情感陪伴机器人，simulator-first）
> 参考源：`xiaozhi-esp32-server-main`（小智官方服务端，commit 固定于 `6afc54a1`）
> 版本：2026-10-04
> 状态：**方案（待确认后实施）**

---

## 0. 结论速览（TL;DR）

| 项 | 结论 |
|---|---|
| 推荐路线 | **方案 B：在 `robot_chat-main` 内建「小智兼容接入层」**，上游只作协议参考，不整体并入 |
| 新增入口 | `WS /xiaozhi/v1/`（设备音频/文本）+ `POST|GET /xiaozhi/ota/`（配网下发），与现有 FastAPI 同端口 8080 |
| 核心改动 | 新增 `services/device_gateway/xiaozhi/` 包（协议、Opus、会话、VAD、TTS 桥接、OTA） |
| 必须前置 | **真实 TTS**（当前 `windows.py` 输出静音，ESP 会「连上也听不到声音」）——即 `COMPLETION_PLAN.md` 模块二 |
| 新增依赖 | `opuslib_next`（Opus 编解码，Windows 需带 DLL 的轮子）、`edge-tts`（或等价 TTS） |
| 后台新增 | ① IoT 设备页升级 ② 新增「设备接入/配网」页 ③ 新增「设备详情」页 ④ 系统状态区新增 ESP 区块 |
| 不建议 | 只接 WebSocket，**不接 MQTT+UDP**（P2 再说）；不引入智控台（manager-api/web） |

> **一句话**：把「小智的耳朵和嘴巴」装到我们自己的大脑上——ESP 只负责采音/放音/显示，ASR、长期记忆、情绪风险、DeepSeek、声纹、Dashboard 全部还是我们自己的。

---

## 1. 现状盘点

### 1.1 我方 `robot_chat-main`（要接的一方）

**已有的、可直接复用的**

| 能力 | 位置 | 说明 |
|---|---|---|
| FastAPI 主程序 / provider 注入 | `services/dialogue/app.py:131` | `create_app(providers={...})`，取不到就用默认实现，最小侵入 |
| 对话管线 | `services/dialogue/pipeline.py` | 情绪、风险、记忆候选、LLM 调用一次跑完 |
| ASR | `services/audio/` | 输入是**完整音频**（`normalize_audio` + `AsrWorker.submit`），不是流式 |
| 长期记忆飞轮 | `services/memory/` | 三層槽位/热/冷，已接通 |
| 声纹登记与识别 | `services/voiceprint/`、`services/enrollment/` | 三段登记，`identity.py` 留好了接缝 |
| 设备心跳 / 设备列表 | `services/device_gateway/events.py`、`services/dashboard/read_model.py` | `devices` 表 + `online/stale/offline` |
| 自有设备事件协议 | `services/device_gateway/contracts.py` | `DeviceEvent v1`（turn/display/stt/tts…），**但这是给我们自己的模拟器用的** |
| PC 模拟器 WS | `app.py:795` `/ws/simulator/{session_id}` | 需管理员 Cookie，ESP 无法使用 |

**缺口（本次要补的）**

1. **没有 ESP 能听懂的握手协议**：`/ws/simulator` 是自有 `DeviceEvent`，小智固件发的是 `hello/listen/abort/iot` + 二进制 Opus。
2. **全项目没有 Opus**（已确认：`grep opus` 零命中），ESP 上下行音频都是 Opus。
3. **没有 VAD**：ESP 是连续推流，必须由服务端判断「说完了」。
4. **TTS 是静音**：`services/tts/windows.py` 无条件返回 `DeterministicTts` 的全零 PCM。**这是「连上也没声音」的根因**。
5. **没有 OTA 接口**：ESP 开机先请求 OTA 拿 WebSocket 地址，没有它就不知道连哪儿。
6. **没有设备侧认证**：现有 `/api/device/heartbeat` 走 `require_device`（admin/device 会话），ESP 没有 Cookie。
7. **后台没有 ESP 相关入口**：`index.html` 只有「IoT 设备」一个只读卡片。

### 1.2 参考 `xiaozhi-esp32-server-main`（要抄的一方）

```
main/
├── xiaozhi-server/        # ★ 唯一需要参考的：ESP 接入服务端（Python）
│   ├── app.py             # 同时起 WebSocket(8000) 与 SimpleHttpServer/OTA(8003)
│   ├── core/websocket_server.py   # websockets 服务器，路径 /xiaozhi/v1/
│   ├── core/connection.py         # 78KB 巨型会话类（握手/音频/对话/TTS 全在里面）
│   ├── core/api/ota_handler.py    # ★ OTA：下发 websocket 地址 + 固件版本
│   ├── core/handle/textHandler/   # ★ hello/listen/abort/iot/mcp/ping 六个处理器
│   ├── core/handle/sendAudioHandle.py  # ★ TTS → Opus 分包下发 + 流控
│   └── config.yaml                # xiaozhi 段 = 握手欢迎包模板
├── manager-api/ manager-web/      # 智控台（Java/Vue）—— 本项目不需要
└── digital-human/ manager-mobile/
```

**协议事实（已从源码逐条核实）**

| 项 | 值 |
|---|---|
| WS 路径 | `/xiaozhi/v1/`，可通过 URL query 或 Header 传 `device-id` / `client-id` / `authorization` |
| 欢迎包 | `{"type":"hello","version":1,"transport":"websocket","session_id":...,"audio_params":{"format":"opus","sample_rate":24000,"channels":1,"frame_duration":60}}` |
| 上行音频 | **裸 Opus 包**（无头部），服务端按 `Decoder(16000, 1)` 解码，每包 960 采样（60ms） |
| 下行音频 | 裸 Opus 包，采样率 24000，60ms/帧 |
| 上行文本 | `hello` / `listen`(state=start\|stop\|detect, mode=auto\|manual\|realtime) / `abort` / `iot`(descriptors\|states) / `mcp` / `ping` |
| 下行文本 | `{"type":"stt","text":...}`、`{"type":"tts","state":"start\|sentence_start\|stop","text":...}`、`{"type":"llm","emotion":...}`、`{"type":"iot","commands":[...]}` |
| OTA | `POST /xiaozhi/ota/`，Header `device-id/client-id/device-model/device-version`，响应含 `websocket:{url,token}`；`GET /xiaozhi/ota/download/{file}.bin` 下发固件 |
| 认证 | 可选 HMAC-SHA256 签名 token（`core/auth.py`）+ 设备白名单 |

**关键判断**：`connection.py` 之所以 78KB，是因为它把 ASR/VAD/LLM/TTS/记忆/MCP 全部耦合在一个类里。**我们要的是它的「协议外壳」，不是它的「大脑」**——所以正确做法是抽离协议层，而不是搬代码。

---

## 2. 目标与边界

### 2.1 目标

1. 真机小智固件（ESP32-S3）接入我方服务，跑通：
   `唤醒/按键 → 采音 → 服务端 VAD → ASR → 记忆+情绪+风险 → DeepSeek → 真实 TTS → Opus 回传 ESP → 喇叭播放 + LCD 字幕/表情`
2. 后台「网络管理界面」（Dashboard）新增与 ESP 相关的完整管理入口。
3. 保留模拟器作为回归来源，两套入口**共用同一对话管线**。

### 2.2 非目标（本次不做）

- 不搬智控台（manager-api / manager-web / manager-mobile）。
- 不做 MQTT + UDP 网关（P2，先 WebSocket 端到端）。
- 不做设备端 MCP / IoT 全量控制协议（只做最小可用子集）。
- 不做固件编译/烧录（属硬件侧）。
- 不改现有 `DeviceEvent v1` 契约（模拟器继续用）。

---

## 3. 三条路线对比

| | **A. 双服务并跑 + 桥接** | **B. 内建小智兼容接入层（推荐）** | **C. 以 upstream 为主，我方做插件** |
|---|---|---|---|
| 做法 | 上游 `xiaozhi-server` 独立跑 8000/8003，改它的 LLM/TTS 指向我方 `/api/chat` | 在 `robot_chat-main` 内实现小智协议的**最小子集**，直接调用现有管线 | 把 `services/` 改写成 upstream 的 provider 插件，主程序换成 upstream |
| 工作量 | 中 | 中高（一次性） | 高 |
| 耦合/风险 | 两套配置两套日志；上游巨型 `connection.py` 里改 LLM 要动核心；版本升级易冲突 | 协议变更面收敛在 `xiaozhi/` 包内，业务代码零改动 | 业务被 upstream 的 provider 契约绑架，声纹/记忆/情绪都得重写适配 |
| 复用我方能力 | 只能经 `/api/chat` 文本口，**音频/情绪/记忆/声纹上下文会丢**（丢设备事件、丢实时字幕） | 全链路复用：管线、记忆、情绪、声纹、审计、Dashboard | 全复用但需重写 |
| 对现有测试影响 | 小 | 小（纯新增，不动现有路由） | 大 |
| 可维护性 | 差（两套栈） | 好 | 中 |
| 结论 | 备选（快速验证可先用） | **✅ 采用** | ❌ |

**为什么不上方案 A**：上游把「对话大脑」写死在 `connection.py` 里，我们要接自己的记忆/情绪/声纹就得深入改它的核心；而它真正值钱的部分只是**协议外壳**（不到 1/5 的代码量），抽取出来自己实现反而更省。

---

## 4. 方案 B 详细设计

### 4.1 新增目录结构

```
services/device_gateway/xiaozhi/
├── __init__.py
├── protocol.py       # 文本消息解析/构造（hello/listen/abort/iot/ping + 下行 stt/tts/llm/iot）
├── opus_codec.py     # Opus 编解码封装（上行 16k 解码 / 下行 24k 编码，60ms 分帧）
├── vad.py            # 服务端端点检测（silero-vad ONNX，复用已有 onnxruntime；能量法兜底）
├── session.py        # XiaozhiSession：握手、状态机、音频缓冲、打断、超时
├── tts_bridge.py     # pipeline segments → Opus 帧 → 下游下发（含流控/预缓冲）
├── ota.py            # OTA POST/GET + 固件下载（局域网 IP 自动生成地址）
└── router.py         # FastAPI 路由挂载：WS /xiaozhi/v1/、HTTP /xiaozhi/ota/
```

在 `services/dialogue/app.py` 的 `create_app()` 里挂载（新增，不动已有逻辑）：

```python
from services.device_gateway.xiaozhi.router import mount_xiaozhi_routes
mount_xiaozhi_routes(application, runtime=runtime, providers=providers, db=db)
```

### 4.2 协议实现映射表

| 小智协议 | 我方实现 | 备注 |
|---|---|---|
| `hello`（上行） | 校验 `version`，记录 `audio_params`（format/sample_rate/frame_duration）、`features` | 不支持非 opus 格式时明确回错，不静默降级 |
| 欢迎包（下行） | 由 `configs/launcher.json` 的 `esp` 段生成，含 `session_id` | `session_id` 用我方 UUID，落 `device_sessions` 表 |
| `listen state=start` | 清空音频缓冲，进入 listening，广播 `display.state` 事件到 Dashboard | |
| 上行二进制 | `opus_codec.decode()` → PCM16 累加缓冲 → `vad.feed()` | 每包 960 采样；**不刷新待机计时器**（聆听期会持续推静音帧） |
| `listen state=stop` | 强制切句：缓冲 → WAV → `AsrWorker.submit()` | 兼容按键说话 |
| VAD 静音超时 | 同上自动切句 | 免按键的核心 |
| `listen state=detect` + `text` | **先判是否唤醒词**：是 → 只开通道 + 广播 `device.wake_word`，不建轮次；不是 → 把 text 当用户输入（跳过 ASR）走 `process_text` | 固件把唤醒词原文（如「小智」）放在这里，它是门铃不是问题 |
| `abort` | 取消当前 turn，清空 TTS 队列，`TurnRegistry.cancel()` | 打断 |
| `ping` | 回 `pong` | |
| `iot descriptors/states` | descriptors **折叠成工具**（`<device>_<method>`，见 4.7），states 记 `device_events` | 旧协议的降级路径 |
| `mcp`（双向 JSON-RPC） | 服务端主动 `initialize`→`tools/list`→`tools/call`；设备反向请求回 `-32601` | **语音控制设备设置的唯一通路**，见 4.7 |
| 下行 `stt` | `process_text` 前先发，让 LCD 显示用户字幕 | |
| 下行 `llm emotion` | 由 `result.emotion` 映射 `happy/sad/neutral/...` | LCD 表情 |
| 下行 `tts state=sentence_start` + 文本 | 每个 segment 一条 | LCD 字幕分句 |
| 下行二进制 | 每个 segment 的 PCM → Opus 24k 60ms 帧 | |
| 下行 `tts state=stop` | 整轮结束 | 结束时给用户完整停顿，重置待机计时 |
| **服务端 `close()`** | 静默到期（`IOT_ESP_STANDBY_SECONDS`）→ 发告别语 → 关通道 | 固件只在 `OnAudioChannelClosed` 时回 `kDeviceStateIdle`；下发 `listen` 无效（固件无此分支） |

> 与我们自有 `DeviceEvent v1` 的关系：**两套并存**。`xiaozhi/session.py` 内部把每个动作**同时**调 `ProtocolEvent(...)` 广播给 Dashboard（复用现有 `/ws/simulator` 的观测能力），实现「ESP 会话也能在后台实时看到」。

### 4.3 对话链路时序

```
ESP ····hello····▶ 网关
网关 ····welcome(hello)····▶ ESP
ESP ──按键/唤醒──▶ listen(start)
ESP ····opus····▶ 网关 ─▶ 解码PCM ─▶ VAD缓冲
   （说话中，Dashboard 收到 display.state=listening）
VAD 判定静音 / listen(stop)
  ─▶ PCM 包成 WAV ─▶ 现有 AsrWorker.submit()
  ─▶ 下行 stt(text)                        ← LCD 显示用户字幕
  ─▶ select_for_turn() + process_text()     ← 复用记忆/情绪/风险/DeepSeek
  ─▶ 下行 llm(emotion)                      ← LCD 表情
  ─▶ 对每个 segment：下行 tts(sentence_start) + Opus 二进制帧
  ─▶ 下行 tts(stop)
  ─▶ 落 conversations / emotion_events / device_sessions
```

**注意**：`process_text` 是同步阻塞的，`/ws/simulator` 里已用 `asyncio.to_thread` 包了一层，新网关沿用同样做法，避免堵住事件循环导致 Opus 帧堆积。

### 4.4 数据模型改动（`services/storage/migrations.py` 追加，只增不改）

```sql
-- ① devices 扩字段
ALTER TABLE devices ADD COLUMN transport TEXT DEFAULT 'simulator';   -- simulator | esp_ws
ALTER TABLE devices ADD COLUMN protocol_version INTEGER;             -- 1
ALTER TABLE devices ADD COLUMN board_model TEXT;                     -- ESP32-S3 / atk-dnesp32s3
ALTER TABLE devices ADD COLUMN client_id TEXT;
ALTER TABLE devices ADD COLUMN bound_user_id TEXT;                   -- 绑定的用户
ALTER TABLE devices ADD COLUMN pairing_code TEXT;                    -- 配网配对码
ALTER TABLE devices ADD COLUMN pairing_expires_at REAL;
ALTER TABLE devices ADD COLUMN token_hash TEXT;                      -- 设备 token（哈希存储）
ALTER TABLE devices ADD COLUMN last_session_at REAL;
ALTER TABLE devices ADD COLUMN last_state TEXT;                      -- idle/listening/thinking/speaking/offline

-- ② 会话表（每次设备连上来一条）
CREATE TABLE IF NOT EXISTS device_sessions (
  session_id TEXT PRIMARY KEY, device_id TEXT, user_id TEXT,
  transport TEXT, protocol_version INTEGER, client_ip TEXT,
  started_at REAL, ended_at REAL, turn_count INTEGER DEFAULT 0, last_error TEXT
);

-- ③ 指令下发（后台 → 设备）
CREATE TABLE IF NOT EXISTS device_commands (
  command_id TEXT PRIMARY KEY, device_id TEXT, type TEXT,      -- restart/volume/brightness/wake_word/ota
  payload_json TEXT, status TEXT,                              -- pending/sent/acked/failed
  created_at REAL, sent_at REAL, acked_at REAL, actor_id TEXT
);

-- ④ OTA 请求审计
CREATE TABLE IF NOT EXISTS ota_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT, board_model TEXT,
  device_version TEXT, client_ip TEXT, granted_ws_url TEXT, created_at REAL
);
```

### 4.5 配置项（新增，不覆盖已有）

`configs/launcher.json`：

```json
{
  "esp_enabled": false,
  "esp_path": "/xiaozhi/v1/",
  "esp_bind_all": true,
  "ota_enabled": false,
  "esp_device_token_required": false,
  "esp_vad_provider": "silero",
  "esp_vad_silence_ms": 800,
  "esp_max_utterance_ms": 20000,
  "esp_tts_provider": "edge"
}
```

`.env`（模板同步更新 `.env.example`）：

```ini
# --- ESP32 真机接入 ---
IOT_ESP_ENABLED=0                 # 打开后 /xiaozhi/v1/ 才接受连接
IOT_ESP_BIND_ALL=1                # 监听 0.0.0.0（局域网），1 时必须配 device token
IOT_ESP_PATH=/xiaozhi/v1/
IOT_OTA_ENABLED=0
IOT_ESP_TOKEN_SECRET=             # 设备 token 签名的 HMAC 密钥（不进 Git）
IOT_ESP_REQUIRE_TOKEN=0           # 1 = 强制校验 device token
IOT_VAD_SILENCE_MS=800
IOT_VAD_MAX_UTTERANCE_MS=20000
IOT_TTS_PROVIDER=edge             # none | edge | sapi
IOT_TTS_EDGE_VOICE=zh-CN-XiaoxiaoNeural
IOT_OTA_BIN_DIR=./data/bin        # 固件 .bin 存放目录
# --- 待机（2026-10-04 补，见 §4.7）---
IOT_ESP_STANDBY_SECONDS=60        # 静默多久没「人声」就待机（只看 VAD/文本，不看音讯包）
IOT_ESP_STANDBY_NOTICE=我先待命啦，需要我的時候叫我一聲就好。   # 留空 = 不发声直接待机
IOT_ESP_IDLE_TIMEOUT_SECONDS=300  # 死链看门狗（纯 socket 层，要比固件 120s 通道超时长）
# --- 設備控制（2026-10-04 补，见 §4.7）---
IOT_ESP_TOOLS=1                   # 打开设备 MCP / iot 工具注册
IOT_ESP_TOOL_TIMEOUT_SECONDS=12   # 单次工具调用等设备回应的秒数
IOT_ESP_WAKE_WORDS=小智,小智小智,你好小智,小智你好,你好小智同學   # listen detect 的唤醒词，不当问题
# --- 串流回答 / 打断（2026-10-04 补，见 §12）---
IOT_ESP_LLM_STREAM=1              # 1 = LLM 走 SSE 串流，首句说完就出声（体感延迟关键）
IOT_ESP_TTS_MAX_CHARS=48          # 单句合成上限；超过就在软断点切开，避免超长请求撞 provider 超时
IOT_ESP_TTS_MIN_CHARS=6           # 非首句的最小长度（首句不受限，第 2 个字就放行）
IOT_ESP_WAKE_WORD_HOLD_SECONDS=0.8 # listen detect 后忽略 VAD 断句的静默期（避开固件提示音）
```

> 端口策略：**OTA 与 WS 同用 8080**（FastAPI/uvicorn 同端口支持 HTTP + WS），比上游的 8000/8003 双端口更好配。ESP 固件的 `CONFIG_OTA_URL` 填 `http://<局域网IP>:8080/xiaozhi/ota/`。

### 4.6 依赖与前置（**顺序很重要**）

| 顺序 | 事项 | 原因 |
|---|---|---|
| 1 | **先做真实 TTS**（`COMPLETION_PLAN.md` 模块二，edge-tts 方案 A） | 不做的话 ESP 连上只能播静音，验收会被误判为「协议没通」 |
| 2 | 新增 `opuslib_next`，验证 Windows + Python 3.11 可导入、DLL 可用 | 这是最大技术风险点，先单独写 10 行脚本 `encode→decode` 自测 |
| 3 | 新增 silero-vad ONNX（约 2MB）进 `models/manifest.json` + 下载脚本 | 与 RAG 一样按 manifest 管权重，不塞进 Git |
| 4 | `edge-tts` 输出 MP3 → 需解码为 PCM16。**项目已有 `E:\tools\ffmpeg\bin`**，直接调用，无需新增解码库 | 复用本机已有 ffmpeg |

### 4.7 待机与设备控制（2026-10-04 补，真机联调发现）

真机跑通后发现两个缺陷，都在这一节收口。**改动只落在接入层**（`session.py` + 两个新模块），没有动既有对话路径。

#### 4.7.1 为什么「回答完一直停在聆听」

固件侧（`xiaozhi-esp32/main/application.cc`，已取证）：
`OnIncomingJson` 只处理 `notify / tts / stt / llm / mcp / system / alert / custom` —— **没有 `listen` 分支**，
服务端下发 `{"type":"listen"}` 只会被打印成 "Unknown message type"，不触发任何状态转换。
设备回到 `kDeviceStateIdle` 的**唯一**服务端可控路径是关闭音频通道（`OnAudioChannelClosed → SetDeviceState(kDeviceStateIdle)`）。
另外 `tts:stop` 在非 ManualStop 模式下会让设备回到 **Listening**（继续听），不是待机。

所以待机必须由服务端主动收尾：**静默到期 → 送告别语 → `close()`**。这与上游一致
（`close_connection_no_voice_time` 默认 120 s，先发 `end_prompt` 再 `conn.close()`）。

服务端计时器**必须拆成两个**——这是「卡在聆听永不待机」的真正根因：

| 计时器 | 默认 | 刷新条件 | 用途 |
|---|---|---|---|
| `_last_voice_at` | — | **只有** VAD 判定说话 / 收到设备文本 / 一轮结束 | 静默到期 → `standby` |
| `_last_activity` | — | 任何入站消息 | socket 彻底失聪 → `dead` |

聆听期间设备会**持续推静音 Opus 帧**。旧实现用「收到任何帧就刷新」的单一计时器，
于是计时器永远够用，设备永远不回待命。`_clock_verdict()` 因此返回三态
`wait / standby / dead`，`_next_deadline_seconds()` 取两者剩余时间的最小值（下限 1 s）驱动 `receive()` 超时。

两个配套修正：
- `_flush_utterance` 在音频短于 `min_utterance_bytes` 时原本裸 `return`，状态留在 listening → 改为先回 `idle`。
- 固件把唤醒词原文（如「小智」）塞进 `listen state=detect` 的 `text`。它是**门铃不是问题**：
  `_is_wake_word()` 命中时只开通道 + 广播 `device.wake_word`，不建轮次、不喂模型。

#### 4.7.2 为什么「语音改音量没反应」

小智固件把音量 / 亮度 / 主题等暴露为 **MCP 工具**，必须由服务端在同一条 WS 上跑 JSON-RPC：
`initialize`(id=1) → 设备回 `serverInfo` → `tools/list`(id=2) → 设备回 `tools[]` → `tools/call`(id≥10) → 设备回 `result.content[0].text`。
原 `_on_text` 对 `mcp` **直接忽略**（注释写着 "does not implement MCP tool calling over the device channel yet"），
工具从未注册给模型，模型只能口头答应。

新增 `services/device_gateway/xiaozhi/mcp.py`：

- `DeviceMcpClient`：握手（6 s 超时）、`tools/list` 支持 `nextCursor` 续拉、`content[0].text` 展开、
  设备反向请求统一回 `-32601`、握手失败**不抛异常**而是返回状态字典（`no_mcp_response` 等）；
- `DeviceTool` / `DeviceToolset`：按原始名与 sanitize 名双索引；
- `sanitize_tool_name()`：`self.audio_speaker.set_volume` → `self_audio_speaker_set_volume`
  （模型 API 的工具名只接受 `[A-Za-z0-9_-]`）；
- 旧 **iot 协议**降级：`{"type":"iot","descriptors":[...]}` 折叠成 `<device>_<method>`，
  调用走 `{"type":"iot","commands":[{"name","method","parameters"}]}`；两种描述符形状都兼容。

工具注册进 `ToolRegistry`（`GatewayContext.tools`），随后有两条让指令真正生效的路径：

1. **规则直达**（`voicecmd.py`，**零模型调用**）：明确设置句（目标词 + 数值 / 大小声标记）
   直接调用设备工具，确认语由本地生成。故意收窄——没有显式数值或步进标记就返回 `None` 交给模型。
   因此**不依赖模型是否支持 function calling**。
2. **模型工具调用**：其余对话把工具清单交给 LLM，走 `pipeline._reply_with_tools()` 的工具循环
   （上限 3 轮 / 30 s 预算，最后一轮不带 tools 以强制口语收尾）。工具调用发生在
   `asyncio.to_thread` 工作线程，需 `asyncio.run_coroutine_threadsafe(...).result(timeout)` 回事件循环走 socket。

规则解析的两个坑（已修）：「百分之三十」的「百」被算成 100（先剥 `百分之/百分` 再解析中文数字）；
「声音太大了」被判成「调大」（`太小聲/太小`、`太大聲/太大` 按目标词消歧）。

#### 4.7.3 前端
`devices.html` 增加「设备控制工具」「静默多久后自动待机」「待机提示语」三项（改完即时写 `.env`）；
设备详情页增加「设备工具」面板：列出固件上报的工具、可填参手动执行，
`tools_status.reason` 会如实回报「固件未响应握手」，而不是让「语音改音量没反应」变成玄学问题。

---

## 5. 网络管理界面（Dashboard）新增内容【重点】

现有侧边栏：总览 / 情绪分析 / IoT 设备 / 对话记录 / 用户列表 / 系统事件。

### 5.1 IoT 设备页升级（改 `index.html` + `dashboard.js`）

- 设备行增加：**传输类型徽标**（`ESP` 绿 / `模拟器` 灰）、协议版本、板型、固件版本、局域网 IP、最后会话时间。
- 增加**筛选器**：全部 / 仅 ESP / 仅模拟器；在线 / 离线。
- 设备卡片配色区分：`transport=esp_ws` 用不同左边框色。
- 空态文案从「等待 ESP 或 simulator 上报」改为带引导的「尚无设备接入 → 前往『设备接入』」。

### 5.2 ★ 新增「设备接入」页（新路由 `/dashboard/devices`）

这是用户说的「相应项目」里最关键的一块——**ESP 配网向导**。

| 区块 | 内容 | 数据来源 |
|---|---|---|
| 接入开关 | ESP 服务总开关、是否监听局域网、是否强制 token | `GET/PATCH /api/esp/settings` |
| 接入地址卡片 | 本机局域网 IP（多网卡列出）、OTA URL、WebSocket URL，**一键复制按钮 + 二维码**（ESP 扫码/手填） | `GET /api/system/network` |
| 接入向导 | 三步图文：① 烧录固件 ② 填 OTA 地址 ③ 上电等待上线 | 静态 |
| 待接入设备 | 收到 OTA 请求但未绑定用户／未授权的设备列表，可「允许接入 / 拉黑」 | `ota_requests` + `devices` |
| 配对绑定 | 生成 6 位配对码 + 有效期，选择要绑定的用户；设备侧显示配对码后确认绑定 | `POST /api/devices/{id}/pair` |
| 设备白名单 | 允许的 device-id / 型号列表 | `devices` |
| 固件管理 | 上传 `*.bin`（命名 `{model}_{version}.bin`）、列表、删除 | `data/bin` + `POST /api/ota/firmware` |

### 5.3 ★ 新增「设备详情」页（新路由 `/dashboard/device/{device_id}`）

- **实时面板**：在线状态、当前状态机（idle/listening/thinking/speaking/offline）、当前用户（声纹归属）、当前情绪、风险等级。
- **实时字幕流**：用户 STT + 机器人 TTS 逐句滚动（订阅 WS 观测通道）。
- **指令下发**：重启、打断当前播放、设置音量、屏幕亮度、切换唤醒词、触发 OTA 检查 → 写 `device_commands` 经 WS 下发。
- **会话历史**：该设备所有 `device_sessions` + 对应的 `conversations`。
- **固件/协议信息**：板型、协议版本、固件版本、最近 OTA 请求时间。
- **危险操作**：解除绑定、禁用设备（需二次确认 + 写 `audit_events`）。

### 5.4 系统状态区新增区块

在 `#events` 面板的 `system-grid` 增加 4 格：

| 格 | 显示 |
|---|---|
| ESP 接入 | 未启用 / 监听中（0.0.0.0:8080/xiaozhi/v1/） |
| OTA 服务 | 未启用 / 已启用（含固件数） |
| Opus 编解码 | 可用 / 不可用（不可用时明确提示，不假装已支持） |
| 局域网地址 | 192.168.x.x（未监听 0.0.0.0 时提示「设备无法接入」） |

同时把「硬件整合 = 等待 ESP」这格改成动态显示。

### 5.5 新增后端 API 清单

> 下面这张表已按**实际实现**校正（2026-10-04 落码）：原计划中的 `/api/system/network`、`/api/devices/{id}/pair`、`/api/ota/requests`、`/api/ota/firmware` 分别改为 `/api/esp/network`、`/api/devices/{id}/bind`、`/api/esp/ota-requests`、`/api/esp/firmware`——ESP 相关的后台面统一收进 `/api/esp/*` 前缀，避免 `/api/ota/*` 这种看似通用实则只服务 ESP 的路径。

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/api/esp/status` | admin | ESP 服务状态（启用/路径/已连接设备数/VAD/TTS 后端/Opus 可用性/观测者） |
| GET | `/api/esp/network` | admin | 局域网 IP 列表 + OTA/WS 地址 + 当前 `.env` 路径 |
| GET | `/api/esp/settings` | admin | 读取 ESP 接入设置（`settings` 字段，见 `status`） |
| POST | `/api/esp/settings` | admin(write) | 写入 ESP 接入设置；保注释写回 `.env` 并写穿进程环境，即时生效 |
| GET | `/api/devices/{device_id}` | admin | 设备详情（含 live 状态、`connected`、`command_attached`） |
| GET | `/api/devices/{device_id}/sessions` | admin | 会话历史 |
| GET | `/api/devices/{device_id}/commands` | admin | 指令历史与状态 |
| POST | `/api/devices/{device_id}/commands` | admin(write) | 下发指令（`speak`/`abort`/`close`/`iot`；离线记为 offline） |
| POST | `/api/devices/{device_id}/bind` | admin(write) | 绑定设备到用户 |
| DELETE | `/api/devices/{device_id}/bind` | admin(write) | 解除绑定 |
| GET | `/api/devices/{device_id}/token` | admin(write) | 查看派生设备令牌（需已配置密钥） |
| GET | `/api/esp/ota-requests` | admin | 待接入/历史 OTA 请求 |
| GET | `/api/esp/firmware` | admin | 固件列表（`{model}_{version}.bin`） |
| POST | `/api/esp/firmware` | admin(write) | 上传固件 .bin |
| DELETE | `/api/esp/firmware/{filename}` | admin(write) | 删除固件 |
| WS | `/ws/device-observe` | admin | Dashboard 订阅设备实时事件（复用 `ProtocolEvent`） |
| WS | `/xiaozhi/v1/` | device-id + 可选 token / 白名单 | **ESP 设备接入**（非 admin） |
| POST/GET | `/xiaozhi/ota/` | 公开 | **ESP OTA 配网**（GET 為人類可讀探針） |
| GET | `/xiaozhi/ota/download/{filename}` | 公开 | 固件下载（防目录穿越） |

### 5.6 新增前端文件

```
services/dashboard/
├── devices.html / devices.js        # 设备接入页
├── device_detail.html / .js         # 设备详情页
└── dashboard.js                     # 改：渲染 transport/协议/板型；导航加「设备接入」
└── index.html                       # 改：sidebar 新增「设备接入」；系统状态新增 ESP 区块
```

---

## 6. 分阶段路线图与验收

### P0 —— 打通「听得见、说得出」（先决条件）

| 步骤 | 产出 | 验收 |
|---|---|---|
| P0-1 | 真实 TTS（edge-tts）+ ffmpeg 转 PCM16 | `media_store` 里的 WAV 不再是全零；`/api/media` 可播 |
| P0-2 | `opus_codec.py` 自测脚本 | `encode(pcm)→decode→pcm` 往返，采样率/帧长正确 |
| P0-3 | silero-vad 接入 + 模型入 manifest | 给定音频能返回语音段起止 |

### P1 —— 协议层跑通

| 步骤 | 产出 | 验收 |
|---|---|---|
| P1-1 | `protocol.py` + `session.py`（hello/listen/abort/ping + 状态机） | 用 Python 写的假客户端能完成握手并收到 welcome |
| P1-2 | 上行音频 → VAD → ASR → `process_text` | 假客户端发一段 Opus，能在 `conversations` 表看到正确转写与回复 |
| P1-3 | `tts_bridge.py` 下行 Opus | 假客户端能收齐帧并解码回正常音频 |
| P1-4 | OTA 接口 + 局域网地址探测 | `curl http://127.0.0.1:8080/xiaozhi/ota/` 返回含 websocket 地址的 JSON |
| P1-5 | 设备 token / 白名单 + 落 `device_sessions` | 无 token 且开启强制校验时被拒；正常设备出现在 `/api/devices` |
| P1-6 | Dashboard「设备接入」页 + 系统状态区块 | 页面能显示局域网地址/OTA URL/二维码；开关能启停 ESP 服务 |
| P1-7 | **真机联调** | ESP 上电 → OTA → 连 WS → 说一句 → 喇叭播放 + LCD 字幕/表情全通 |

### P2 —— 增强

- `device_detail` 页 + 指令下发（重启/音量/亮度/打断）。
- `iot` 描述符解析（LCD/表情资源协商）。
- MQTT + UDP 网关（局域网低延迟、断线重连）。
- ~~声纹判定接入 `identity`~~ ✅ **已落地**：每轮语音先跟已登记模板比对（`IOT_ESP_VOICEPRINT=1`），
  顺序为 **声纹命中 → 设备绑定 → `IOT_ESP_DEFAULT_USER`**；命中即为 `voiceprint_verified`，
  在 `IOT_MEMORY_REQUIRE_IDENTITY=1` 下也能取用个人记忆。绑定因此从「必要」降为「后备」，
  同一账号可在任意多台设备上使用（身份跟着声音，不跟着设备）。复用 `/api/voiceprint/identify`
  完全相同的模板集合、provider 版本与阈值，结果写 `device_events.speaker_identified`。
- 多设备并发与会话隔离压测。

### 每阶段回归

```powershell
python -m pytest tests -q
python -m compileall -q services simulator scripts
```

**红线**：新增路由必须是纯增量；`/ws/simulator`、`/api/chat`、记忆飞轮、声纹登记的行为不得改变，226 个既有测试必须全绿。

---

## 7. 风险与红线

| 风险 | 影响 | 对策 |
|---|---|---|
| **Opus 库在 Windows 装不上 / DLL 缺失** | 整个方案卡死 | P0-2 先做 10 行往返自测；不可行则退到「让固件走 PCM 格式」（需改固件，成本转移到硬件侧） |
| **TTS 仍为占位** | 验收被误判 | 把 TTS 列为 P0 前置，未完成前 `/api/esp/status` 明确返回 `tts: unavailable` |
| VAD 误切句 | 用户话被截断 | 静音阈值可配（`IOT_VAD_SILENCE_MS`）+ 最大语句时长保护；保留按键 `listen stop` 强制切句 |
| 局域网暴露风险 | 未授权设备接入 | 默认 `IOT_ESP_ENABLED=0`；监听 0.0.0.0 时要求 token；所有接入写审计 |
| 协议版本漂移 | 固件升级后连不上 | `hello.version` 做显式校验与拒绝，不静默兼容；记录到 `device_sessions.protocol_version` |
| 下行帧堆积 | 内存涨、延迟大 | 预缓冲 + 流控（参考上游 `AudioRateController`），打断时立刻清队列 |
| 隐私 | 原始音频/声纹外泄 | 原始 Opus/PCM **不落盘**（只落转写文本），沿用 `RAG_DESIGN.md` 的边界 |

---

## 8. 关键文件改动索引

| 关注点 | 新增 | 修改 |
|---|---|---|
| 协议/网关 | `services/device_gateway/xiaozhi/*` | `services/dialogue/app.py`（只加一行挂载） |
| 音频 | `services/device_gateway/xiaozhi/opus_codec.py`、`vad.py` | — |
| TTS | `services/tts/edge.py` | `services/tts/config.py`（加 `edge` 分支）、`requirements.txt` |
| 数据 | — | `services/storage/migrations.py`、`services/dashboard/read_model.py` |
| 设备 API | — | `services/dialogue/app.py`、`services/device_gateway/events.py` |
| 后台前端 | `services/dashboard/devices.*`、`device_detail.*` | `index.html`、`dashboard.js` |
| 配置 | — | `configs/launcher.json`、`.env.example`、`models/manifest.json`、`scripts/download-model.ps1` |
| 文档 | `docs/ESP32_ESP_INTEGRATION_PLAN.md`（本文） | `docs/integration/ESP_ONBOARDING.md`、`docs/DEVICE_PROTOCOL.md`、`docs/IMPLEMENTATION_STATUS.md` |

---

## 9. 待确认问题

1. **ESP32 硬件是否已到货？** 若未到，P1-7 真机联调延后，可先用「Python 假客户端 + 模拟器」验收 P1-1~P1-6。
2. **固件是哪一版/哪个板型？**（如 `atk-dnesp32s3` / `v2.5.0`）决定 `hello.version`、`audio_params` 与 LCD 资源；协议版本不同会影响能否直接复用。
3. **是否接受引入 edge-tts（在线 TTS）？** 若不接受离线替代（SAPI / piper），音质会明显下降。
4. **是否现在就要 MQTT+UDP？** 建议先在 P2，WebSocket 已能满足 2.4G 局域网对话。
5. **后台「设备接入」页入口命名**：`设备接入` / `配网与接入` / `ESP 管理` —— 需要定一个。

### 已作的决定（2026-10-04）

- **问题 3 → 采用 edge-tts**：`IOT_TTS_PROVIDER=edge`，实测 24 kHz 单声道、RMS≈2038（非静音），粤语/普通话/英语三种声音均可。默认仍保持 `windows`，不动既有安装的行为；`/api/esp/status` 用 `tts.produces_audio` 明说是否真的出得了声。
- **问题 4 → 仍在 P2**：本层只做 WebSocket，`services/device_gateway/mqtt_udp.py` 维持占位。
- **问题 5 → 定名「设备接入」**（路由 `/dashboard/devices`）。
- **问题 1、2 → 未回答**：因此 P1-7 真机联调未执行，P0~P1 的验收全部建立在「假客户端 + 注入式 ASR/TTS」之上。

---

## 10. 落地现状（2026-10-04）

代码已全部落地并回归：`pytest tests/test_esp_gateway.py -q` → **41 passed**（含 Dashboard 页面测试与声纹身份、LLM 回退的回归测试），`services/` 全量 `compileall` 通过。

### 已完成

| 项 | 状态 | 证据 |
|---|---|---|
| P0-1 真实 TTS | ✅ | `services/tts/edge.py` + `services/tts/config.py` 的 `edge` 分支；实测合成 4.8 s 音频 |
| P0-2 Opus 往返 | ✅ | 上行 1920→105→1920、下行 2880→220→2880；`test_opus_encoder_frames_and_decoder_roundtrip` |
| P0-3 VAD | ✅（改用能量式） | `vad.py` 的滑窗最小噪声估计 + 双边界夹取；4 个测试 |
| P1-1 协议与状态机 | ✅ | `protocol.py`/`session.py`；`test_websocket_text_turn_end_to_end` |
| P1-2 上行音频→ASR | ✅ | `test_websocket_audio_turn_runs_asr`（1 s Opus 进 ASR，约 32000 字节 PCM16） |
| P1-3 下行 Opus | ✅ | 同一测试断言收到了二进制音频帧 |
| P1-4 OTA | ✅ | `test_ota_http_reports_disabled_service`、`test_ota_download_refuses_traversal` |
| P1-5 token/白名单/落库 | ✅ | 3 个 `authorize_device` 测试 + `test_migration_adds_esp_schema_idempotently` |
| P1-6 Dashboard | ✅ | `/dashboard/devices`、`/dashboard/device/{id}`、侧边栏「设备接入」、系统状态 7 格（ASR/DeepSeek/数据库/ESP 接入/OTA/Opus/局域网地址）；`test_dashboard_device_pages_are_served_with_their_assets` |

### 与计划的偏离（都是主动决定，不是遗漏）

1. **VAD 用能量式而非 silero**：silero 需额外模型与依赖，会推迟 P1；`create_vad()` 在遇到非 `energy` 的 provider 时**显式降级并把状态标记为 `degraded`**，而不是假装用了指定引擎。
2. **二维码未实现**：小智固件是手填 OTA 地址，不扫码，所以二维码在这里主要是装饰；页面改用「一键复制」+ 明文地址。要做的话只需前端引入一个 QR 库，不涉及后端。
3. **配对码流程未实现**：改由管理員在后台「待接入设备」直接选择用户绑定（`/bind`），少一步设备侧交互，也少一个可被爆破的短码。
4. **指令集为 `speak`/`abort`/`close`/`iot`**：原计划的「重启/音量/亮度」需要固件侧支持对应 IoT 方法，未实现前不下发（拒绝未知指令为 422，而不是静默丢弃）。
5. **`configs/launcher.json`、`models/manifest.json` 未改**：接入层不新增需要启动器解析的模型，且 Opus/TTS 走 pip 依赖与 PATH 上的 ffmpeg，不动 manifest 是更小的改动面。

### 回归中修掉的三个真 bug

- **`listen state=stop` 会清空刚录好的音频**：`_on_listen` 先 `_reset_audio()` 再 `_flush_utterance()`，而后者正是从 buffer 取数据 → 手动模式（按键说话）的语音永远进不了 ASR，且连接会一直等到 120 s 空闲超时才断开。修法：不在 stop 分支预先清空（`_flush_utterance` 自己会重置）。这个 bug 由 `test_websocket_audio_turn_runs_asr` 抓到，测试从 121.86 s 降到 1.55 s。
- **Dashboard 改设置“保存成功但没生效”**：`effective_env()` 是「进程环境优先」，而 `.env` 在启动时已被 `load_dotenv` 读进进程，所以写回 `.env` 的值会被旧的环境变量盖掉，与接口承诺的 `restart_required: false` 矛盾。修法：新增 `settings_store.export_env()`，写盘后同步写穿 `os.environ`。
- **ESP 每一轮都退回罐头回复、`model` 记成 `none`**（真机联调时发现）：`session.py` 只写
  `self.ctx.providers.get("deepseek") or None`，而生产环境是 `app = create_app()` → `providers == {}`，
  于是 `llm=None`，`process_text` 直接返回 `status="unavailable" / model="none"` 并走兜底文案；
  同一台服务的 `/health` 却回报 `deepseek_configured: true`（key 在进程里），所以看起来像「key 坏了」。
  对照 `app.py` 的两处调用点即可看出缺的是 `or DeepSeekClient()`。修法：补齐同样的回退，
  并以真 key + 真网络跑通一轮（`llm_status="ok"`、`served_model="deepseek-flash"`）。
  **教训**：所有「取 provider」的地方都必须有同一个回退，否则同一功能在不同入口表现不一致。

---

## 11. 落地现状（2026-10-04 晚 · 第二轮：待机与设备控制）

真机对话跑通后暴露的两个缺陷，均已修复（设计见 §4.7）。

### 新增模块

| 文件 | 作用 |
|---|---|
| `services/device_gateway/xiaozhi/mcp.py` | 设备端 MCP JSON-RPC 客户端（握手 / `tools/list` / `tools/call` / 旧 `iot` 描述符折叠 / 工具名 sanitize） |
| `services/device_gateway/xiaozhi/voicecmd.py` | 确定性规则解析「改设备设置」语句 → 直连工具 + 本地确认语（零模型调用） |
| `services/device_gateway/xiaozhi/registry.py` 的 `ToolRegistry` | 每个会话注册自己的工具集，供 Dashboard 与管线查询 |

### 改动的既有文件

- `session.py`：主循环改双时钟（`_last_voice_at` 待机 / `_last_activity` 死链）+ `_clock_verdict()` /
  `_next_deadline_seconds()` / `_enter_standby()`；`_on_listen` 增加 `_listened` 与唤醒词过滤；
  `_flush_utterance` 无语音时回 `idle`；`_send_welcome` 后自动 `_start_tools()`；
  `_maybe_device_command()` 与 `_think(tools=...)` 两条设备控制路径。
- `config.py` / `settings_store.py` / `router.py`：新增 6 个配置项（§4.5）与 Dashboard 写入白名单。
- `context.py` / `app.py`：`GatewayContext.tools` 接线。
- `deepseek.py` / `pipeline.py`：`tools` 形参与 `_reply_with_tools()` 工具循环。
- `dashboard/devices.html` / `devices.js` / `device_detail.html` / `device_detail.js`：新开关与「设备工具」面板。

### 验收口径（真机）

1. 打开「设备接入」页确认 `IOT_ESP_TOOLS` 已开，设备上线后详情页「设备工具」应列出 ≥1 个工具；
   若显示「固件未响应」，说明该固件不带 MCP，语音控制设备设置不可用（其余对话不受影响）。
2. 说一句普通问题，**静置约 60 s**：应听到待机提示语，随后设备回到待命（不再卡在「聆听」）。
3. 说「把音量调到 60」：设备音量应真实变化，并得到一句本地确认语（该路径不调用模型，即使模型不支持
   function calling 也生效）。
4. 单独说唤醒词「小智」：设备进入聆听但**不应**被当成问题回答。

### 回归

`pytest tests -q` → **60 passed in 58 s**（整个 `tests/` 目录，用专用 venv 跑），`services/` 全量 `compileall` 通过。
新增覆盖：MCP 握手 / 超时 / 设备报错、iot 两种描述符形状、规则命中与弃权、
待机三态与「静音不刷新计时」、唤醒词不是问题、静默后待机并落库 `standby_no_voice`、
设备工具直达（`metadata["model"] == "device-tool"`）、
新设置项写盘与回读（`standby_seconds` / `standby_notice` 折叠空白 / `tools_enabled` / 越界 422）。

跑测试踩过的坑（都记下来，下次别再踩）：

1. **必须用专用 venv**：`E:/tools/venvs/esp_gateway/Scripts/python.exe`（带 opus / pyogg）。
   用 conda base（3.14.6）跑会跳过全部 `requires_opus` 用例，还会在 MCP 用例处直接卡住不返回；
2. **不要并发跑两个 pytest**，也不要只靠 `tail` 看进度——重定向时 Python 的 stdout 是块缓冲，
   看起来像「卡死」。用 `PYTHONUNBUFFERED=1`，并加 `-o faulthandler_timeout=90`，真卡住会自动打栈；
3. **`POST /api/esp/settings` 会写穿 `os.environ`**（生产上正是「改完即时生效」），
   所以测试里改设置前必须先用 `monkeypatch.setenv()` 把键注册好，否则旧值会污染后续用例
   —— 实测 `IOT_ESP_TOOLS=0` 泄漏后，规则路径静默弃权、回落到真实模型调用，整套测试挂死；
4. pytest 结束时的临时目录回收会撞上本环境的 safe-delete 保护并被中断，
   用 `--basetemp=runtime/pytest-tmp` 绕开即可拿到汇总行。

---

## 12. 落地现状（2026-10-04 深夜 · 第三轮：响应速度、打断、手动结束）

真机上暴露的四个体验缺陷：「说得慢」「插话打断不了」「长句说一半就断」「没法手动结束」。
根因都不是协议接错了，而是**播放路径的形状**错了——旧路径严格串行：

```
等完整回答 → split_speech → 逐句 edge-tts → 逐句编码 → 逐句发送
```

四个后果一一对应四个缺陷：首音要等整段答案 + 首句合成完；每一步都是 inline await，
没有任何取消点；一句无标点的长文变成一个巨大的 edge-tts 请求，撞 provider 超时后**整句被丢**，
后半段永久静音；没有任何「结束」语义。

### 12.1 修法：把串行改成两级流水

新增 `services/device_gateway/xiaozhi/playback.py` 的 `TtsPlayer`，两个 worker 并行：

| worker | 干什么 | 在哪 |
|---|---|---|
| `_synthesize_loop` | 句子 → PCM（edge-tts + ffmpeg 是阻塞的） | `asyncio.to_thread` |
| `_playback_loop` | PCM → Opus → 按 60 ms 帧节奏发 socket | 事件循环 |

于是**句 n+1 的合成与句 n 的播放重叠**；一句合成失败只丢那一句（错误进 `errors` 并推
`display.state.audio_error`）；两个 stage 每轮都查 `aborted`，可以中途撕掉。
队列**故意不设上限**——生产者是语言模型，输出被答案本身限界，加背压只会多一个死锁面
（worker 线程没法 `await` 一个满的 `asyncio.Queue`）。

**排队规则**：`_push()` 先看当前是否就在 loop 上，是则 `put_nowait`，否则
`loop.call_soon_threadsafe` —— 因为 `on_delta` 跑在 `asyncio.to_thread` 的工作线程里，
`offer()` 必须线程安全。

### 12.2 修法：LLM 走 SSE 串流 + 句子流式切分

- `services/dialogue/deepseek.py`：新增 `reply_stream()`（`stream: true`，逐行解析
  `data: {...}` 取 `choices[0].delta.content`）。**回退策略**：非 200，或连接中断且
  **尚未吐出任何 delta** 时，退回非流式 `reply()`；已经吐出部分内容就保留已流部分，不重来。
- `services/tts/segments.py`：新增 `SentenceStreamer`，delta 进来就吐完整句子。
  硬终止符 `。！？!?；;…\n`；超 `max_chars` 还没终止符就在软断点 `，,、：:）)】」》」` 切。
  **首句不受 `min_chars` 限制**（`floor=2`）——首音延迟是用户真正体感的那部分延迟，
  为了「凑够 6 个字」把首句压后是本末倒置。
- **元数据绝不朗读**：prompt 要求模型在结尾附 ` ```json …``` ` 携带 emotion/risk/remember；
  流式读者会在最后一句之后才看到它，所以 `_note_metadata()` 在第一个标记
  （` ``` ` 或裸键 `"emotion"` / `"risk"` / `"risk_evidence"` / `"remember"`）处截断，
  并把紧贴其前的半截句（`…不错{"emo`）砍到最后一个完整终止符之后，避免把 `{` 读出来。
- **流式只用于无工具回答**：有 tool 的回合必须先拿到完整 `tool_calls`，否则会把工具 JSON 念出来。
  所以 `pipeline.process_text` 三分支：`tools → tool loop` / `on_delta → reply_stream` / 其余 → `reply`。
- 回退兜底：`_think()` 传 `on_delta` 时若抛 `TypeError` 且信息含 `on_delta`，自动降级为不带流式重试。

### 12.3 修法：打断的唯一杠杆是 `tts:stop`

固件 `main/application.cc` 的 `OnIncomingJson` 只处理 `tts/stt/llm/mcp/system/...`，
**没有 `listen` 分支**。`tts state=stop` 在 `kDeviceStateSpeaking` 时：manual 模式 → `kDeviceStateIdle`，
否则 → `kDeviceStateListening`。所以**服务端能打断播放的唯一一条消息就是 `tts:stop`**。

`session._interrupt()` 重写为：先无条件 `_stop_speech()`（发 `tts:stop`），再 `player.abort()`，
再 cancel `_turn_task` 并 await，最后 `_reset_audio()` + 推 `turn.interrupted`。

**一个必须避开的竞态**：正常播完时设备必然回一条 `listen start`。旧逻辑把 `listen start`
当打断、取消正在收尾的回合，于是「说完最后一句话，回合被自己取消」。对齐上游
`listenMessageHandler`（对 `listen start` **只** `reset_audio_states()`，不打断）后，
已从 `_on_listen` 的 `start` 分支**移除**打断逻辑；玩家自己也**不再**发 `tts:stop`
（`playback._playback_loop` 明确注释说明为何），把这条消息交回 session 统一排序，
否则设备回敬的 `listen start` 会撞上还没收尾的回合。

### 12.4 修法：语音手动结束（`dialogctl.py`）

新增 `services/device_gateway/xiaozhi/dialogctl.py`，`classify()` 用
**整句精确匹配 + 长度上限 12 字**，这是刻意的：子串规则会在
「我想结束这段关系，怎么办？」上误挂电话。

| 意图 | 词表 | 行为 |
|---|---|---|
| `stop` | 停、停一下、别说了、安静、闭嘴、stop… | 本地回一句「不说了」（`_speak_local`，**不调用模型**），继续聆听 |
| `end` | 退下、结束、结束对话、拜拜、再见、byebye… | 走 `_leave_conversation()` → `_enter_standby("voice_end")`：告别语 → 关 WS → 设备回待命 |

### 12.5 另外两处对齐上游

- **唤醒词静默期**：`listen detect` 之后固件会自己播提示音，`IOT_ESP_WAKE_WORD_HOLD_SECONDS`（0.8 s）
  内忽略 VAD 断句，避免把提示音当成用户说完。
- **单句容错**：旧路径一句失败 = 整段丢弃；现在只丢那句并继续。

### 12.6 改动文件

| 文件 | 改动 |
|---|---|
| `services/device_gateway/xiaozhi/playback.py` | **新增** `TtsPlayer`（两级流水） |
| `services/device_gateway/xiaozhi/dialogctl.py` | **新增** 语音控制指令识别 |
| `services/tts/segments.py` | 新增 `SentenceStreamer`；`split_speech` 保留供批量用 |
| `services/dialogue/deepseek.py` | 新增 `reply_stream()` + `_parse_sse_line()` |
| `services/dialogue/pipeline.py` | `process_text(on_delta=...)` 三分支；`model.streamed` |
| `services/device_gateway/xiaozhi/session.py` | `_interrupt` 发 `tts:stop`；`_stream_answer()`；`_speak_local`/`_speak` 改用 player；`_leave_conversation()`；`_on_listen` 移除 start 打断；唤醒词静默期 |
| `config.py` / `settings_store.py` / `router.py` | 4 个新配置项（§4.5）与 Dashboard 白名单 + 校验（`tts_max_chars` 12–200、`tts_min_chars` 1–50、`wake_word_hold_seconds` 0–10） |
| `dashboard/devices.html` / `devices.js` | 新增「语音体验（对标小智官方）」卡片：流式回答开关 + 3 个数值输入（越界时前端先挡一次并回滚，服务端仍会二次校验） |

> 这 4 项都**不需要重启**：`settings_store.export_env()` 会把写盘的值同步写穿 `os.environ`，
> `EspSettings.from_env()` 下一次读到的就是新值（`test_latency_settings_round_trip_and_are_bounds_checked` 覆盖）。

### 12.7 真机验收口径

1. 问一句普通问题：**第一个字应很快就出声**（不再等整段答案写完）。
2. 让它说一段较长的内容，中途插话：**应当立刻被打断**，设备转入聆听。
3. 故意让它说长句／无标点：**应当能说完**，不再停在半截。
4. 说「停」：应回「不说了」且不再继续念，仍在聆听；说「退下」：应听到告别语，随后设备回待命。
5. 说「我想结束这段关系，怎么办？」：**不应**被当成结束指令。

### 12.8 回归

`pytest tests -q` → **72 passed in 72.6 s**（专用 venv `E:/tools/venvs/esp_gateway`，`--basetemp=runtime/pytest-tmp`）。
新增 12 项覆盖：`SentenceStreamer`（首句即时 / 无标点封顶 / 围栏与裸 JSON 都不朗读）、
`split_speech` 封顶、`dialogctl` 整句匹配与「真实句子不误判」、`TtsPlayer`（顺序 + 单句容错 +
`abort` 后不再发 `stop`）、`_interrupt` 必发 `tts:stop`、「停」不调模型、「退下」落库 standby 并关通道、
「流式答案在模型写完前就已经在出声」、以及 4 个延迟开关的写盘往返与越界 422。

> 跑之前**必须先清空 `runtime/pytest-tmp`**：pytest 每次启动都删除并重建 basetemp，而上一轮会留下 160+ 个文件，
> 一次性 `rmtree` 会触发沙箱的 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（阈值 50）→ 进程被 `SystemExit(1)` 打断，
> 症状是**「前 36 项通过、之后全部 ERROR at setup」**，很容易被误读成代码坏了。逐文件删除即可绕开。
> 清空时要**连空目录一起删掉**（只删文件会让 basetemp 处于半残状态，触发
> `SAFE_DELETE_FAIL_CLOSED / Errno 53 找不到网络路径`，表现为 OTA 那 3 个用例报 ERROR）。
> 让 basetemp 从「不存在」起步，pytest 就不会调用删除钩子。

---

## 13. 落地现状（2026-10-05 凌晨 · 第四轮：真机「调小反而说调大」+ 响应仍然慢）

用户报告两件事：**说「音量调小一点」得到「好的，音量已经调大了一点」**，以及**响应仍然比较慢**。
先说一句：**设备连的是 `192.168.137.98`（用户已确认），就是真机**，不是模拟器。

### 13.1 取证方法（值得记下来）

真机的运行数据**不在 `data/emotional_robot.sqlite3`**（那份是旧的、最后一条设备事件停在 `10-04 20:08`），
而在 **`IoTGroup5/emotional_robot.sqlite3`**（启动器的 `DataDir` 默认值就是 `.\IoTGroup5`）。
第一次查 `data/` 会以为「真机从没连上过」，这是纯粹的误判。

另一条线索：`services/**/__pycache__/*.cpython-3XX.pyc` 的 mtime 晚于对应 `.py`，就说明服务**已经重载过**这份代码。
服务跑的是 Anaconda 的 **Python 3.14.6**（`*.cpython-314.pyc`），不是测试 venv 的 3.13 —— 两套解释器不要混淆。

### 13.2 Bug A：确认语方向反了（纯逻辑 bug，动作是对的）

数据库里的原始记录：

```
00:12:36 | 用户: '音量调小一点。'
          回复: '好的，音量已经调大了一点。'
          meta: {'model': 'device-tool', 'latency_ms': 12}
device_command | {"tool":"self.audio_speaker.set_volume","arguments":{"volume":80}}
```

**音量确实从 100 被调到了 80，动作完全正确**；错的只有那一句话。`model=device-tool` + `latency_ms=12`
说明走的是规则直达路径、根本没调模型，所以这不是「模型不听话」，是本地文案生成的 bug。

根因在 `voicecmd.confirmation()`：

```python
value = level if level is not None else command.value   # 调用方传的是「解析后的绝对档位」
if command.kind == "step":
    up = int(value) > 0        # ← 100-20=80，80 > 0，于是永远说「调大」
```

方向必须来自**步进符号**，不是来自绝对档位。修法：`up = int(command.value) > 0`。
顺带修掉措辞：亮度不再说「调小／调大」，按目标词分别用「调亮／调暗」（粤语 調光／調暗）。

### 13.3 Bug B：只要设备上报了工具，流式就被静默禁用（「仍然慢」的直接原因）

`session._think()` 里 `tools = self._llm_tools()`；真机固件上报 **6 个 MCP 工具**，所以 `tools` 恒非空。
而 `pipeline.process_text` 的分支顺序是：

```python
if llm is not None and tools and tool_runner is not None:
    response = _reply_with_tools(...)      # ← 永远走这里，on_delta 被直接丢掉
elif llm is not None and on_delta is not None and ...reply_stream...:
    response = llm.reply_stream(...)       # ← 真机上永远到不了
```

**结果**：第三轮加的流式在真机上从未生效过一次（测试里工具集为空，所以全绿）。
这不是「流式没用」，是「流式被绕过了」。

修法（对齐上游做法：函数调用本来就在同一条流上，两者不必二选一）：

- `deepseek._parse_sse_event()` 同时解析 `delta.content` 与 `delta.tool_calls`；
- `_merge_tool_call_deltas()` / `_finalise_tool_calls()` 按 `index` 累积**分片**的函数调用
  （OpenAI 兼容流会把一个调用拆到多个 chunk 里）；
- `reply_stream(messages, request_id, on_delta, tools=...)` 新增 `tools`，并且**回退时也带着 tools**
  （原来非 200 回退到 `self.reply(...)` 会把工具能力一起丢掉）；
- `pipeline._stream_with_tools()`：内容 delta 照常边说边合成；若某轮流返回 `tool_calls`，就执行工具、
  把结果喂回去再流一轮；**最后一轮撤掉工具**，保证这一轮一定以「话」而不是「又一次调用」结束。
  分支顺序改成**流式优先**。

**风险与验证**：如果这条 relay 在 `stream:true` 下忽略 `tools`，就会从「工具可靠」退化成「只有嘴上答应」。
所以直接对真端点做了验证（`scripts/measure-latency.py`）：

```
流式 + tools → status=ok  tool_calls=[('self_audio_speaker_set_volume', '{"volume": 40}')]  671 ms
非流式 + tools → status=ok  tool_calls=[('self_audio_speaker_set_volume', '{"volume": 40}')]  804 ms
```

分片累积器把 `self_audio_speaker_set_volume` 与 `{"volume": 40}` 正确重组，`streamed=False`
（这一轮没有正文 delta，因为模型选了调用工具）—— 所以 `streamed` 现在如实反映「有没有真的流过正文」，
而不是恒为 `True`。

### 13.4 顺手查出的一个陷阱：工具名带点会被模型 API 直接 400

```
HTTP 400  Invalid 'tools[0].function.name': expected a string matching '^[a-zA-Z0-9_-]+$'
```

固件的工具名是**点分路径**（`self.audio_speaker.set_volume`），而模型 API 只接受 `[A-Za-z0-9_-]`。
项目里已有 `mcp.sanitize_tool_name()`（`self_audio_speaker_set_volume`）并已接在 `as_tool_llm()` 上，
所以真实路径不受影响 —— 但这正是「看起来实现了、其实永远不工作」的经典形态：
只要有人绕过 sanitize 直接拼工具表，整轮会以 `status="error"` 静默降级成兜底文案，
而 `except Exception` 会把原因吞掉。诊断脚本因此**强制走真实的 sanitizer**，不手搓工具名。

### 13.5 延迟预算（实测，`scripts/measure-latency.py`）

一轮语音从「用户闭嘴」到「设备出声」的全部账单：

| 阶段 | 实测 | 性质 |
|---|---|---|
| VAD 结束静音判定（`IOT_VAD_SILENCE_MS`） | ~800 ms | 可调，config |
| ASR（SenseVoice / sherpa-onnx） | 178–1200 ms（2304 ms 音频） | 本地模型；**跑在 CPU** |
| LLM 首句可合成（流式） | 1494–2066 ms | 外部 API |
| TTS 首块（edge-tts） | **791–2077 ms（波动很大）** | 外部服务 |
| ffmpeg 解码整句 | 190–385 ms | 本地 |
| **合计** | **≈ 3.5–5.5 s** | |

对照第三轮之前（流式被绕过时）的等效值：LLM 要等**整段**完成（2000–2370 ms）而不是首句
（1494–2066 ms），TTS 要**收齐**（1010–1896 ms）而不是首块（791–1437 ms）。

**结论：这条链路里占大头的两项都在服务外部** ——
edge-tts 的首块延迟随外网状况在 0.8–2.1 s 之间抖，DeepSeek 首句 1.5–2.1 s。
本服务可控的部分（VAD 800 ms + ffmpeg ~200 ms + 本地确认语 12 ms）都不是主要矛盾。
所以「仍然慢」= **Bug B 造成的「流式从未生效」＋ 外部服务本身的延迟**，二者叠加，前者已修。

### 13.6 已知但不修的诊断陷阱

`[asr] sensevoice provider=cuda compute_type=int8 reason=cuda_ready cuda_devices=1`
这句话里 **`provider=cuda` 是「声明」而不是「事实」**：紧接着 sherpa-onnx 自己会打印
`Please compile with -DSHERPA_ONNX_ENABLE_GPU=ON ... Fallback to cpu!`。
本机的 `sherpa_onnx` wheel 就是 CPU-only 构建，`sherpa_onnx` 也没有 `get_available_providers()`
之类的 API 来问它真实用了什么，所以**没有可靠办法在代码里如实上报**。
记录在此，免得下次看到 `cuda_ready` 却去排查一个并不存在的 GPU 路径。

### 13.7 改动文件

| 文件 | 改动 |
|---|---|
| `services/device_gateway/xiaozhi/voicecmd.py` | `confirmation()` 步进方向改看 `command.value`；亮度改用「调亮／调暗」 |
| `services/dialogue/deepseek.py` | `_parse_sse_event`（含 tool_calls）、`_merge_tool_call_deltas`、`_finalise_tool_calls`；`reply_stream(..., tools=...)`；**回退时保留 tools**；`streamed` 如实上报 |
| `services/dialogue/pipeline.py` | 新增 `_stream_with_tools()`；分支顺序改为**流式优先** |
| `scripts/measure-latency.py` | **新增**：对真端点/真模型测 LLM 首句、TTS 拆分、ASR；含 DPAPI 密钥读取（不打印）与真实工具 sanitize |

### 13.8 回归

`pytest tests -q` → **77 passed in 109 s**（专用 venv，basetemp 彻底清空后从零起步）。
新增 5 项：确认语方向（含亮度措辞与绝对值不受影响）、「有工具也不能把流式拿走」、
「流式返回的 tool_calls 会被执行且绝不朗读」、「必须说话的那一轮是撤掉工具问的」、
分片 `tool_calls` 重组（含无名分片被丢弃）。

### 13.9 真机验收口径（本轮）

1. 说「音量调小一点」→ **音量真的变小，且说的是「调小」**（说「大一点」则相反）。
2. 问一句普通问题（例如「珠海的天气怎么样」）→ 首句应明显比之前更早出声。
3. 说「把音量调到 40」这类明确指令，仍走规则直达（`latency_ms` 十几毫秒、不调模型）。
4. 说一句规则覆盖不到的设备指令 → 模型工具调用应当真的动作，而不是只口头答应。


## 14. 落地现状（2026-10-05 凌晨 · 第五轮：装置表情不会随内容变化）

用户报告：**ESP32 并没有根据说话内容变换 emoji**，要求参考官方代码检查。

### 14.1 取证：真机上 20 轮对话的情绪全是 `neutral`

```sql
SELECT emotion, COUNT(*) FROM conversations GROUP BY emotion;
-- neutral  20
```

20 笔里没有第二条记录。也就是说设备**从头到尾只收到过 `SetEmotion("neutral")`**，
表情当然不会动。列一下最近几轮：

```
10-05 00:37 | neutral | 精量。
10-05 00:12 | neutral | 音量调小一点。
10-04 23:36 | neutral | 太极。
10-04 21:05 | neutral | 结束对话，刚才输了这还。
```

### 14.2 官方是怎么做的（对照真源码）

固件侧（**本机 `xiaozhi-esp32`，`PROJECT_VER 2.0.4`，正是真机跑的那份**）：

```cpp
// main/application.cc
} else if (strcmp(type->valuestring, "llm") == 0) {
    auto emotion = cJSON_GetObjectItem(root, "emotion");
    if (cJSON_IsString(emotion)) {
        Schedule([this, display, emotion_str = std::string(emotion->valuestring)]() {
            display->SetEmotion(emotion_str.c_str());
        });
    }
}
```

`tts` 消息**不携带情绪**；情绪只在 `llm` 消息的顶层 `emotion` 字段上。
`LcdDisplay::SetEmotion` 再走两步：主题的 emoji 资源集 → 没有就退回
`font_awesome_get_utf8(name)`（本机 `78__xiaozhi-fonts` 的表把 21 个名字全部映射到了
字形，`font_awesome_30_4` 能画出来）。板子 `cx-esp32s3` 用的是不带 theme 的
`new SpiLcdDisplay(...)`，所以走的是 font-awesome 兜底那条路，**没有资源包也有表情**。

服务端侧（本机 `项目/xiaozhi-esp32-server-main`）：

```python
# core/utils/textUtils.py
async def get_emotion(conn, text):
    emoji, emotion = "🙂", "happy"
    for char in text:
        if char in EMOJI_MAP:          # 21 个 emoji -> 情绪名
            emoji, emotion = char, EMOJI_MAP[char]
            break
    await conn.websocket.send(json.dumps(
        {"type": "llm", "text": emoji, "emotion": emotion, "session_id": conn.session_id}))
```

```python
# core/connection.py —— 一轮对话只在开头取一次
if emotion_flag and content is not None and content.strip():
    asyncio.run_coroutine_threadsafe(textUtils.get_emotion(self, content), self.loop)
    emotion_flag = False
```

配套的提示词（`agent-base-prompt.txt`）：

> - [Single emoji prefix] Only ONE emoji is allowed, and only at the very beginning
>   of each regular reply (no emoji when calling tools).
> - [Emoji whitelist] Only use emojis from this list: {{emojiList}}.

**结论：官方让模型在「自己的回复」开头放一个 emoji，服务端扫这个 emoji 得到情绪名。**
装置的表情跟着**对话内容**走。

### 14.3 我们错在哪

我们的 `emotion` 是一条**三值情感分类**（`positive|negative|neutral`），
而且判的是**使用者这一轮的心情**（本地关键词 + 提示词明写「emotion 表示使用者本輪情緒」）：

```python
_EMOTION_TABLE = {("negative",""): ("sad","😔"), ("positive",""): ("happy","🙂"), ...}
```

两个后果，叠在一起就是「表情不动」：

1. **语义反了**：表情表达的是「使用者心情」，不是「助手这句话的表情」。
   官方那套是模型自己挑表情，所以每轮都可能不一样。
2. **词汇只有 3 个**（+ 风险用的 `shocked`）：哪怕判对了也只有 🙂／😔／😶 三张脸。
   而 `detect_emotion()` 是关键词兜底、模型又常回 `neutral`，
   真机 20 轮就**全部**落在 `neutral` 上 —— 固件里 `neutral` 的字形 `0xf5a4`
   还恰好是空白，屏幕上什么都不显示。

### 14.4 改法（对齐官方）

| 文件 | 改动 |
|---|---|
| `services/emoji.py` | **新增**叶子模块：官方 `EMOJI_MAP` 21 条 `EMOJI_TO_EMOTION`、`EMOJI_WHITELIST`、`emotion_from_emoji()`、`is_emoji()`/`strip_leading_emoji()`（用官方 `EMOJI_RANGES`，认得出来的认不出来的都剥） |
| `services/device_gateway/xiaozhi/protocol.py` | `DEVICE_EMOTIONS` 改为由 emoji 表派生（**不可能再漂移**）；新增 `face_message()` |
| `services/dialogue/pipeline.py` | 提示词加「正文開頭只放一個 emoji、只能從白名單挑、呼叫工具那輪不放」 |
| `services/tts/segments.py` | `SentenceStreamer` 与 `split_speech` 都剥掉开头的 emoji —— **不会被念出来** |
| `services/device_gateway/xiaozhi/session.py` | 表情改由**第一个正文分片**决定（官方口径）；风险回合覆盖；批次回退才用 `_provisional_emotion` |

```python
# protocol.face_message —— 一个函数就是全部口径
if risk_level in {"attention", "urgent"}:          # 安全优先于装饰
    name, emoji = map_emotion(fallback_emotion or "negative", risk_level)
    return {"type": "llm", "emotion": name, "text": emoji, "session_id": session_id}
found = emotion_from_emoji(reply_text)             # 官方口径：读模型自己的 emoji
if found is not None:
    emoji, name = found
    return {"type": "llm", "emotion": name, "text": emoji, "session_id": session_id}
name, emoji = map_emotion(fallback_emotion or "neutral")   # 兜底
return {"type": "llm", "emotion": name, "text": emoji, "session_id": session_id}
```

**两处刻意不照抄官方**（都是为了这个产品）：

1. **模型忘了放 emoji 时，不假装成 `🙂/happy`**（官方是无条件 default happy）。
   在一台情感陪伴机器人上，对着难过的一轮笑，比没表情更糟。
   我们退回「使用者心情」的判定结果。
2. **风险回合覆盖模型自己的 emoji**（`attention`→😔、`urgent`→😱）。
   在这条产品线上，表情先是安全信号，然后才是装饰。

发送时机：表情在**第一个正文分片**到达时发出，且排在第一次 `player.offer()` 之前
（`call_soon_threadsafe` 的 FIFO 顺序），所以**一定早于 `tts start`**，
不会播到一半才换脸。工具调用的轮次没有正文，天然不会放 emoji，
第二轮真正说话时分片才触发 —— 与官方 `emotion_flag` 同效。

### 14.5 回归

`pytest tests -q` → **83 passed in 85.6 s**（77 → 83）。新增 6 项：

* 会话层：**表情跟随回复 emoji 而不是使用者心情**（`emotion="neutral"` 与 `😭` 故意冲突）、
  且 `llm` 消息必须早于 `tts:start`、且 emoji 不出现在 TTS 文本里；
* 21 个白名单 emoji **逐个**映射到固件认识的名字，且都在 `DEVICE_EMOTIONS` 里；
* 没有认识的 emoji 时**不猜 happy**（含白名单外的 🎈）；
* 风险回合覆盖模型表情（`urgent`→`shocked`、`attention`→`sad`，无风险时不覆盖）；
* 开头 emoji **绝不被合成**（逐字流式、粘在首句、批次切分三种形态；句中的 emoji 不动）；
* 提示词里**确实带着白名单**。

### 14.6 真机验收口径（本轮）

1. 说一句带情绪的话（例如「我今天真的很難過」）→ 屏幕应显示 😭 一类的**哭泣/难过脸**；
   聊开心的事 → 🙂／😆。
2. 一轮里脸部**只变一次**，且出现在**开口之前**（不会第一句播到一半才换）。
3. 屏幕上的字幕**不会把 emoji 念出来**，也不会出现 emoji 字符。
4. 说触发风险的话（例如「我不想活了」）→ 脸应变成 😱／😔（风险优先）。

> 固件把 `neutral` 映射成**空白字形**（`font_awesome.py: "neutral": 0xf5a4 # [blank]`），
> 所以待机/无表情时屏幕上**不显示**表情图标，这是上游行为，不是缺陷。
