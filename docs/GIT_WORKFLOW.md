# Git Workflow

The canonical repository is `https://github.com/gcgcg123/robot_chat.git`.
The `main` branch is the stable PC simulator baseline. The Xiaozhi source is a
pinned submodule, not a copied vendor tree.

## First clone

```powershell
git clone https://github.com/gcgcg123/robot_chat.git
cd robot_chat
git submodule update --init --recursive
```

Then create the Python environment with `首次設定.bat` and download the model:

```powershell
.\scripts\download-model.ps1
```

## Daily change flow

```powershell
git switch main
git pull --ff-only
git switch -c feature/<short-name>
# edit and test
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q services simulator scripts
git diff --check
git status --short
git add <source-files>
git commit -m "feat: describe the change"
git push -u origin feature/<short-name>
```

Do not commit `.env`, `data/`, `runtime/`, `logs/`, model weights, API keys,
device tokens, voiceprint templates, raw audio or SQLite databases. Do not use
`git push --force` on `main`.

## Updating the Xiaozhi reference

Review upstream changes separately, run the protocol tests, then update the
submodule pointer in its own commit. Never silently replace the pinned commit:

```powershell
git -C upstream/xiaozhi-esp32-server fetch origin
git -C upstream/xiaozhi-esp32-server checkout <reviewed-commit>
git add upstream/xiaozhi-esp32-server
git commit -m "chore: update Xiaozhi reference"
```

CI runs `pytest tests -q` so the upstream repository's own test package does
not collide with this project's `tests/conftest.py`.
