# Phase 1 Execution Ledger

Plan: `docs/superpowers/plans/2026-09-14-phase1-software.md`

User authorized Phase 1 implementation on 2026-09-14. Phase 2 firmware is not authorized until hardware arrival. This file records actual work. A `[x]` entry means the PC implementation and automated contract checks are complete; human hardware, voice-quality, and production-operation gates are tracked separately. RAG remains a disabled extension point because its knowledge content is intentionally left for project-specific design.

## Preflight

Project is not a Git repository. Interpreter: `.venv/Scripts/python.exe` (Python 3.13); pytest 9.1.1. Taskboard CLI is unavailable; no LOCAL-3 status write was made.

Ruling: Use immutable source snapshots and this ledger instead of Git/worktree/commit-based skill helpers, as the approved plan explicitly preserves the non-Git project in place. Cost if wrong: later Git adoption must import these local changes; source snapshots remain available. Never delete the project or this audit trail as skill scratch cleanup.

Ruling: Protect existing runtime while implementing and testing; execute production migration only after review and recovery tests. Use explicit temporary paths and loopback test ports. Cost if wrong: live deployment happens later than code completion, but existing records are preserved.

## Plan Review

| Task | Internal consistency / dependency check | Execution decision |
|---|---|---|
| P1-01 | Global app imports can write production DB; launcher may load real key even with isolated logs | Test bootstrap and subprocess environment isolation before baseline; no network providers in tests |
| P1-02 | Uses P1-01 settings, auth, migrations, existing users schema | Add users module after foundation review; preserve existing IDs and demo markers |
| P1-03 | GPU unavailable vs corrupt model need separate failure handling | Bounded worker and observable fallback; no new Whisper download |
| P1-04 | Media ownership depends on session tables, used by PC in P1-06/08 | Define session/turn-scoped audio before exposing media |
| P1-05 | Enrollment needs P1-02 users, P1-03 normalized audio, P1-04 prompts | Independent verification and template commit; calibration is a human acceptance gate |
| P1-06 | Mic permission and playback are user-controlled browser capabilities | No automatic capture; only explicit start action |
| P1-07 | Uses P1-05 identity and P1-01 consent; private vectors distinct from public chunks | Owner filtering precedes similarity; consent checked at call time |
| P1-08 | Shared pipeline requires P1-03 through P1-07 and later P1-09 analysis | Nonimplemented provider states remain unavailable; no invented analysis |
| P1-09 | Updates existing dashboard read model and P1-08 pipeline | Separate risk analysis/review status, time windows, evidence and identity correction |
| P1-10 | Integrates P1-04/06/08 events plus P1-01 launcher | Real local protocol tests are distinct from ESP acceptance |
| P1-01 / all | app.py, migrations, test fixtures are shared | One implementation agent at a time; review before dependent work |
| P1-02 / 06 / 07 / 09 | user detail and consent UI are shared | Preserve route shapes, changes staged in dependency order |
| P1-03 / 05 / 07 | Model manifest and runtime dependencies are shared | ASR environment not overwritten; isolate incompatible providers |
| P1-04 / 06 / 08 / 10 | Device events, private media, playback ACK, cancel are shared | session_id + turn_id + segment_id ownership/ordering enforced end-to-end |
| P1-01 / 10 | Setup/start/stop scripts shared | Bring minimum auth/device startup integration into P1-01, finish runner integration P1-10 |

## Progress (2026-09-14 implementation pass)

- [x] P1-01: Test isolation, safe storage, management authorization (71 project tests pass; production backup/recovery remains an operator gate)
- [x] P1-02: Users and profiles (CRUD, pagination, summary API, Dashboard list and user detail page)
- [x] P1-03: ASR and model lifecycle (resident faster-whisper, WAV normalization, bounded worker, CUDA/OOM fallback; local CPU smoke passed)
- [x] P1-04: Server TTS and media (sentence splitter, Windows provider contract, deterministic fallback, ownership/TTL media store)
- [x] P1-05: Voiceprints and enrollment (three-sample wizard, encrypted baseline template, identify endpoint and decision classifier; FA/FR calibration remains a human gate)
- [x] P1-06: PC microphone wizard (session bootstrap, browser PCM/WAV recording, WebSocket events, 320x240 display and enrollment flow)
- [x] P1-07: DeepSeek boundary and RAG extension point (environment-only client, scope/consent filters and optional RAGFlow adapter; custom knowledge is deliberately disabled)
- [x] P1-08: Complete audio/display pipeline core (text orchestration, turn cancellation, event contract, WebSocket and simulator display)
- [x] P1-09: Analysis and risks (local rules, merge semantics, trends, review routes and user summary APIs)
- [x] P1-10: Gateway, launcher, acceptance core (MQTT payload, UDP frame loopback, capability/playback contracts, one-click lifecycle and acceptance script)

Remaining acceptance gates are human Windows TTS listening/playback, voiceprint FA/FR calibration, optional DeepSeek smoke with a newly rotated key, production backup/recovery, and all ESP hardware tests. These do not block the PC simulator from running with local fallback.

Launcher note: a backend process can start without credentials, but the normal one-click path is not a complete Dashboard acceptance path until `IOT_ADMIN_PASSWORD` is configured and a simulator device token is supplied. Without those prerequisites, protected APIs or simulator heartbeat fail by design.

Human recording, consent, voice quality, calibration, and actual ESP tests cannot be replaced by automated fake providers.
