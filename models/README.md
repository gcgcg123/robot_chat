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

## Downloading

```powershell
# default backend (SenseVoice, ~228 MB)
.\scripts\download-model.ps1

# the faster-whisper baseline instead (~1.5 GB)
.\scripts\download-model.ps1 -Model whisper

# both
.\scripts\download-model.ps1 -Model both
```

The script prefers the Hugging Face `hf` / `huggingface-cli` executable next to
the project interpreter and falls back to the `huggingface_hub` Python API, so a
missing CLI is not fatal. It never puts tokens or credentials in the repository.

Behind a slow or blocked connection, point it at a mirror:

```powershell
.\scripts\download-model.ps1 -Endpoint https://hf-mirror.com
```

`一鍵安裝並啟動.bat` runs this automatically for the configured backend and
skips the download when the model is already present.

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
