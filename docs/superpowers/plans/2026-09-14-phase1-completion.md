# Phase 1 PC Product Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the non-ESP PC product path so a user can configure an administrator, start the service, register a profile and voice sample with a computer microphone, run a simulated half-duplex conversation, and inspect the resulting device, conversation, emotion and risk data in the Dashboard.

**Architecture:** Keep the existing FastAPI/SQLite service and Windows launcher. Add a protected browser UI for login, profile/enrollment, simulator audio and user detail; connect it to a session-scoped WebSocket and the existing dialogue/audio contracts. Keep RAG behind an optional disabled provider boundary: the phase-1 conversation must work with no retrieval results and must not add project-specific knowledge data.

**Tech Stack:** Python 3.13 virtualenv, FastAPI, SQLite, faster-whisper/CT2, browser MediaRecorder/WebSocket/Web Audio APIs, Windows PowerShell 5.1 launcher scripts, pytest.

**Spec:** `docs/superpowers/plans/2026-09-14-two-stage-implementation.md`, `docs/RAG_DESIGN.md`, `docs/DEVICE_PROTOCOL.md`

## Global Constraints

- ESP firmware, LCD wiring, Wi-Fi, MQTT broker and real UDP peer remain Phase 2 and are not required for this plan.
- RAG retrieval content is out of scope; use an optional provider that returns no chunks by default and preserve the `shared`, `user:<id>`, and `analysis` boundaries.
- Private memory is readable only for an `accepted` identity with current consent; unknown or ambiguous identity never reads private memory.
- Audio uploads are limited to 10 MB and 60 seconds and are normalized to mono 16 kHz before ASR/voiceprint processing.
- Secrets are never committed or written to HTML, source code, `.env`, or command files; launcher setup uses Windows DPAPI for the DeepSeek key and a locally configured admin password.
- Every new behavior is covered by a focused pytest or browser-contract test before implementation (red-green-refactor).
- The default product flow is half-duplex and must expose cancellation, stale-turn rejection, and clear error states.

### Task 1: First-run authentication and launcher prerequisites

**Files:**
- Modify: `scripts/setup-project.ps1`, `scripts/start-project.ps1`, `scripts/stop-project.ps1`, `scripts/launcher-common.ps1`
- Create: `scripts/provision-simulator.ps1`
- Modify: `services/dialogue/app.py`, `services/security/auth.py`
- Test: `tests/test_project_launcher.py`, `tests/security/test_auth.py`

**Interfaces:**
- `POST /api/auth/login` accepts `{actor_id, password}` and sets the session cookie.
- `POST /api/auth/logout` revokes the session after CSRF validation.
- `POST /api/device/heartbeat` accepts a bearer device token for simulator calls and validates the device binding.
- `provision-simulator.ps1 -DataDir <path> -DeviceId <id>` creates a device-bound token without printing it to logs.

- [ ] Write failing tests for missing-admin diagnostics, simulator token provisioning, duplicate start, and cleanup after heartbeat failure.
- [ ] Run `pytest tests/test_project_launcher.py tests/security/test_auth.py -q` and confirm the new tests fail for the missing behavior.
- [ ] Implement secure first-run admin setup and device-token provisioning; make start detect an existing owned service and repair only a missing heartbeat instead of launching a second backend.
- [ ] Add ownership validation and rollback for service/heartbeat startup failures.
- [ ] Run the focused tests and then the full project suite.

### Task 2: Dashboard login, user registry and detail page

**Files:**
- Modify: `services/dashboard/index.html`, `services/dashboard/dashboard.css`, `services/dashboard/dashboard.js`
- Create: `services/dashboard/login.js`, `services/dashboard/user_detail.js`, `services/dashboard/enrollment.js`
- Modify: `services/dialogue/app.py`, `services/users/repository.py`, `services/users/schemas.py`
- Test: `tests/users/test_api.py`, `tests/users/test_detail_api.py`, `tests/test_dashboard_assets.py`

**Interfaces:**
- `GET /dashboard` serves the login-aware Dashboard shell.
- `GET /api/auth/session` returns actor, role and CSRF token when authenticated.
- `GET /api/users` returns `{items,total,page,page_size}`.
- `GET /api/users/{user_id}` returns profile and registration metadata.
- `GET /api/users/{user_id}/summary?period=day|week` returns emotion counts and risk summary.

- [ ] Write failing tests for login state, user detail navigation, empty states, profile validation, and deletion confirmation.
- [ ] Run the focused tests and verify failure.
- [ ] Implement login form, session refresh, logout, user table, profile form, detail view, and error/empty states without adding a text-chat box.
- [ ] Render device status, recent mood, conversation list, risk level and review status on the detail page.
- [ ] Run focused frontend asset/API tests and the full suite.

### Task 3: Real PC microphone simulator and enrollment wizard

**Files:**
- Create: `services/dashboard/simulator.html`, `services/dashboard/simulator.css`, `services/dashboard/simulator.js`
- Modify: `services/dialogue/app.py`
- Create: `simulator/audio_capture.py`, `simulator/audio_player.py`
- Modify: `simulator/state.mjs`
- Test: `tests/test_simulator.py`, `tests/test_simulator_assets.py`, `tests/integration/test_enrollment_flow.py`

**Interfaces:**
- `POST /api/simulator/sessions` creates a ten-minute session with mic, speaker, display and enrollment capabilities.
- `POST /api/enrollments` starts enrollment for a profile.
- `POST /api/enrollments/{enrollment_id}/samples` accepts normalized WAV sample bytes and returns state/quality.
- `POST /api/enrollments/{enrollment_id}/complete` verifies the template and returns the decision.
- `WS /ws/simulator/{session_id}` carries `audio.start`, `audio.chunk`, `caption`, `tts.segment`, `playback.ack`, `turn.cancel`, and `error` events.

- [ ] Write failing tests for session expiry, missing browser capability, sample count/quality, WebSocket event ordering and stale session rejection.
- [ ] Run the focused tests and verify failure.
- [ ] Implement the page, explicit microphone permission flow, MediaRecorder WAV conversion, playback queue, 320x240 display canvas, and enrollment stepper.
- [ ] Keep audio in memory or short-lived media storage and never persist raw samples beyond the enrollment policy.
- [ ] Run browser-contract tests and full pytest.

### Task 4: Voiceprint provider and enrollment persistence

**Files:**
- Modify: `services/voiceprint/provider.py`, `services/voiceprint/matcher.py`, `services/voiceprint/templates.py`, `services/enrollment/service.py`
- Create: `services/voiceprint/storage.py`, `services/voiceprint/config.py`
- Modify: `services/storage/migrations.py`, `services/dialogue/app.py`
- Test: `tests/voiceprint/test_provider.py`, `tests/voiceprint/test_enrollment_api.py`, `tests/voiceprint/test_matcher.py`

**Interfaces:**
- `VoiceprintProvider.embed(pcm16: bytes) -> list[float]` returns a normalized embedding or a typed unavailable error.
- `VoiceprintMatcher.identify(embedding, templates) -> IdentityResult` returns `accepted`, `ambiguous`, or `unknown`.
- Enrollment persists template metadata, model revision, consent, and revocation timestamp; raw audio is not stored as the template.

- [ ] Write failing tests for deterministic fixture embeddings, encrypted-at-rest template bytes, threshold/margin decisions, revocation and cross-user isolation.
- [ ] Run the focused tests and verify failure.
- [ ] Implement a local provider selected by configuration, with deterministic fixture mode for CI and a documented CPU-capable embedding backend for the PC demo.
- [ ] Add sample quality checks, 3-5 sample enrollment, one held-out verification sample, and explicit unknown/ambiguous responses.
- [ ] Run voiceprint tests and full pytest; record provider/model revision in the acceptance report.

### Task 5: TTS provider and media playback contract

**Files:**
- Modify: `services/tts/provider.py`, `services/tts/media_store.py`, `services/tts/segments.py`
- Create: `services/tts/windows.py`, `services/tts/config.py`
- Modify: `services/dialogue/app.py`
- Test: `tests/tts/test_segments.py`, `tests/tts/test_windows.py`, `tests/tts/test_media_store.py`

**Interfaces:**
- `TtsProvider.synthesize(text: str, voice: str) -> TtsArtifact` returns a short-lived WAV artifact and matching sentence segments.
- `GET /api/media/{media_id}` enforces session/turn ownership and expiry.
- `tts.segment` includes `segment_id`, text, media URL, sequence and final flag.

- [ ] Write failing tests for Windows provider selection, non-empty WAV output, media expiry, ownership rejection and segment order.
- [ ] Run the focused tests and verify failure.
- [ ] Implement Windows local voice selection with a deterministic fallback only in test mode; keep the fallback clearly marked as non-audio.
- [ ] Wire playback ACK and cancellation to media revocation.
- [ ] Run TTS tests and full pytest.

### Task 6: Dialogue pipeline without RAG content

**Files:**
- Modify: `services/dialogue/pipeline.py`, `services/dialogue/deepseek.py`, `services/memory/ragflow.py`, `services/memory/retriever.py`
- Create: `services/dialogue/contracts.py`, `services/dialogue/turns.py`
- Modify: `services/dialogue/app.py`
- Test: `tests/dialogue/test_pipeline.py`, `tests/dialogue/test_deepseek.py`, `tests/dialogue/test_websocket.py`

**Interfaces:**
- `process_text(text, user_id, identity, session_id, turn_id) -> DialogueResult` works when retrieval returns zero chunks.
- `DialogueResult` contains normalized text, emotion, risk, reply, citations, segments and latency metadata.
- `RAGProvider.retrieve(...) -> list[MemoryChunk]` remains optional and returns `[]` when disabled.

- [ ] Write failing tests for no-RAG conversation, DeepSeek fallback, timeout/error classification, cancellation, private-memory denial and response schema.
- [ ] Run focused tests and verify failure.
- [ ] Implement the non-RAG path, structured fallback reply, safety/risk merge, turn registry and WebSocket event emission.
- [ ] Keep RAG context labelled untrusted and do not add custom knowledge records.
- [ ] Run dialogue/WebSocket tests and full pytest.

### Task 7: Device event, MQTT/UDP loopback and analytics

**Files:**
- Modify: `services/device_gateway/mqtt_udp.py`, `services/device_gateway/events.py`, `services/device_gateway/mock_gateway.py`
- Create: `services/device_gateway/pc.py`, `services/device_gateway/protocol_peer.py`
- Modify: `services/analysis/risk.py`, `services/dashboard/read_model.py`, `services/dialogue/app.py`
- Test: `tests/device_gateway/test_protocol_peer.py`, `tests/analysis/test_routes.py`, `tests/integration/test_pc_loopback.py`

**Interfaces:**
- MQTT control uses `device.register`, `device.heartbeat`, `device.capabilities`, `turn.cancel` JSON payloads.
- UDP loopback uses `IOT1` framing with session ID and monotonic sequence; wrong-session and duplicate frames are rejected.
- `GET /api/analytics/overview?days=7|30` and `GET /api/users/{user_id}/risk-events` expose Dashboard data.

- [ ] Write failing tests for protocol peer, duplicate/out-of-order frames, analytics windows, risk review transitions and device offline states.
- [ ] Run focused tests and verify failure.
- [ ] Implement PC adapter, broker-independent loopback peer, trend aggregation, risk review endpoint and Dashboard charts.
- [ ] Keep real MQTT broker/ESP transport behind the Phase 2 adapter boundary.
- [ ] Run integration tests and full pytest.

### Task 8: Acceptance packaging and documentation

**Files:**
- Modify: `一鍵啟動.bat`, `首次設定.bat`, `開發模式.bat`, `一鍵停止.bat`, `docs/STARTUP_GUIDE.md`
- Create: `scripts/acceptance-check.ps1`, `docs/PC_ACCEPTANCE.md`
- Modify: `docs/PROJECT_PROGRESS_2026-09-14.html`, `docs/IMPLEMENTATION_STATUS.md`, `README.md`
- Test: `tests/test_acceptance_check.py`

**Interfaces:**
- `scripts/acceptance-check.ps1` runs preflight, starts an isolated service, checks login, user creation, simulator session, no-RAG dialogue, analytics and clean stop.
- Exit code 0 means only the PC acceptance checklist passed; it never claims ESP or RAGFlow acceptance.

- [ ] Write failing tests for acceptance-check exit codes, isolated data directories and no-secret logging.
- [ ] Run the focused test and verify failure.
- [ ] Implement the acceptance script and update the one-click guide with prerequisites and expected limitations.
- [ ] Run the full project suite, compileall, acceptance-check and launcher start/stop/restart tests.
- [ ] Record exact evidence and remaining risks in the HTML report.
