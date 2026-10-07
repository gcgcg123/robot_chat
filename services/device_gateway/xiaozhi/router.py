"""FastAPI routes for the ESP32 access layer.

Device-facing (no admin cookie -- an ESP32 has none):

    WS   {esp_path}                      the xiaozhi audio/text protocol
    POST /xiaozhi/ota/                   address + token delivery
    GET  /xiaozhi/ota/                   human-readable probe
    GET  /xiaozhi/ota/download/{file}    firmware download

Admin-facing (existing admin session + CSRF, same helpers as ``app.py``):

    GET/POST /api/esp/settings           read / write the ESP settings block
    GET      /api/esp/status             codec, VAD, TTS, network, live sessions
    GET      /api/esp/network            LAN addresses the device should use
    GET      /api/esp/ota-requests       OTA audit trail
    GET/POST /api/esp/firmware           firmware inventory / upload
    DELETE   /api/esp/firmware/{name}    remove a firmware file
    GET      /api/devices/{id}           device detail (incl. live state)
    GET      /api/devices/{id}/sessions  connection history
    GET      /api/devices/{id}/commands  command history
    POST     /api/devices/{id}/commands  queue a command
    POST     /api/devices/{id}/bind      bind the device to a user
    DELETE   /api/devices/{id}/bind      unbind
    GET      /api/devices/{id}/token     show the derived device token
    WS       /ws/device-observe          live device event stream for the dashboard
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from services.device_gateway.xiaozhi import network, ota, protocol, settings_store, store
from services.device_gateway.xiaozhi.auth import device_token, extract_device_headers
from services.device_gateway.xiaozhi.config import ROOT, EspSettings
from services.device_gateway.xiaozhi.context import GatewayContext
from services.device_gateway.xiaozhi.session import XiaozhiSession
from services.device_gateway.xiaozhi.vad import create_vad
from services.security.auth import SESSION_COOKIE, _session

DEFAULT_ESP_PATH = "/xiaozhi/v1/"
COMMAND_TYPES = {"speak", "abort", "close", "iot", "mcp"}
MAX_FIRMWARE_BYTES = 8 * 1024 * 1024


class EspSettingsPatch(BaseModel):
    enabled: bool | None = None
    path: str | None = Field(default=None, max_length=64)
    require_token: bool | None = None
    allowed_devices: list[str] | None = Field(default=None, max_length=64)
    default_user_id: str | None = Field(default=None, min_length=1, max_length=128)
    sample_rate: int | None = Field(default=None, ge=8000, le=48000)
    frame_duration_ms: int | None = Field(default=None, ge=10, le=120)
    uplink_sample_rate: int | None = Field(default=None, ge=8000, le=48000)
    ota_enabled: bool | None = None
    ota_base_url: str | None = Field(default=None, max_length=200)
    vad_provider: str | None = Field(default=None, max_length=32)
    vad_silence_ms: int | None = Field(default=None, ge=200, le=5000)
    vad_min_speech_ms: int | None = Field(default=None, ge=60, le=2000)
    vad_max_utterance_ms: int | None = Field(default=None, ge=1000, le=120000)
    vad_rms_threshold: float | None = Field(default=None, gt=0, le=20000)
    tts_enabled: bool | None = None
    llm_stream: bool | None = None
    tts_max_chars: int | None = Field(default=None, ge=12, le=200)
    tts_min_chars: int | None = Field(default=None, ge=1, le=50)
    wake_word_hold_seconds: float | None = Field(default=None, ge=0, le=10)
    standby_seconds: int | None = Field(default=None, ge=5, le=3600)
    idle_timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    standby_notice: str | None = Field(default=None, max_length=200)
    tools_enabled: bool | None = None
    tool_timeout_seconds: float | None = Field(default=None, ge=1, le=60)
    wake_words: list[str] | None = Field(default=None, max_length=16)
    voiceprint_enabled: bool | None = None
    # 0 means "inherit VOICEPRINT_THRESHOLD", so the floor is 0 and not a plausible cosine value.
    voiceprint_threshold: float | None = Field(default=None, ge=0, le=0.95)
    voiceprint_min_margin: float | None = Field(default=None, ge=0, le=0.95)
    voiceprint_confirm_turns: int | None = Field(default=None, ge=1, le=5)
    voiceprint_continuity_floor: float | None = Field(default=None, ge=0, le=0.95)


class CommandRequest(BaseModel):
    type: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


class BindRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)


def _tts_report(provider) -> dict[str, Any]:
    """Say plainly whether real speech is produced, instead of only naming a class.

    The provider declares it (``TtsProvider.produces_audio``) rather than this function guessing
    from the class name: measured 2026-10-06, ``WindowsTtsProvider`` returns 14080 bytes of pure
    zeros and still answered ``produces_audio: true`` -- the wrong answer to give someone who is
    debugging a silent device.
    """

    name = type(provider).__name__
    # A provider whose capability needs a probe (``WindowsTtsProvider``) is asked rather than read:
    # its ``produces_audio`` attribute is False until SAPI has been looked for, so reading it from
    # here answers "not yet" instead of "no" -- measured 2026-10-06, same checkout, two restarts,
    # two different answers purely because a turn had happened in between.
    probe = getattr(provider, "capability", None)
    if callable(probe):
        try:
            audible = bool(probe())
        except Exception:  # noqa: BLE001 - a broken probe is a diagnostic, not a failed status call
            audible = bool(getattr(provider, "produces_audio", True))
    else:
        audible = bool(getattr(provider, "produces_audio", True))
    # A provider that can explain itself is preferred over the generic label: "no_sapi_voices" or
    # "synthesis_failed:RuntimeError" is what an operator can act on.
    reason = "" if audible else (str(getattr(provider, "diagnostic", "") or "") or "silence_provider")
    return {"provider": name, "produces_audio": audible, "reason": reason}


def _enrollment_summary(events: list[dict[str, Any]], active_by_user: dict[str, int]) -> dict[str, Any] | None:
    """The last enrollment attempt, for a panel that has no pending item to show.

    ``voiceprint_enrolled`` is the terminal row (templates built or the build failed); a
    ``voiceprint_enrollment`` row is one sample, accepted or rejected with the reason the operator
    heard spoken back. Reporting both means "5/5 sentences, 5 templates" is visible *after* the
    enrollment is over -- which is exactly when the operator is deciding whether it worked.
    """

    if not events:
        return None
    newest = events[0]
    payload = dict(newest.get("payload") or {})
    user_id = str(payload.get("user_id") or "")
    completed = str(newest.get("event_type")) == "voiceprint_enrolled"
    return {
        "at": newest.get("created_at"),
        "completed": completed,
        "ok": bool(payload.get("ok") if completed else payload.get("accepted")),
        "user_id": user_id,
        "reason": str(payload.get("reason") or ""),
        "samples": int(payload.get("samples") or payload.get("sample_count") or 0),
        "required_samples": int(payload.get("required_samples") or 0),
        "templates": int(payload.get("templates") or active_by_user.get(user_id, 0)),
        "model_version": str(payload.get("model_version") or ""),
        "kept_previous": bool(payload.get("kept_previous")),
    }


def _port(ctx: GatewayContext) -> int:
    """The TCP port the device should dial (PROJECT_PORT wins over the dataclass)."""
    configured = os.getenv("PROJECT_PORT", "").strip()
    if configured.isdigit():
        return int(configured)
    return int(getattr(ctx.runtime, "port", 8080) or 8080)


def _query_of(target) -> str:
    url = getattr(target, "url", None)
    return getattr(url, "query", "") or ""


def mount_xiaozhi_routes(application: FastAPI, ctx: GatewayContext) -> None:
    """Attach every ESP route. Purely additive: no existing route is replaced."""
    ctx.vad_status = create_vad(ctx.settings)[1]

    # ----------------------------------------------------------- device: WS
    paths = {ctx.settings.path, ctx.settings.path.rstrip("/"), DEFAULT_ESP_PATH, DEFAULT_ESP_PATH.rstrip("/")}

    async def xiaozhi_websocket(websocket: WebSocket) -> None:
        settings: EspSettings = ctx.settings
        if not settings.enabled:
            await websocket.accept()
            await websocket.send_text(protocol.dumps(protocol.error_message("esp_access_disabled", "")))
            await websocket.close(code=4403)
            return

        headers = dict(websocket.headers)
        identity = extract_device_headers(headers, _query_of(websocket))
        device_id = identity["device_id"]
        if not device_id:
            await websocket.accept()
            await websocket.send_text(protocol.dumps(protocol.error_message("device_id_required", "")))
            await websocket.close(code=4400)
            return
        if ctx.live.count() >= ctx.settings.max_sessions:
            await websocket.accept()
            await websocket.send_text(protocol.dumps(protocol.error_message("too_many_sessions", "")))
            await websocket.close(code=4429)
            return

        session = XiaozhiSession(
            ctx,
            websocket,
            device_id=device_id,
            client_id=identity["client_id"],
            client_ip=websocket.client.host if websocket.client else "",
            authorization=identity["authorization"],
            board_model=str(headers.get("device-model", "") or headers.get("device_model", "") or ""),
            firmware=str(headers.get("device-version", "") or headers.get("firmware-version", "") or ""),
        )
        await session.run()

    for path in sorted(paths):
        application.add_api_websocket_route(path, xiaozhi_websocket)

    # ----------------------------------------------------------- device: OTA
    @application.post("/xiaozhi/ota/")
    async def ota_post(request: Request):
        if not ctx.settings.enabled:
            return JSONResponse({"success": False, "message": "esp_access_disabled"}, status_code=503)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            body = {}
        headers = dict(request.headers)
        identity = extract_device_headers(headers, _query_of(request))
        device_id = identity["device_id"]
        if not device_id:
            return JSONResponse({"success": False, "message": "device_id_required"}, status_code=400)

        board_model = ""
        for key in ("device-model", "device_model", "model"):
            if headers.get(key):
                board_model = str(headers[key]).strip()
                break
        if not board_model:
            board = body.get("board") if isinstance(body, dict) else None
            board_model = str((board or {}).get("type", "") if isinstance(board, dict) else "") or str(
                (body or {}).get("model", "") if isinstance(body, dict) else ""
            )
        device_version = ""
        for key in ("device-version", "device_version", "firmware-version", "app-version", "application-version"):
            if headers.get(key):
                device_version = str(headers[key]).strip()
                break
        if not device_version and isinstance(body, dict):
            application_block = body.get("application")
            if isinstance(application_block, dict):
                device_version = str(application_block.get("version", "") or "")
        device_version = device_version or "0.0.0"
        board_model = board_model or "default"

        client_ip = request.client.host if request.client else ""
        payload = ota.build_ota_payload(
            ctx.settings,
            device_id=device_id,
            client_id=identity["client_id"],
            board_model=board_model,
            device_version=device_version,
            port=_port(ctx),
        )
        with ctx.db() as conn:
            store.upsert_device(
                conn,
                device_id=device_id,
                transport="esp_ota",
                protocol_version=None,
                board_model=board_model,
                firmware=device_version,
                client_id=identity["client_id"],
                client_ip=client_ip,
                capabilities=["ota_pending"],
                identity_verified=False,
                is_simulator=False,
            )
            store.record_ota_request(
                conn,
                device_id=device_id,
                client_id=identity["client_id"],
                board_model=board_model,
                device_version=device_version,
                client_ip=client_ip,
                granted_ws_url=str(payload.get("websocket", {}).get("url", "")),
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @application.get("/xiaozhi/ota/")
    async def ota_probe(request: Request):
        probe = ota.build_ota_probe(ctx.settings, port=_port(ctx))
        state = "已启用" if (ctx.settings.enabled and ctx.settings.ota_enabled) else "未启用"
        return PlainTextResponse(
            f"OTA 接口运行正常（{state}）。向设备下发的 WebSocket 地址：{probe['websocket_url']}\n",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/xiaozhi/ota/download/{filename}")
    async def ota_download(filename: str):
        if not ctx.settings.ota_enabled:
            raise HTTPException(status_code=404, detail="firmware_disabled")
        resolved = ota.resolve_firmware_path(ctx.settings.ota_bin_dir, filename)
        if resolved is None:
            raise HTTPException(status_code=404, detail="firmware_not_found")
        return FileResponse(resolved, media_type="application/octet-stream", filename=resolved.name)

    # ----------------------------------------------------------- admin: ESP
    @application.get("/api/esp/status")
    def esp_status(request: Request):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
        probe = ota.build_ota_probe(ctx.settings, port=_port(ctx))
        asr = application.state.asr_service.snapshot() if hasattr(application.state, "asr_service") else {"status": "unavailable"}
        return {
            "settings": ctx.settings.as_dict(),
            "opus": ctx.opus,
            "vad": ctx.vad_status,
            "tts": _tts_report(ctx.tts_provider),
            "asr": {"status": asr.get("status"), "device": asr.get("device"), "backend": asr.get("backend")},
            "network": probe,
            "live": [device.as_dict() for device in ctx.live.all()],
            "tools": ctx.tools.snapshot(),
            "observers": ctx.observer.snapshot(),
            "commands": ctx.commands.snapshot(),
            "active_sessions": ctx.live.count(),
            "max_sessions": ctx.settings.max_sessions,
        }

    @application.get("/api/esp/network")
    def esp_network(request: Request):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
        probe = ota.build_ota_probe(ctx.settings, port=_port(ctx))
        addresses = ctx.settings.allowed_devices
        return {
            **probe,
            "esp_path": ctx.settings.path,
            "allowed_devices": list(addresses),
            "require_token": ctx.settings.require_token,
            "token_configured": bool(ctx.settings.token_secret),
            "settings_path": str(ctx.env_path) if ctx.env_path else "",
        }

    @application.post("/api/esp/settings")
    def esp_settings_update(patch: EspSettingsPatch, request: Request):
        values = patch.model_dump(exclude_none=True)
        if "path" in values:
            path = str(values["path"]).strip()
            if not path.startswith("/") or ".." in path or len(path) > 64:
                raise HTTPException(status_code=422, detail="invalid_esp_path")
            values["path"] = path
        if "vad_provider" in values and values["vad_provider"] not in {"energy"}:
            # Accepting it would silently run a different detector than requested.
            raise HTTPException(status_code=422, detail="vad_provider_not_implemented")
        if "standby_notice" in values:
            # The value goes into a KEY=VALUE line: a newline would corrupt .env
            # and silence the notice at the same time.
            values["standby_notice"] = " ".join(str(values["standby_notice"]).split())
        if "wake_words" in values:
            values["wake_words"] = [
                " ".join(str(word).split()) for word in (values["wake_words"] or []) if str(word).strip()
            ]
        if "default_user_id" in values:
            with ctx.db() as conn:
                ctx.admin_auth(request, conn)
                row = conn.execute("SELECT status FROM users WHERE user_id=?", (values["default_user_id"],)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="user_not_found")
            if row["status"] != "active":
                raise HTTPException(status_code=409, detail="user_disabled")
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
        if not values:
            return {"ok": True, "settings": ctx.settings.as_dict(), "restart_required": False}

        env_updates = {
            settings_store.EDITABLE_KEYS[name]: settings_store.to_env_value(value)
            for name, value in values.items()
            if name in settings_store.EDITABLE_KEYS
        }
        env_path = ctx.env_path or (ROOT / ".env")
        try:
            settings_store.update_env_file(Path(env_path), env_updates)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"env_write_failed:{exc}") from exc

        settings_store.export_env(env_updates)
        ctx.settings = EspSettings.from_env(settings_store.effective_env(Path(env_path)))
        ctx.vad_status = create_vad(ctx.settings)[1]
        # Keep the pending-enrollment sample count in step with the settings the operator just
        # saved; an enrollment already in flight keeps the count it started with.
        if ctx.enrollment is not None:
            ctx.enrollment.required_samples = max(1, int(ctx.settings.enroll_samples or 3))
        with ctx.db() as conn:
            ctx.record_audit(conn, actor_id, "esp_settings_update", target_type="system", target_id="esp", metadata={"changed": sorted(values)})
        return {"ok": True, "settings": ctx.settings.as_dict(), "restart_required": False}

    @application.get("/api/esp/ota-requests")
    def esp_ota_requests(request: Request, limit: int = 50):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
            return store.list_ota_requests(conn, limit)

    @application.get("/api/esp/firmware")
    def esp_firmware_list(request: Request):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
        return {"bin_dir": str(ctx.settings.ota_bin_dir), "items": ota.list_firmware(ctx.settings.ota_bin_dir)}

    @application.post("/api/esp/firmware", status_code=201)
    async def esp_firmware_upload(request: Request, file: UploadFile = File(...)):
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
        filename = Path(file.filename or "").name
        if not ota.SAFE_FILENAME.match(filename) or not ota.FIRMWARE_PATTERN.match(filename):
            raise HTTPException(status_code=422, detail="firmware_name_must_be_model_version.bin")
        payload = await file.read()
        if not payload:
            raise HTTPException(status_code=422, detail="empty_file")
        if len(payload) > MAX_FIRMWARE_BYTES:
            raise HTTPException(status_code=413, detail="firmware_too_large")
        ctx.settings.ota_bin_dir.mkdir(parents=True, exist_ok=True)
        target = ctx.settings.ota_bin_dir / filename
        target.write_bytes(payload)
        with ctx.db() as conn:
            ctx.record_audit(conn, actor_id, "esp_firmware_upload", target_type="firmware", target_id=filename, metadata={"size": len(payload)})
        return {"ok": True, "filename": filename, "size": len(payload)}

    @application.delete("/api/esp/firmware/{filename}")
    def esp_firmware_delete(filename: str, request: Request):
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
        resolved = ota.resolve_firmware_path(ctx.settings.ota_bin_dir, Path(filename).name)
        if resolved is None:
            raise HTTPException(status_code=404, detail="firmware_not_found")
        resolved.unlink()
        with ctx.db() as conn:
            ctx.record_audit(conn, actor_id, "esp_firmware_delete", target_type="firmware", target_id=resolved.name)
        return {"ok": True}

    # ----------------------------------------------------------- admin: devices
    def _device_detail(conn, device_id: str) -> dict[str, Any]:
        device = store.get_device(conn, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="device_not_found")
        live = ctx.live.get(device_id)
        toolset = ctx.tools.get(device_id)
        return {
            **device,
            "status": _device_status(device.get("last_seen")),
            "live": live.as_dict() if live else None,
            "connected": live is not None,
            "command_attached": ctx.commands.connected(device_id),
            # What the firmware said it can do, so "语音控制音量" has a visible
            # answer: an empty list here means the device declared no tools.
            "tools": toolset.snapshot() if toolset is not None else [],
            "tools_status": ctx.tools.status(device_id),
        }

    def _device_status(last_seen: float | None) -> str:
        if not last_seen:
            return "offline"
        age = max(0.0, time.time() - float(last_seen))
        if age < 60:
            return "online"
        if age <= 300:
            return "stale"
        return "offline"

    @application.get("/api/devices/{device_id}")
    def device_detail(device_id: str, request: Request):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
            return _device_detail(conn, device_id)

    @application.get("/api/devices/{device_id}/sessions")
    def device_sessions(device_id: str, request: Request, limit: int = 50):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
            if store.get_device(conn, device_id) is None:
                raise HTTPException(status_code=404, detail="device_not_found")
            return store.list_sessions(conn, device_id, limit)

    @application.get("/api/devices/{device_id}/commands")
    def device_commands(device_id: str, request: Request, limit: int = 50):
        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
            if store.get_device(conn, device_id) is None:
                raise HTTPException(status_code=404, detail="device_not_found")
            return store.list_commands(conn, device_id, limit)

    @application.post("/api/devices/{device_id}/commands", status_code=201)
    def device_command(device_id: str, payload: CommandRequest, request: Request):
        if payload.type not in COMMAND_TYPES:
            raise HTTPException(status_code=422, detail=f"unsupported_command:{payload.type}")
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
            if store.get_device(conn, device_id) is None:
                raise HTTPException(status_code=404, detail="device_not_found")
        command_id = str(uuid.uuid4())
        delivered = ctx.commands.push(device_id, {"command_id": command_id, "type": payload.type, "payload": payload.payload})
        status = "queued" if delivered else "offline"
        with ctx.db() as conn:
            store.create_command(
                conn,
                command_id=command_id,
                device_id=device_id,
                command_type=payload.type,
                payload=payload.payload,
                actor_id=actor_id,
                status=status,
                reason="" if delivered else "device_not_connected",
            )
            ctx.record_audit(conn, actor_id, "esp_device_command", target_type="device", target_id=device_id, metadata={"type": payload.type, "delivered": delivered})
        return {"ok": True, "command_id": command_id, "status": status, "delivered": delivered}

    @application.post("/api/devices/{device_id}/enroll-voiceprint", status_code=201)
    def device_enroll_voiceprint(device_id: str, payload: BindRequest, request: Request):
        """Start enrolling this device's *own* microphone as (or for) ``payload.user_id``.

        The board then asks for three sentences and stores the samples itself, and the finished
        templates are built from those recordings -- which is what makes verification work at all:
        measured 2026-10-06, browser-enrolled templates scored 0.38-0.44 against the same person on
        an ESP32 (threshold 0.55), because the microphone is part of the embedding.
        """
        from services.device_gateway.xiaozhi import enrollment

        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
            if store.get_device(conn, device_id) is None:
                raise HTTPException(status_code=404, detail="device_not_found")
            user = conn.execute("SELECT status, enrollment_language FROM users WHERE user_id=?", (payload.user_id,)).fetchone()
            if user is None:
                raise HTTPException(status_code=404, detail="user_not_found")
            if user["status"] != "active":
                raise HTTPException(status_code=409, detail="user_disabled")
            ctx.record_audit(
                conn, actor_id, "esp_voiceprint_enrollment_started",
                target_type="device", target_id=device_id, metadata={"user_id": payload.user_id},
            )
            language = str(user["enrollment_language"] or ctx.user_language(payload.user_id) or "zh-CN")

        item = ctx.enrollment.start(device_id, payload.user_id, language)
        # Speak the first sentence right away when the device is listening: the operator should not
        # have to read the prompts off the dashboard while standing next to the board.
        prompts = enrollment.prompts_for(language)
        ctx.commands.push(device_id, {
            "command_id": f"enroll-{enrollment.next_request_id()[:8]}",
            "type": "enroll_start",
            "payload": {"text": f"我要記住你的聲音。請說：{prompts[0]}" if prompts else "我要記住你的聲音。"},
        })
        body = item.as_dict(prompts)
        body["ok"] = True
        body["connected"] = ctx.commands.connected(device_id)
        return body

    @application.get("/api/devices/{device_id}/enroll-voiceprint")
    def device_enroll_voiceprint_status(device_id: str, request: Request):
        from services.device_gateway.xiaozhi import enrollment, store

        with ctx.db() as conn:
            ctx.admin_auth(request, conn)
            # The pending state is in memory and disappears the moment the enrollment finishes, so
            # the panel went back to 「未开始」 and a *successful* enrollment was indistinguishable
            # from nothing having happened (2026-10-06: five samples stored, five templates built,
            # reported by the operator as a failure). The persisted outcome is reported alongside.
            events = store.latest_device_event(
                conn, device_id, ("voiceprint_enrollment", "voiceprint_enrolled"), limit=6
            )
            templates = conn.execute(
                "SELECT user_id, COUNT(*) AS n FROM voiceprint_templates WHERE active=1 GROUP BY user_id"
            ).fetchall()
        active_by_user = {str(row["user_id"]): int(row["n"]) for row in templates}
        last = _enrollment_summary(events, active_by_user)
        item = ctx.enrollment.get(device_id)
        if item is None:
            return {
                "device_id": device_id,
                "state": "idle",
                "prompts": [],
                "connected": ctx.commands.connected(device_id),
                "last": last,
            }
        body = item.as_dict(enrollment.prompts_for(item.language))
        body["connected"] = ctx.commands.connected(device_id)
        body["last"] = last
        return body

    @application.delete("/api/devices/{device_id}/enroll-voiceprint")
    def device_enroll_voiceprint_cancel(device_id: str, request: Request):
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
            ctx.record_audit(
                conn, actor_id, "esp_voiceprint_enrollment_canceled",
                target_type="device", target_id=device_id,
            )
        return {"ok": ctx.enrollment.cancel(device_id)}

    @application.post("/api/devices/{device_id}/bind")
    def device_bind(device_id: str, payload: BindRequest, request: Request):
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
            if store.get_device(conn, device_id) is None:
                raise HTTPException(status_code=404, detail="device_not_found")
            user = conn.execute("SELECT status FROM users WHERE user_id=?", (payload.user_id,)).fetchone()
            if user is None:
                raise HTTPException(status_code=404, detail="user_not_found")
            if user["status"] != "active":
                raise HTTPException(status_code=409, detail="user_disabled")
            store.bind_device_user(conn, device_id, payload.user_id)
            ctx.record_audit(conn, actor_id, "esp_device_bind", target_type="device", target_id=device_id, metadata={"user_id": payload.user_id})
        live = ctx.live.get(device_id)
        if live is not None:
            live.user_id = payload.user_id
            ctx.live.put(live)
        return {"ok": True, "device_id": device_id, "user_id": payload.user_id, "note": "重新连接后生效"}

    @application.delete("/api/devices/{device_id}/bind")
    def device_unbind(device_id: str, request: Request):
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
            if not store.bind_device_user(conn, device_id, None):
                raise HTTPException(status_code=404, detail="device_not_found")
            ctx.record_audit(conn, actor_id, "esp_device_unbind", target_type="device", target_id=device_id)
        return {"ok": True}

    @application.get("/api/devices/{device_id}/token")
    def device_token_view(device_id: str, request: Request):
        with ctx.db() as conn:
            actor_id = ctx.admin_auth(request, conn, write=True)
            if store.get_device(conn, device_id) is None:
                raise HTTPException(status_code=404, detail="device_not_found")
            if not ctx.settings.token_secret:
                raise HTTPException(status_code=409, detail="token_secret_not_configured")
            ctx.record_audit(conn, actor_id, "esp_device_token_view", target_type="device", target_id=device_id)
        token = device_token(device_id, ctx.settings.token_secret)
        return {"device_id": device_id, "token": token, "header": f"Authorization: Bearer {token}"}

    # ----------------------------------------------------------- admin: observe
    @application.websocket("/ws/device-observe")
    async def device_observe(websocket: WebSocket):
        try:
            with ctx.db() as conn:
                row = _session(websocket, conn)
                if row["role"] != "admin":
                    raise HTTPException(status_code=403, detail="admin_required")
        except HTTPException:
            await websocket.close(code=4401)
            return
        queue = await ctx.observer.subscribe()
        if queue is None:
            await websocket.close(code=4429)
            return
        await websocket.accept()
        try:
            await websocket.send_json({"type": "observer.ready", "live": [device.as_dict() for device in ctx.live.all()]})
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - a broken observer must not affect devices
            return
        finally:
            ctx.observer.unsubscribe(queue)
