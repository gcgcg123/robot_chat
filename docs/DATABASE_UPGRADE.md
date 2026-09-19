# Updating main after PR #3

Schema versions belong to the SQLite database, not to Docker. The integrated
application supports schema 5 and upgrades existing schema 3 and 4 databases on
startup. A fresh database is also created at schema 5. Repeated startup preserves
existing data. Databases newer than the supported schema are rejected.

## Before updating

Commit or stash your own source changes first. Keep `.env`, `.env.docker`, model
files and runtime data local; do not add them to Git. Run these commands from the
existing project directory, while the previous image is still available:

```powershell
docker compose stop dialogue
docker compose run --rm --no-deps --entrypoint python dialogue scripts/backup-runtime.py /var/lib/iotgroup5/emotional_robot.sqlite3 /var/lib/iotgroup5/backups/before-schema5.sqlite3
```

The backup command refuses to overwrite an existing nonempty backup. Choose a
new filename for a later upgrade. Keep a second copy outside the Docker volume.
If there is no existing database, skip the backup step.

## Update after the PR is merged

```powershell
git switch main
git pull --ff-only origin main
docker compose up -d --build dialogue
docker compose logs --tail 50 dialogue
Invoke-RestMethod http://127.0.0.1:8080/health
```

Open `http://127.0.0.1:8080/dashboard` and sign in with the password configured in
`.env.docker`. Start the service through Compose so its environment, ports,
models and persistent volume are included. A separately started Docker Desktop
container does not automatically inherit those settings.

Do not run `docker compose down -v` during an upgrade: that deletes the database
volume. Cloning again does not reset or upgrade an existing Docker volume.

## Models and configuration

Existing `.env.docker` files without `ASR_PROVIDER` continue to use Whisper.
Set `ASR_PROVIDER=sensevoice` only after downloading that model into
`models/asr/sensevoice-small`; use `ASR_PROVIDER=whisper` for the existing
`models/asr/whisper-large-v3-turbo-ct2` model. The environment file now controls
the provider instead of Compose overriding it. The new configuration template
selects SenseVoice. Download models on the host because `/models` is read-only.

The optional BGE embedding model enables semantic memory retrieval. Without it,
memory retrieval uses lexical matching. Voiceprint samples are specific to their
model: changing providers requires recording the enrollment samples again.

## Rollback and parallel branches

After migration, an old schema-3 application cannot open a schema-5 database.
Rollback requires both the previous application version and the matching backup,
with all database users stopped. Keep the upgraded database separately so changes
made after the upgrade are not lost. Do not change `PRAGMA user_version` manually.

Use separate data volumes for branches with different schema versions. When
running branches simultaneously, also assign distinct container names and host
ports. A Compose project name alone does not override the fixed container name
or port in this repository.
