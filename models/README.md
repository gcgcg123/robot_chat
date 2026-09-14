# Model Assets

Model weights are deliberately excluded from Git. This keeps clones and CI
small and avoids accidentally redistributing weights without checking the
upstream model card and license.

The Phase 1 ASR baseline is the CTranslate2 model used by `faster-whisper`:

- Repository: `mobiuslabsgmbh/faster-whisper-large-v3-turbo`
- Target: `models/asr/whisper-large-v3-turbo-ct2`
- Runtime: `faster-whisper` with CUDA/FP16 when available, otherwise CPU/int8
- Expected local `model.bin` SHA-256 for the current baseline:
  `E76620F83D5F5B69EFD3D87E3DC180C1BD21DF9FBEBACFD4335E5E1EFCC018DA`

Download after cloning:

```powershell
.\scripts\download-model.ps1
```

The script requires the Hugging Face `hf` or `huggingface-cli` command. It
does not put tokens or credentials in the repository. Re-run the local smoke
test after downloading:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\models tests\audio -q
```

The Transformers checkpoint in `models/asr/whisper-large-v3-turbo` is kept as
a local reference only. It is not a drop-in replacement for the CTranslate2
directory.
