# 项目完善步骤文档

> 本文档面向后续接手/完善本项目的开发者，梳理「哪些模块还是占位实现、需要改成什么、具体怎么改、怎么验收」。
> 当前定位：simulator-first 的 PC 端已验证跑通（见 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)），
> 本文档覆盖从「PC 占位实现」到「真模型 / 真机」的完整路径。

---

## 0. 现状速览

| 模块 | 当前实现 | 是否真模型 | 优先级 |
|---|---|---|---|
| 对话 LLM | DeepSeek `deepseek-chat`（[deepseek.py](../services/dialogue/deepseek.py)） | ✅ 真（需 key） | — |
| ASR 语音转文字 | 默认 **SenseVoice-Small**（[sensevoice.py](../services/audio/sensevoice.py)，ONNX int8 228 MB，粤/普/英）；可选 faster-whisper `whisper-large-v3-turbo`（[asr.py](../services/audio/asr.py)） | ✅ 真（需下载模型） | — |
| RAG / 长期记忆 | 表 + 接口 + 检索函数都有，但**没接进对话链路** | ⚠️ 半成品 | **P0** |
| TTS 语音合成 | 输出**静音**（[windows.py](../services/tts/windows.py)） | ❌ 占位 | **P0** |
| 声纹识别 | `[均值, RMS, 过零率]` 统计量 + 余弦（[provider.py](../services/voiceprint/provider.py)） | ❌ 占位 | P1 |
| 情绪 / 风险 | 关键词规则（[pipeline.py](../services/dialogue/pipeline.py) / [risk.py](../services/analysis/risk.py)） | ❌ 规则 | P1 |
| 向量检索 embedding | 未实现（`embedding_json` 字段空占位） | ❌ 未做 | P1 |
| ESP32 真机接入 | mock / loopback（[mock_gateway.py](../services/device_gateway/mock_gateway.py)） | ❌ Phase 2 | P2 |

**关键架构事实**：主程序用 **provider 注入**模式——`create_app(providers={...})`
（[app.py:131](../services/dialogue/app.py#L131)）里通过 `providers.get("asr" / "voiceprint" / "tts" / "deepseek" / "rag")`
注入实现，取不到就用默认占位。**所以大多数模块只需：① 写一个新 provider；② 启动时注入，无需改 app.py 内部逻辑。**
这是最小侵入的完善路径。

---

## 1. 模块一：接通长期记忆检索（P0，最高优先）

### 现状
- `memory_chunks` 表已建好（[migrations.py:66-71](../services/storage/migrations.py#L66-L71)），字段齐全（含 `approved`/`active`/`embedding_json`）。
- 管理员 CRUD 接口已有：`GET/POST /api/users/{user_id}/memories`、`DELETE .../memories/{memory_id}`（[app.py:394-418](../services/dialogue/app.py#L394-L418)）。
- `process_text` 已支持 `memories` 参数并会调用 `retrieve()`（[pipeline.py:63-64](../services/dialogue/pipeline.py#L63-L64)）。
- **但调用点没传 `memories`**：`_chat`（[app.py:234](../services/dialogue/app.py#L234)）和 WebSocket（[app.py:573](../services/dialogue/app.py#L573)）都只传了 `rag_provider=providers.get("rag")`，而 `providers` 默认是空 dict，`rag` 取到 `None` → `_provider_chunks` 直接返回 `[]`。

### 目标
对话时能从数据库加载该用户的 `active=1` 记忆，经权限过滤后作为「不可信参考」注入上下文。

### 怎么改（3 步）

**Step 1 — 新增读取函数** `services/memory/repository.py`：

```python
from services.memory.schemas import MemoryChunk

def load_memories(conn, user_id: str) -> list[MemoryChunk]:
    rows = conn.execute(
        "SELECT chunk_id, owner_user_id, text, source_id, source_kind, approved, active "
        "FROM memory_chunks WHERE owner_user_id=? AND active=1 ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [
        MemoryChunk(
            chunk_id=r["chunk_id"], owner_user_id=r["owner_user_id"], text=r["text"],
            source_id=r["source_id"], source_kind=r["source_kind"],
            approved=bool(r["approved"]), active=bool(r["active"]),
        )
        for r in rows
    ]
```

**Step 2 — 在两个调用点传入 `memories`**。以 `_chat` 为例（[app.py:229-243](../services/dialogue/app.py#L229-L243)）：

```python
def _chat(req, request=None):
    memories = []
    if request is not None:
        with db() as conn:
            admin_auth(request, conn, write=True)
            memories = load_memories(conn, req.user_id)
    result = process_text(
        req.text, user_id=req.user_id, language=user_language(req.user_id),
        identity=identity, session_id=..., llm=llm,
        rag_provider=providers.get("rag"),
        memories=memories,              # ← 关键：传进去
        request_id=str(uuid.uuid4()),
    )
```

WebSocket 分支（[app.py:573](../services/dialogue/app.py#L573)）同理，在已有 `with db() as conn` 里加载并传入。

**Step 3 — 验证权限语义**。`retrieve()` 内部先过 `visible_chunk()`：
- `owner_user_id is None` → 共享知识，只要 `approved & active` 即可读；
- 有 owner → 个人记忆，要求 `identity.decision == "accepted"` 且 user_id 匹配。

模拟器里 `_chat` 的 `identity` 是硬编码 `accepted`（[app.py:232](../services/dialogue/app.py#L232)），所以会放行个人记忆；接真机后这里要换成真实声纹判定结果。

### 验收
1. `POST /api/users/{id}/memories` 写入一条 `approved=true` 的记忆。
2. `POST /api/chat` 发起对话，回复/上下文里应出现该记忆（带 `[不可信參考 ...]` 标注）。
3. 写入 `approved=false` 或 `active=0` 的记忆，对话中**不应**出现。

---

## 2. 模块二：真实 TTS（P0）

### 现状
[windows.py:16-17](../services/tts/windows.py#L16-L17) 的 `synthesize` 无条件 `return DeterministicTts().synthesize(...)`，输出**全零静音**，只是让流程能跑。

### 目标
`TtsProvider.synthesize(text) -> SynthesizedAudio(pcm16, sample_rate, channels, duration_ms, provider)` 返回真实语音 PCM16。**契约不变**，只替换实现。

### 怎么改（二选一）

**方案 A（推荐，见效快）：edge-tts**（微软在线 TTS，中文音质好，免费，需联网）

```python
# services/tts/edge.py
import asyncio
from .provider import TtsProvider, SynthesizedAudio

class EdgeTtsProvider(TtsProvider):
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        # edge-tts 输出 MP3，需解码为 16k 单声道 PCM16（用 ffmpeg 或 pydub）
        # 注意：FastAPI 有事件循环，不能在协程内直接 asyncio.run，
        # 应放到线程里（asyncio.to_thread）或用 edge_tts 的异步 API。
        ...
```

**方案 B：真正调通 Windows SAPI**（离线，但音质一般）：用 `pywin32` 的 `SAPI.SpVoice` 合成到 WAV 文件，读回 PCM16。

### 接入方式（不用改 app.py）
在启动脚本里注入：
```python
app = create_app(providers={"tts": EdgeTtsProvider()})
```
或改 [config.py](../services/tts/config.py) 的 `create_tts_provider()`，把 `IOT_TTS_PROVIDER` 增加一个 `edge` 分支。

### 验收
Dashboard 对话后能听到真实语音；`media_store` 里存的不是全零静音。

---

## 3. 模块三：真实声纹（P1）

### 现状
[provider.py](../services/voiceprint/provider.py) 的 `embed()` 返回 `[mean, rms, zcr]` 三维统计量；[matcher.py](../services/voiceprint/matcher.py) 用余弦相似度 + `threshold=0.82`。

### 目标
替换为真实说话人 embedding，**保持 `embed(pcm16) -> list[float]` 契约不变**。

### 怎么改
用 `resemblyzer`（轻量、CPU 友好）或 `speechbrain` ECAPA / 3D-Speaker：

```python
# services/voiceprint/provider.py 内替换
import numpy as np
from resemblyzer import VoiceEncoder

class ResemblyzerVoiceprint(VoiceprintProvider):
    def __init__(self):
        self._encoder = VoiceEncoder()
    def embed(self, pcm16: bytes) -> list[float]:
        wav = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        return self._encoder.embed_utterance(wav).tolist()
```

### 必做：重校阈值
`threshold=0.82` 是针对 3 维统计量调的，对 256 维 embedding 完全不适用。需要用真人样本重跑 FA/FR：
- 找一组「本人多次」和「他人」样本，画出余弦分数分布，重新定 `threshold` 和 `min_margin`（[matcher.py:24](../services/voiceprint/matcher.py#L24)）。

### 验收
真人对真人 FA/FR 达标；注册流程（[enrollment/service.py](../services/enrollment/service.py)）无需改动即可用。

---

## 4. 模块四：情绪 / 风险结构化（P1）

### 现状
情绪（[pipeline.py:15-21](../services/dialogue/pipeline.py#L15-L21)）和风险（[risk.py:9-14](../services/analysis/risk.py#L9-L14)）都是关键词规则。

### 目标
用 LLM 返回结构化情绪/风险，**关键词规则保留作为安全兜底**。

### 怎么改
1. 让 DeepSeek 回复里带结构化情绪/风险字段（在 [deepseek.py](../services/dialogue/deepseek.py) 的 `reply` 里解析，或单独一次分类调用）。
2. **风险关键词规则必须保留**：`merge_risk`（[risk.py:17-21](../services/analysis/risk.py#L17-L21)）已经实现「取 max」，自杀/自伤等 urgent 关键词**不允许**被 LLM 结果拉低。

### 验收
普通情绪判断更细腻；urgent 关键词命中时风险仍判为 `urgent`（安全红线）。

---

## 5. 模块五：向量 embedding 检索（P1，RAG 升级）

### 现状
[retriever.py](../services/memory/retriever.py) 用 Jaccard 词法重叠排序；`memory_chunks.embedding_json` 字段空着。

### 目标
语义检索替代词法检索。

### 怎么改
1. 加 embedding provider（`sentence-transformers` + BGE-small 中文，CPU 可跑）。
2. 写记忆时算 embedding 存进 `embedding_json`（在 [app.py:410](../services/dialogue/app.py#L410) 的 `add_memory` 里补一列）。
3. 检索时算 query embedding，与候选做余弦 top-k，替换 [retriever.py:31-35](../services/memory/retriever.py#L31-L35) 的打分。
4. 保留 `visible_chunk` 权限过滤在打分**之前**执行（安全顺序不变，见 [RAG_DESIGN.md](RAG_DESIGN.md)）。

### 验收
语义相近但用词不同的记忆能被召回；在审批过的 fixture 集上做召回 benchmark。

---

## 6. 模块六：ESP32 真机接入（P2 / Phase 2）

### 现状
[mock_gateway.py](../services/device_gateway/mock_gateway.py) 是 placeholder，[mqtt_udp.py](../services/device_gateway/mqtt_udp.py) 是 loopback frame，host 固定 `127.0.0.1`。

### 目标
接真实 MQTT broker + UDP 音频，局域网 IP，ESP 固件，LCD 字幕/角色。

### 怎么改
1. 参考 `upstream/xiaozhi-esp32-server` 的 gateway 实现，把 loopback 换成真实 broker 连接。
2. 把 [configs/launcher.json](../configs/launcher.json) 的 `host` 从 `127.0.0.1` 改成局域网 IP。
3. 启动官方 server 的 gateway/OTA（WS 8000 / OTA 8003 / MQTT 1883 / UDP 8884），ESP 烧录固件后联调。
4. 声纹判定结果替换 [app.py:232](../services/dialogue/app.py#L232) 硬编码的 `accepted`。

### 验收
ESP 真机麦克风 → ASR → 对话 → TTS 播放 → LCD 字幕全链路通。

---

## 7. 通用验证清单

每次改完一个模块，跑：

```powershell
python -m pytest tests -q
python -m compileall -q services simulator scripts
```

（`python` 請用跑專案的那個解譯器：啟動器依序找 `IOT_PYTHON` → `configs/launcher.json` 的 `python` → 專案 `.venv` → PATH。`pytest` 目前不在 `requirements.txt`，需先 `python -m pip install pytest`。）

---

## 8. 注意事项 / 红线

1. **密钥不进 Git**：DeepSeek key、管理员密码只走 DPAPI 加密文件 / 环境变量，绝不写进 `.env` 提交。
2. **风险关键词规则不可删除**：自杀/自伤等 urgent 检测是安全底线，LLM 只能提升不能拉低。
3. **隐私边界**：个人记忆必须 `consent 有效 + identity accepted + approved` 才能进入上下文；原始语音、声纹向量、未同意的私人对话**绝不进 shared RAG**（见 [RAG_DESIGN.md](RAG_DESIGN.md)）。
4. **优先级建议**：先做模块一（RAG 接通）和模块二（TTS），这两处改动小、收益直接，且能让整个对话链路从「demo 占位」变成「真正可用」。

---

## 附：关键文件索引

| 关注点 | 文件 |
|---|---|
| 主程序 / 路由 | [services/dialogue/app.py](../services/dialogue/app.py) |
| 对话 pipeline | [services/dialogue/pipeline.py](../services/dialogue/pipeline.py) |
| 记忆 / RAG | [services/memory/](../services/memory/) |
| 数据库 schema | [services/storage/migrations.py](../services/storage/migrations.py) |
| TTS | [services/tts/](../services/tts/) |
| 声纹 | [services/voiceprint/](../services/voiceprint/) |
| 设备网关 | [services/device_gateway/](../services/device_gateway/) |
| 配置 | [configs/launcher.json](../configs/launcher.json) |
