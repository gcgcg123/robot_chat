# Model Assets

Model weights are deliberately excluded from Git. This keeps clones and CI
small and avoids accidentally redistributing weights without checking the
upstream model card and license.

Two ASR backends are supported. `ASR_PROVIDER` selects one, and both are
declared in `manifest.json`:

## Default: SenseVoice-Small (ONNX)

- Repository: `csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`
- Target: `models/asr/sensevoice-small`
- Runtime: `sherpa-onnx`, CPU/int8 (CUDA/FP16 when a usable GPU is detected)
- Size: **~228 MB** (`model.int8.onnx`)
- Languages: zh, **yue (Cantonese)**, en, ja, ko
- Measured on the CPU-only reference laptop: **~0.5 s** for a 5-7 s utterance

Chosen as the default because it is non-autoregressive (one forward pass instead
of an encoder/decoder loop), so it does not pay Whisper's fixed 30-second window
cost, and because it keeps Cantonese accurate instead of trading it away.

## Optional: Whisper large-v3-turbo (CTranslate2)

- Repository: `mobiuslabsgmbh/faster-whisper-large-v3-turbo`
- Target: `models/asr/whisper-large-v3-turbo-ct2`
- Runtime: `faster-whisper` with CUDA/FP16 when available, otherwise CPU/int8
- Size: ~1.5 GB
- Measured on the same machine: **~35 s** per utterance, because Whisper always
  encodes a padded 30-second window regardless of the clip length

Keep this backend if you need its slightly higher accuracy and can afford the
latency, or if you run on a machine with a real CUDA GPU.

## Always installed: BGE-small-zh-v1.5 (ONNX)

- Repository: `Xenova/bge-small-zh-v1.5`
- Target: `models/embedding/bge-small-zh-v1.5`
- Runtime: `onnxruntime` + `tokenizers` (no torch)
- Size: **~90 MB** (`onnx/model.onnx`, 512-dim output)
- Selection: `IOT_EMBEDDING_MODEL_PATH`, defaulting to the directory above

This one is not an ASR backend and there is nothing to choose: the long-term
memory flywheel embeds every stored probe and every new question, and uses the
cosine similarity between them to decide whether a memory is relevant enough to
inject. Measured separation on this machine with the calibrated floor (`0.68`):
real matches score `0.78-1.00`, unrelated questions `0.38-0.58`.

It is optional only in the sense that the service still starts without it. In
that case scoring falls back to lexical similarity (character bigrams), which
still works but is noticeably weaker for Chinese paraphrases, so memory recall
becomes hit-or-miss. Download it.

## Optional: ECAPA-VoxCeleb (speaker recognition)

- Repository: `speechbrain/spkrec-ecapa-voxceleb`
- Target: `models/voiceprint/ecapa-voxceleb`
- Runtime: `speechbrain` + `torch` (`VOICEPRINT_PROVIDER=ecapa`), CPU by default
- Size: **~85 MB** (`embedding_model.ckpt` is 81 MB, plus the classifier and
  normalisation checkpoints)
- Output: a 192-dim L2-normalised embedding, compared by cosine similarity

Unlike the ASR and embedding models, fetching this one is not enough to use it:
the Python packages come from `requirements-voiceprint.txt` (~730 MB, torch
dominates). Without them voiceprint enrollment answers 503 with the install
command and `/health` reports `voiceprint_dependencies_missing` -- it does not
fall back to the placeholder provider, because that provider returns three
acoustic numbers and cannot tell speakers apart.

## Knowledge-base reranker (optional, off by default)

- Repository: `BAAI/bge-reranker-base` (on ModelScope)
- Target: `models/rerank/bge-reranker-base`
- Runtime: `transformers` + `torch` (`IOT_KNOWLEDGE_RERANK=local`), CPU only
- Size: **~1.06 GB** (`model.safetensors` is 1,112,206,140 B; six files in
  total, each with its SHA-256 in `models/manifest.json`)
- Purpose: a cross-encoder that reads a question and a manual passage together,
  as the second stage of knowledge retrieval

It is fetched from **ModelScope**, not Hugging Face: measured on 2026-10-03,
`huggingface.co` and `hf-mirror.com` both time out from the reference network
while `modelscope.cn` answers. `scripts/download-rerank-model.py` does the
download and is what `-Model rerank` calls.

**Enabled or not, the retrieval path is unchanged unless you ask for it.** The
second stage measured a real recall gain (26/28 → 27/28 questions answered) but
its score bands overlap the ones it must reject, so a question the manual does
not answer gets passages as confidently as a real hit -- see
`docs/RAG_KNOWLEDGE_PLAN.md` A9.7.15 for the numbers. It also costs 8 s per
reranked turn on a 4-core CPU. `IOT_KNOWLEDGE_RERANK=0` is the default; the
switch, the checksums and the tests stay so a better embedding model can turn it
on later.

## Downloading

```powershell
# default ASR backend (SenseVoice, ~228 MB)
.\scripts\download-model.ps1

# the faster-whisper baseline instead (~1.5 GB)
.\scripts\download-model.ps1 -Model whisper

# both ASR backends
.\scripts\download-model.ps1 -Model both

# the memory embedding model (~90 MB, needed by whichever backend you use)
.\scripts\download-model.ps1 -Model embedding

# speaker recognition weights (~85 MB; also needs requirements-voiceprint.txt)
.\scripts\download-model.ps1 -Model voiceprint

# knowledge-base reranker (~1.06 GB, from ModelScope; also needs torch)
.\scripts\download-model.ps1 -Model rerank
```

The script prefers the Hugging Face `hf` / `huggingface-cli` executable next to
the project interpreter and falls back to the `huggingface_hub` Python API, so a
missing CLI is not fatal. It never puts tokens or credentials in the repository.

Behind a slow or blocked connection, point it at a mirror:

```powershell
.\scripts\download-model.ps1 -Endpoint https://hf-mirror.com
```

`一鍵安裝並啟動.bat` runs this automatically for the configured backend plus the
embedding model, and skips whichever download is already present. The 85 MB
speaker model is fetched only with `-WithVoiceprint`, because it is useless
without the optional packages -- and the 1.06 GB reranker only with
`-WithRerank`, because it is off at runtime as well:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-project.ps1 -WithVoiceprint
powershell -ExecutionPolicy Bypass -File scripts\bootstrap-project.ps1 -WithRerank
```

## Verifying

```powershell
py -3 -m pytest tests\models tests\audio -q
```

`manifest.json` carries the expected SHA-256 for the primary weight file of each
backend; the download script prints the hash it observed.

## Notes

- A bare `models/asr/whisper-large-v3-turbo` Transformers checkpoint is **not**
  used by either backend and is not shipped. `faster-whisper` needs the
  CTranslate2 directory (`whisper-large-v3-turbo-ct2`).
- Switching backends only needs one line: set `ASR_PROVIDER` to `sensevoice` or
  `whisper` (in `configs/launcher.json`, or `ASR_PROVIDER` in the environment).
