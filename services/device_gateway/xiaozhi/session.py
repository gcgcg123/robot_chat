"""One ESP32 device session: handshake, audio in, audio out.

The session is the only place that knows both protocols at once -- xiaozhi's
wire format on one side and this project's dialogue pipeline on the other.  It
owns no business logic of its own: identity, memory recall, emotion/risk
analysis and the LLM call all belong to ``services.dialogue`` and are injected
through :class:`GatewayContext`.

Turn lifecycle
--------------
    hello            -> welcome (audio_params the device must honour)
    listen start     -> reset buffers, state=listening
    binary frames    -> Opus decode -> PCM buffer -> energy VAD
    VAD end / stop   -> WAV -> ASR worker -> stt -> pipeline -> llm -> TTS -> Opus frames
    abort            -> stop playback, cancel the turn

Barge-in is deliberate: a new utterance (or ``listen start``) cancels the turn
that is still speaking, exactly like the upstream server's ``reset_audio_states``.

Speech is *streamed*: the model's deltas are cut into sentences and handed to
:class:`~services.device_gateway.xiaozhi.playback.TtsPlayer` as they arrive, so
the device starts talking after the first sentence instead of after the whole
answer.  Ending playback is this class's job, not the player's -- ``tts:stop`` is
the only message that moves the firmware out of its speaking state, and it has to
be ordered against the turn's own bookkeeping.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable

from services.audio.normalize import normalize_audio
from services.device_gateway.contracts import DeviceEvent as ProtocolEvent
from services.dialogue.deepseek import DeepSeekClient
from services.tts.segments import SentenceStreamer, split_speech
from services.voiceprint.matcher import IdentityResult
from services.voiceprint.matcher import identify as identify_voiceprint
from services.device_gateway.xiaozhi import pcm, protocol, voicecmd
from services.device_gateway.xiaozhi import dialogctl
from services.device_gateway.xiaozhi.auth import authorize_device
from services.device_gateway.xiaozhi.config import EspSettings
from services.device_gateway.xiaozhi.context import GatewayContext, TurnRequest
from services.device_gateway.xiaozhi.mcp import DeviceMcpClient
from services.device_gateway.xiaozhi.opus_codec import OpusDecoder, OpusEncoder, OpusUnavailable
from services.device_gateway.xiaozhi.playback import TtsPlayer
from services.device_gateway.xiaozhi.registry import LiveDevice
from services.device_gateway.xiaozhi.vad import create_vad
from services.device_gateway.xiaozhi import store

STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_IDLE = "idle"
STATE_ERROR = "error"


class XiaozhiSession:
    """Drives exactly one connected device for the lifetime of its socket."""

    def __init__(
        self,
        ctx: GatewayContext,
        websocket: Any,
        *,
        device_id: str,
        client_id: str = "",
        client_ip: str = "",
        authorization: str = "",
        board_model: str = "",
        firmware: str = "",
    ) -> None:
        self.ctx = ctx
        self.settings: EspSettings = ctx.settings
        self.websocket = websocket
        self.device_id = device_id
        self.client_id = client_id
        self.client_ip = client_ip
        self.board_model = board_model
        self.firmware = firmware
        self.session_id = uuid.uuid4().hex
        self.turn_id = ""
        self.user_id = ""
        self.identity_verified = False
        # Who the *speaker* was, resolved per utterance from the voiceprint
        # templates.  ``user_id``/``identity_verified`` start from the device
        # binding and are overwritten when a template matches, which is what
        # makes one account usable from any number of devices.
        self._speaker_template_id: str | None = None
        self._speaker_decision: str = ""
        self._speaker_score: float | None = None
        #: True only after a voiceprint match -- distinct from
        #: ``identity_verified``, which is about the device token, not the person.
        self._speaker_verified = False

        self._authorization = authorization
        self._send_lock = asyncio.Lock()
        self._buffer = bytearray()
        self._decoder: OpusDecoder | None = None
        self._encoder: OpusEncoder | None = None
        self._vad = None
        self._vad_status: dict = {}
        self._state = STATE_IDLE
        self._greeted = False
        self._closed = False
        self._turn_task: asyncio.Task | None = None
        self._command_task: asyncio.Task | None = None
        self._last_activity = time.time()
        self._turns = 0
        self._last_error = ""
        self._descriptors: dict[str, dict] = {}
        self._connected_at = time.time()
        self._audio_format = "opus"

        # ---- standby clock -------------------------------------------------
        # ``_last_activity`` is refreshed by *any* frame, and a listening ESP
        # streams silence continuously, so it can only serve as a dead-socket
        # guard.  ``_last_voice_at`` is what "没说话" really means, and
        # ``_listened`` keeps a device that was never woken out of standby.
        self._last_voice_at = time.time()
        self._listened = False

        # ---- streaming speech ----------------------------------------------
        #: The player currently owning the speaker, so an interrupt can reach it
        #: without threading a reference through every call.
        self._player: TtsPlayer | None = None
        #: The firmware's own wake-word beep lands in the microphone right after
        #: ``listen detect``; ignoring end-of-utterance for a moment keeps it
        #: from being transcribed as the user's opening word.
        self._vad_hold_until = 0.0

        # ---- device tools (MCP / iot) --------------------------------------
        self._loop: asyncio.AbstractEventLoop | None = None
        self._features: dict[str, bool] = {}
        self._mcp = DeviceMcpClient(
            self._send_json,
            timeout=float(getattr(ctx.settings, "tool_timeout_seconds", 12.0) or 12.0),
        )
        self._tools_task: asyncio.Task | None = None
        self._tools_status: dict[str, Any] = {}

    @property
    def _toolset(self):
        return self._mcp.tools

    # ------------------------------------------------------------------ outbound

    async def _send_json(self, message: dict) -> None:
        async with self._send_lock:
            await self.websocket.send_text(protocol.dumps(message))

    async def _send_bytes(self, payload: bytes) -> None:
        async with self._send_lock:
            await self.websocket.send_bytes(payload)

    async def _publish(self, event_type: str, payload: dict[str, Any], turn_id: str = "") -> None:
        """Mirror a device event onto the dashboard observer channel."""
        try:
            event = ProtocolEvent(self.device_id, self.session_id, turn_id or self.turn_id, event_type, payload)
        except ValueError:
            return
        self._apply_live(event_type, payload)
        await self.ctx.observer.publish(event.as_dict())

    def _apply_live(self, event_type: str, payload: dict[str, Any]) -> None:
        live = self.ctx.live.get(self.device_id)
        if live is None:
            return
        live.last_event_at = time.time()
        if event_type == "turn.started":
            live.state = STATE_THINKING
            live.caption = "正在理解…"
            live.user_text = ""
            live.transcript = ""
            live.turn_id = self.turn_id
        elif event_type == "stt.final":
            live.user_text = str(payload.get("text", ""))
            live.caption = live.user_text
            live.state = STATE_THINKING
        elif event_type == "display.state":
            state = str(payload.get("state", "")) or live.state
            live.state = state
            live.caption = str(payload.get("caption", live.caption))
            if payload.get("emotion"):
                live.emotion = str(payload["emotion"])
        elif event_type == "tts.segment":
            text = str(payload.get("text", ""))
            live.transcript += text
            live.caption = text
            live.state = STATE_SPEAKING
            result = payload.get("result") or {}
            if result.get("emotion"):
                live.emotion = str(result["emotion"])
            risk = (result.get("risk") or {}).get("risk_level")
            if risk:
                live.risk_level = str(risk)
        elif event_type == "tts.end":
            live.state = STATE_IDLE
            live.caption = ""
        elif event_type in {"turn.interrupted", "turn.failed"}:
            live.state = STATE_IDLE
            live.caption = ""
            live.risk_level = ""
        self.ctx.live.put(live)

    async def _fail(self, reason: str, *, detail: str = "", close: bool = False, code: int = 1011) -> None:
        self._last_error = reason
        try:
            await self._send_json(protocol.error_message(reason, self.session_id))
        except Exception:  # noqa: BLE001 - socket already gone
            pass
        await self._publish("turn.failed", {"error": reason, "detail": detail}, self.turn_id)
        if close:
            await self._close(code)

    async def _close(self, code: int = 1000) -> None:
        self._closed = True
        try:
            await self.websocket.close(code=code)
        except Exception:  # noqa: BLE001 - already closed
            pass

    # ------------------------------------------------------------------ inbound

    async def run(self) -> None:
        await self.websocket.accept()
        status = self.ctx.opus
        if not status.get("available"):
            await self._fail("opus_unavailable", detail=str(status.get("reason", "")), close=True)
            return

        auth = authorize_device(self.settings, device_id=self.device_id, authorization=self._authorization)
        if not auth.allowed:
            await self._fail(auth.reason, close=True, code=4403)
            return
        self.identity_verified = auth.verified

        try:
            self._decoder = OpusDecoder(self.settings.uplink_sample_rate, self.settings.frame_duration_ms)
            self._encoder = OpusEncoder(self.settings.sample_rate, self.settings.frame_duration_ms)
        except OpusUnavailable as exc:  # pragma: no cover - guarded by the status check above
            await self._fail("opus_unavailable", detail=str(exc), close=True)
            return
        self._vad, self._vad_status = create_vad(self.settings)

        self._loop = asyncio.get_running_loop()
        self._resolve_user()
        self._register()
        self._command_task = asyncio.create_task(self._command_loop())

        try:
            while not self._closed:
                verdict = self._clock_verdict()
                if verdict == "standby":
                    await self._enter_standby()
                    break
                if verdict == "dead":
                    await self._fail("idle_timeout", close=True, code=1000)
                    break
                try:
                    payload = await asyncio.wait_for(
                        self.websocket.receive(), timeout=self._next_deadline_seconds()
                    )
                except asyncio.TimeoutError:
                    continue
                kind = payload.get("type", "")
                if kind == "websocket.disconnect":
                    break
                self._last_activity = time.time()
                data = payload.get("bytes")
                if data is not None:
                    # Audio frames say nothing about activity: a listening device
                    # streams silence forever, which is exactly why the old
                    # "any frame resets the idle timer" rule never expired.
                    await self._on_audio(data)
                    continue
                text = payload.get("text")
                if text is not None:
                    # A device-initiated message (listen / ping / iot) *is*
                    # activity and deserves to postpone standby.
                    self._last_voice_at = time.time()
                    await self._on_text(text)
        except Exception as exc:  # noqa: BLE001 - a broken socket must not leak a task
            self._last_error = f"session_error:{exc}"
        finally:
            await self._shutdown()

    def _clock_verdict(self) -> str:
        """``wait`` / ``standby`` / ``dead`` for the current moment.

        Two clocks, because they answer two different questions: has nobody
        *spoken* for a while (the user is done and the device should go back to
        待机), and has the socket gone completely silent (it is gone).
        """
        now = time.time()
        if now - self._last_activity >= self.settings.idle_timeout_seconds:
            return "dead"
        if self._turn_task is not None and not self._turn_task.done():
            return "wait"
        if not self._listened:
            # Never woken up: the device is in wake-word mode and cannot be in
            # listening, so there is nothing to stand down from.
            return "wait"
        if now - self._last_voice_at >= self.settings.standby_seconds:
            return "standby"
        return "wait"

    def _next_deadline_seconds(self) -> float:
        now = time.time()
        voice_left = self.settings.standby_seconds - (now - self._last_voice_at)
        link_left = self.settings.idle_timeout_seconds - (now - self._last_activity)
        return max(1.0, min(voice_left, link_left))

    async def _enter_standby(self, reason: str = "no_voice", *, turn_id: str = "") -> None:
        """End the listening phase so the device returns to 待机.

        A device in listening mode has the microphone open and waits for the
        server to end the turn; the firmware ignores ``listen`` messages from the
        server, so the only lever that actually moves it back to standby is
        closing the channel -- at which point it re-arms wake-word detection
        (``OnAudioChannelClosed`` -> ``kDeviceStateIdle``).  That is also what the
        upstream server does, with a goodbye in front of it.

        ``reason`` is ``no_voice`` (the standby clock expired) or ``voice_end``
        (the user said "退下"); both end the session, but only the first should
        be recorded as silence.
        """
        self._last_error = f"standby_{reason}"
        if self.settings.standby_notice and self.settings.tts_enabled:
            try:
                await self._speak_local(
                    self.settings.standby_notice, turn_id or f"standby-{reason}", settle_seconds=1.0
                )
            except Exception:  # noqa: BLE001 - a farewell must never block standby
                pass
        self._set_state(STATE_IDLE, "已待机（等待唤醒词）")
        await self._publish(
            "device.standby",
            {"reason": reason, "seconds": self.settings.standby_seconds},
            turn_id,
        )
        try:
            with self.ctx.db() as conn:
                store.record_device_event(
                    conn,
                    self.device_id,
                    "standby",
                    {"reason": reason, "seconds": self.settings.standby_seconds, "turns": self._turns},
                )
        except Exception:  # noqa: BLE001
            pass
        await self._close(1000)

    async def _on_text(self, raw: str) -> None:
        try:
            body = protocol.parse_text(raw)
        except protocol.ProtocolError as exc:
            # Never fatal: a firmware probing an unsupported message should not
            # lose its session, but the operator must still see it happened.
            with self.ctx.db() as conn:
                store.record_device_event(conn, self.device_id, "protocol_error", {"reason": str(exc), "raw": raw[:200]})
            await self._send_json(protocol.error_message(str(exc), self.session_id))
            return

        message_type = str(body.get("type", ""))
        if message_type == "hello":
            await self._on_hello(body)
            return
        if not self._greeted:
            # Lenient interop: some firmware revisions start streaming without a
            # hello (or send hello again after a reconnect).
            await self._send_welcome()
        if message_type == "listen":
            await self._on_listen(body)
        elif message_type == "abort":
            await self._on_abort()
        elif message_type == "ping":
            await self._send_json(protocol.pong_message(self.session_id))
        elif message_type == "iot":
            self._on_iot(body)
        elif message_type == "mcp":
            await self._on_mcp(body)
        # "server" is accepted and ignored on purpose: this project does not
        # expose server-side tools over the device channel.

    async def _on_hello(self, body: dict) -> None:
        audio_params = body.get("audio_params") or {}
        requested_format = str(audio_params.get("format", "opus")).strip().lower()
        if requested_format and requested_format != "opus":
            await self._fail(f"unsupported_audio_format:{requested_format}", close=True, code=4400)
            return
        version = body.get("version")
        try:
            self._protocol_version = int(version) if version is not None else protocol.PROTOCOL_VERSION
        except (TypeError, ValueError):
            self._protocol_version = protocol.PROTOCOL_VERSION
        if self._protocol_version != protocol.PROTOCOL_VERSION:
            await self._fail(f"unsupported_protocol_version:{self._protocol_version}", close=True, code=4400)
            return
        features = body.get("features") or {}
        if isinstance(features, dict):
            self._features = {str(key): bool(value) for key, value in features.items()}
        self._audio_format = requested_format or "opus"
        with self.ctx.db() as conn:
            store.record_device_event(
                conn,
                self.device_id,
                "esp_hello",
                {
                    "features": self._features,
                    "audio_params": audio_params if isinstance(audio_params, dict) else {},
                    "version": self._protocol_version,
                },
            )
        await self._send_welcome()

    async def _send_welcome(self) -> None:
        self._greeted = True
        await self._send_json(
            protocol.welcome_message(
                self.session_id,
                sample_rate=self.settings.sample_rate,
                frame_duration_ms=self.settings.frame_duration_ms,
                audio_format="opus",
            )
        )
        with self.ctx.db() as conn:
            store.set_device_state(conn, self.device_id, "connected")
        await self._publish("display.state", {"state": STATE_IDLE, "caption": "设备已连接"})
        if self.settings.tools_enabled and self._tools_task is None:
            # Discovery is a background task: a firmware that never answers MCP
            # must delay a conversation by exactly nothing.
            self._tools_task = asyncio.create_task(self._start_tools())

    async def _start_tools(self) -> None:
        """Ask the device which tools it exposes (volume, screen, ...)."""
        try:
            status = await self._mcp.start()
        except Exception as exc:  # noqa: BLE001 - never fail a session over tools
            status = {"available": False, "ready": False, "tools": 0, "reason": f"error:{type(exc).__name__}", "names": []}
        self._tools_status = status
        try:
            with self.ctx.db() as conn:
                store.record_device_event(
                    conn,
                    self.device_id,
                    "device_tools",
                    {
                        "available": status.get("available"),
                        "ready": status.get("ready"),
                        "tools": status.get("tools"),
                        "reason": status.get("reason"),
                        "names": status.get("names"),
                        "mcp_feature": bool(self._features.get("mcp")),
                    },
                )
        except Exception:  # noqa: BLE001
            pass
        self._register_tools()
        await self._publish(
            "device.tools",
            {
                "available": status.get("available"),
                "ready": status.get("ready"),
                "count": status.get("tools"),
                "reason": status.get("reason"),
                "tools": self._toolset.snapshot(),
            },
        )

    async def _on_listen(self, body: dict) -> None:
        try:
            state, _mode, text = protocol.parse_listen(body)
        except protocol.ProtocolError as exc:
            await self._send_json(protocol.error_message(str(exc), self.session_id))
            return

        if state == "start":
            self._listened = True
            self._reset_audio()
            # Deliberately *no* interrupt here.  The firmware answers every
            # ``tts:stop`` by re-entering listening and sending this message, so
            # treating it as a barge-in made the server cancel its own turn while
            # it was still winding down.  The upstream server does the same
            # (``listenMessageHandler`` only resets audio state); a real barge-in
            # arrives as ``abort``, which the firmware sends from
            # ``AbortSpeaking`` -- including the wake-word path.
            self._set_state(STATE_LISTENING, "正在聆听")
            return

        if state == "stop":
            # Do NOT reset here: _flush_utterance reads the buffered audio and
            # resets afterwards.  Clearing first would discard the utterance.
            self._listened = True
            await self._flush_utterance("flush")
            return

        # state == "detect": the device recognised a wake word / command word
        # itself and ships the text instead of audio.
        if text:
            if self._is_wake_word(text):
                # The firmware sends its own wake word here ("小智").  It is the
                # doorbell, not something to answer: feeding it to the model used
                # to produce a reply to the word "小智" and then a whole TTS round
                # before the user had said anything.  Opening the channel is
                # enough -- the user's real sentence arrives as audio.
                self._listened = True
                self._vad_hold_until = time.time() + self.settings.wake_word_hold_seconds
                self._set_state(STATE_LISTENING, "正在聆听")
                await self._publish("device.wake_word", {"text": text}, "")
                return
            await self._start_text_turn(text)

    async def _on_abort(self) -> None:
        await self._interrupt("device_abort")

    def _is_wake_word(self, text: str) -> bool:
        candidate = "".join(text.split()).strip()
        for word in self.settings.wake_words:
            normalised = "".join(str(word).split())
            if normalised and normalised in candidate and len(candidate) <= max(6, len(normalised) + 2):
                return True
        return False

    def _on_iot(self, body: dict) -> None:
        """Register declared IoT methods as tools (the pre-MCP protocol).

        The firmware's shape is a *list* of descriptor objects
        (``{"name","description","properties","methods"}``); the mapping shape is
        kept working because the first revision of this layer assumed it.
        """
        descriptors = body.get("descriptors")
        if descriptors is not None:
            added = self._mcp.add_iot_descriptors(descriptors)
            if isinstance(descriptors, dict):
                for name, spec in descriptors.items():
                    if isinstance(spec, dict):
                        self._descriptors[str(name)] = spec
            elif isinstance(descriptors, list):
                for item in descriptors:
                    if isinstance(item, dict) and item.get("name"):
                        self._descriptors[str(item["name"])] = item
            with self.ctx.db() as conn:
                store.record_device_event(
                    conn, self.device_id, "iot_descriptors", {"added": added, "total": len(self._toolset)}
                )
            self._register_tools()
            self._publish_tools_soon()
        states = body.get("states")
        if states is not None:
            with self.ctx.db() as conn:
                store.record_device_event(conn, self.device_id, "iot_states", {"states": states})

    async def _on_mcp(self, body: dict) -> None:
        """One JSON-RPC payload from the device (handshake result or tool reply)."""
        payload = body.get("payload")
        before = len(self._toolset)
        await self._mcp.handle(payload)
        if len(self._toolset) != before or self._mcp.ready:
            self._register_tools()
            self._tools_status = {**self._tools_status, "available": self._mcp.available, "ready": self._mcp.ready, "tools": len(self._toolset)}
            self._publish_tools_soon()

    def _register_tools(self) -> None:
        try:
            self.ctx.tools.put(
                self.device_id,
                self._toolset,
                {
                    "available": self._mcp.available,
                    "ready": self._mcp.ready,
                    "reason": self._mcp.reason,
                    "count": len(self._toolset),
                    "names": [tool.name for tool in self._toolset.all()],
                },
            )
        except Exception:  # noqa: BLE001 - observability must never break a session
            pass

    def _publish_tools_soon(self) -> None:
        """Announce the tool set without awaiting inside a receive handler."""
        if self._loop is None:
            return
        snapshot = self._toolset.snapshot()
        status = self._tools_status
        self._loop.create_task(
            self._publish(
                "device.tools",
                {
                    "available": self._mcp.available,
                    "ready": self._mcp.ready,
                    "count": len(snapshot),
                    "reason": self._mcp.reason,
                    "tools": snapshot,
                    "status": status,
                },
            )
        )

    # ------------------------------------------------------------------ audio in

    async def _on_audio(self, packet: bytes) -> None:
        if not self._greeted:
            await self._send_welcome()
        if self._state != STATE_LISTENING:
            # Streaming without an explicit listen start is treated as listening;
            # refusing the frames would silently lose the user's speech.
            self._set_state(STATE_LISTENING, "正在聆听")
        self._listened = True
        try:
            frame = self._decoder.decode(packet)
        except ValueError as exc:
            with self.ctx.db() as conn:
                store.record_device_event(conn, self.device_id, "audio_error", {"reason": str(exc)})
            return
        if not frame:
            return
        self._buffer.extend(frame)
        decision = self._vad.feed(frame)
        if decision.speaking or decision.triggered:
            # Only real speech moves the standby clock.  Silence frames keep
            # arriving for as long as the device is listening, and counting them
            # would be counting the pause the standby exists to detect.
            self._last_voice_at = time.time()
        if decision.triggered:
            self._set_state(STATE_LISTENING, "正在聆听")
        if decision.ended and time.time() >= self._vad_hold_until:
            # The firmware's own wake-word chime is still ringing right after
            # ``listen detect``; ending the utterance on it would send the user's
            # first word to ASR on its own, or nothing at all.
            await self._flush_utterance(decision.reason)

    def _reset_audio(self) -> None:
        self._buffer = bytearray()
        if self._vad is not None:
            self._vad.reset()

    async def _flush_utterance(self, reason: str) -> None:
        data = bytes(self._buffer)
        self._reset_audio()
        if len(data) < self.settings.min_utterance_bytes:
            # Nothing was actually said (a stray ``listen stop``, a cough).  The
            # state has to fall back to idle here, otherwise the dashboard keeps
            # reporting 正在聆听 for a device that is doing nothing at all.
            self._set_state(STATE_IDLE, "")
            return
        await self._interrupt("barge_in")
        self._turn_task = asyncio.create_task(self._run_turn(pcm16=data, reason=reason))

    async def _start_text_turn(self, text: str) -> None:
        await self._interrupt("barge_in")
        self._turn_task = asyncio.create_task(self._run_turn(text=text, reason="device_text"))

    async def _interrupt(self, reason: str) -> None:
        """Stop speaking and abandon whatever turn is in flight.

        ``tts:stop`` goes out first and unconditionally: the firmware's
        ``OnIncomingJson`` handles ``tts``/``stt``/``llm``/``mcp`` but has **no**
        ``listen`` handler, so this message is the only thing that moves it out
        of ``kDeviceStateSpeaking`` (back to listening, or to idle in manual
        mode).  Without it a barge-in left the device talking over the user
        until it ran out of already-buffered audio.
        """
        task, player = self._turn_task, self._player
        active = task is not None and not task.done()
        if not active and player is None:
            return
        self._player = None
        if self.turn_id:
            self.ctx.turn_registry.cancel(self.turn_id)
        await self._stop_speech()
        if player is not None:
            await player.abort()
        if active:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - an aborted turn reports, never raises
                pass
        self._turn_task = None
        self._reset_audio()
        if self.turn_id:
            await self._publish("turn.interrupted", {"reason": reason}, self.turn_id)
        self.turn_id = ""
        self._set_state(STATE_IDLE, "")

    async def _stop_speech(self) -> None:
        """Tell the device to leave its speaking state.

        A device that is not speaking ignores this, so it is safe to send on any
        path that might have been mid-playback.
        """
        try:
            await self._send_json(protocol.tts_message("stop", self.session_id))
        except Exception:  # noqa: BLE001 - the socket may already be gone
            pass

    def _new_player(self, turn_id: str, *, payload: dict | None = None, source: str = "") -> TtsPlayer:
        """Build and start the streaming player for one turn."""
        player = TtsPlayer(
            turn_id=turn_id,
            session_id=self.session_id,
            provider=self.ctx.tts_provider,
            encoder=self._encoder,
            resample=pcm.resample,
            send_json=self._send_json,
            send_bytes=self._send_bytes,
            publish=self._publish,
            set_state=self._set_state,
            is_cancelled=lambda: self.ctx.turn_registry.is_cancelled(turn_id),
            language=self._language,
            sample_rate=self.settings.sample_rate,
            frame_duration_ms=self.settings.frame_duration_ms,
            prebuffer_frames=self.settings.playback_buffer_frames,
            result_payload=payload or {},
            segment_extra={"source": source} if source else None,
            speaking_state=STATE_SPEAKING,
            audio_enabled=bool(self.settings.tts_enabled),
        )
        self._player = player
        return player.start()

    # ------------------------------------------------------------------ one turn

    async def _run_turn(self, *, pcm16: bytes | None = None, text: str | None = None, reason: str = "") -> None:
        request_id = str(uuid.uuid4())
        try:
            self.turn_id = self.ctx.turn_registry.begin(self.session_id, request_id)
        except RuntimeError:
            self._turn_id_fallback(request_id)
        if not self.turn_id:
            return
        turn_id = self.turn_id
        try:
            await self._publish("turn.started", {"request_id": request_id, "reason": reason}, turn_id)
            self._set_state(STATE_THINKING, "正在理解…")

            if text is None:
                text = await self._transcribe(pcm16 or b"")
            text = (text or "").strip()
            if not text:
                await self._publish("turn.failed", {"error": "empty_transcript"}, turn_id)
                self._set_state(STATE_IDLE, "")
                return

            # Who spoke is decided by the voice, not by which device it came in
            # on: that is what lets one registered user talk to any of them.
            await self._identify_speaker(pcm16 or b"", turn_id)

            await self._send_json(protocol.stt_message(text, self.session_id))
            await self._publish("stt.final", {"text": text}, turn_id)

            # Talking to the *conversation itself* beats everything else: "停"
            # and "退下" have to be obeyed even when the model call would have
            # failed, and neither should cost a model round trip.
            control = dialogctl.classify(text)
            if control == "stop":
                await self._publish("turn.local", {"kind": "stop", "text": text}, turn_id)
                await self._speak_local(dialogctl.acknowledgement("stop", self._language()), turn_id)
                return
            if control == "end":
                await self._publish("turn.local", {"kind": "end", "text": text}, turn_id)
                await self._leave_conversation(turn_id)
                return

            # A device-setting instruction ("把音量调到 60") is served by the
            # device itself.  Running it through the rules first keeps that
            # control working even when the model call is unavailable, and the
            # model path below still handles everything the rules abstain from.
            local = await self._maybe_device_command(text, turn_id)
            if local is not None:
                # A canned/local answer carries no emoji of its own, so the face
                # comes from the verdict on this turn's risk level.
                await self._send_json(
                    protocol.face_message(
                        self.session_id,
                        fallback_emotion=local["emotion"],
                        risk_level=local.get("risk_level", ""),
                    )
                )
                await self._speak(local, turn_id)
                return

            await self._stream_answer(text, turn_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced, never swallowed
            self._last_error = str(exc)
            await self._publish("turn.failed", {"error": "turn_error", "detail": str(exc)}, turn_id)
            self._set_state(STATE_ERROR, "处理失败")
        finally:
            if self.turn_id == turn_id:
                self.ctx.turn_registry.complete(turn_id)
                self.turn_id = ""
                if self._state in {STATE_THINKING, STATE_SPEAKING}:
                    self._set_state(STATE_IDLE, "")
            # A finished turn is conversation: give the user a full pause again
            # instead of standing the device down the moment the reply ends.
            self._last_voice_at = time.time()

    def _turn_id_fallback(self, request_id: str) -> None:
        """Recover when the registry still holds a stale turn for this session."""
        if self.turn_id:
            self.ctx.turn_registry.cancel(self.turn_id)
        try:
            self.turn_id = self.ctx.turn_registry.begin(self.session_id, request_id)
        except RuntimeError:
            self.turn_id = ""

    async def _transcribe(self, pcm16: bytes) -> str:
        try:
            normalized = normalize_audio(pcm.to_wav(pcm16, self.settings.uplink_sample_rate), "audio/wav")
        except ValueError as exc:
            await self._publish("turn.failed", {"error": f"audio_invalid:{exc}"}, self.turn_id)
            return ""
        try:
            future = self.ctx.asr_worker.submit(normalized, self._language())
        except Exception as exc:  # noqa: BLE001 - queue full or worker gone
            await self._publish("turn.failed", {"error": "asr_unavailable", "detail": str(exc)}, self.turn_id)
            return ""
        try:
            result = await asyncio.wrap_future(future)
        except Exception as exc:  # noqa: BLE001 - model failure is a real answer
            await self._publish("turn.failed", {"error": "asr_failed", "detail": str(exc)}, self.turn_id)
            return ""
        if isinstance(result, dict):
            return str(result.get("text", ""))
        return str(result or "")

    def _language(self) -> str:
        try:
            return self.ctx.user_language(self.user_id)
        except Exception:  # noqa: BLE001 - a disabled/deleted account falls back
            return "zh-CN"

    async def _identify_speaker(self, pcm16: bytes, turn_id: str) -> None:
        """Attribute this utterance to an enrolled user, if anyone matches.

        Resolution order, highest first: matched voiceprint, the device's bound
        account, ``IOT_ESP_DEFAULT_USER``.  Binding therefore stays a fallback
        rather than a requirement -- a user recognised by voice is served their
        own memory and language on whatever device is in the room.

        Every failure here is a *normal* outcome (no templates enrolled, provider
        model missing, utterance too short) and none of them may fail the turn:
        the worst case is the identity we already had.
        """
        if not self.settings.voiceprint_enabled:
            return
        # Attribute every utterance on its own.  Clearing first means a failed
        # match can never silently inherit the previous speaker's identity.
        if self._speaker_verified:
            self._speaker_verified = False
            self._speaker_template_id = None
            self._resolve_user()
        if not pcm16:
            return
        provider = getattr(self.ctx, "voiceprint_provider", None)
        if provider is None:
            return
        model_version = str(getattr(provider, "model_version", "") or "")
        try:
            with self.ctx.db() as conn:
                templates = store.list_active_templates(conn, model_version)
        except Exception as exc:  # noqa: BLE001
            self._speaker_decision = f"templates_unavailable:{type(exc).__name__}"
            return
        if not templates:
            # Nobody has enrolled: skip the embedding entirely so a turn costs
            # nothing extra until the feature is actually usable.
            self._speaker_decision = "no_templates"
            return
        try:
            vector = await asyncio.to_thread(provider.embed, pcm16)
            result = await asyncio.to_thread(
                identify_voiceprint,
                vector,
                templates,
                threshold=None,
                min_margin=None,
                provider=model_version,
            )
        except Exception as exc:  # noqa: BLE001
            self._speaker_decision = f"embedding_failed:{type(exc).__name__}"
            return

        self._speaker_decision = result.decision
        self._speaker_score = result.best_score if result.best_score >= 0 else None
        if result.decision == "accepted" and result.user_id:
            self.user_id = str(result.user_id)
            self._speaker_verified = True
            self._speaker_template_id = result.template_id
        else:
            # A rejected match must not leave a previous speaker's identity in
            # place: fall back to the binding that ``_resolve_user`` chose.
            self._speaker_verified = False
            self._speaker_template_id = None
            self._resolve_user()
        with self.ctx.db() as conn:
            store.record_device_event(
                conn,
                self.device_id,
                "speaker_identified",
                {
                    "decision": result.decision,
                    "user_id": self.user_id,
                    "best_score": round(result.best_score, 6),
                    "template_id": result.template_id,
                    "model_version": model_version,
                },
            )
        # Keep the dashboard's live view on the person actually talking rather
        # than on whoever the device was bound to at handshake.
        live = self.ctx.live.get(self.device_id)
        if live is not None:
            live.user_id = self.user_id
            live.identity_verified = self._speaker_verified
            self.ctx.live.put(live)
        await self._publish(
            "speaker.identified",
            {
                "decision": result.decision,
                "user_id": self.user_id,
                "verified": self._speaker_verified,
                "score": round(self._speaker_score, 4) if self._speaker_score is not None else None,
            },
            turn_id,
        )

    async def _think(self, text: str, turn_id: str, *, on_delta: Callable[[str], None] | None = None) -> dict[str, Any] | None:
        """Run recall + ``process_text`` and persist the turn.

        ``on_delta`` -- when the caller can speak while the model is still
        writing -- is forwarded to the pipeline, which uses it only for a plain
        (tool-free) answer.  The callback runs on the worker thread, so anything
        it touches has to be thread-safe; the sentence streamer and the player's
        queue both are.
        """
        identity = self._identity()
        with self.ctx.db() as conn:
            recall = self.ctx.select_for_turn(
                conn,
                user_id=self.user_id,
                query=text,
                identity=identity,
                embedder=self.ctx.embedding_provider.embed_queries if self.ctx.embedding_provider.available else None,
                settings=self.ctx.memory_config,
            )
        llm = None if self.ctx.testing else (self.ctx.providers.get("deepseek") or DeepSeekClient())
        tools = self._llm_tools()
        extra: dict[str, Any] = {}
        if on_delta is not None and self.settings.llm_stream:
            extra["on_delta"] = on_delta
        try:
            result = await asyncio.to_thread(
                self.ctx.process_text,
                text,
                user_id=self.user_id,
                language=self._language(),
                identity=identity,
                session_id=self.session_id,
                turn_id=turn_id,
                llm=llm,
                rag_provider=self.ctx.providers.get("rag"),
                memories=recall.selected,
                request_id=str(uuid.uuid4()),
                memory_settings=self.ctx.memory_config,
                tools=tools or None,
                tool_runner=self._run_tool_sync if tools else None,
                **extra,
            )
        except TypeError as exc:
            # A test double or an older pipeline that does not take ``on_delta``
            # should cost the *streaming*, not the turn.
            if "on_delta" not in str(exc):
                await self._publish("turn.failed", {"error": "pipeline_failed", "detail": str(exc)}, turn_id)
                self._set_state(STATE_ERROR, "处理失败")
                return None
            print(f"[esp] pipeline rejected on_delta ({exc}); retrying without streaming", flush=True)
            result = await asyncio.to_thread(
                self.ctx.process_text,
                text,
                user_id=self.user_id,
                language=self._language(),
                identity=identity,
                session_id=self.session_id,
                turn_id=turn_id,
                llm=llm,
                rag_provider=self.ctx.providers.get("rag"),
                memories=recall.selected,
                request_id=str(uuid.uuid4()),
                memory_settings=self.ctx.memory_config,
                tools=tools or None,
                tool_runner=self._run_tool_sync if tools else None,
            )
        except Exception as exc:  # noqa: BLE001
            await self._publish("turn.failed", {"error": "pipeline_failed", "detail": str(exc)}, turn_id)
            self._set_state(STATE_ERROR, "处理失败")
            return None

        model = "testing-disabled" if self.ctx.testing else str(result.model.get("name") or result.model.get("status") or "none")
        request = TurnRequest(
            user_id=self.user_id,
            text=text,
            device_id=self.device_id,
            voiceprint_id=self._speaker_template_id if self._speaker_verified else None,
        )
        try:
            conversation_id = self.ctx.save_conversation(
                request,
                result.reply,
                result.emotion,
                model,
                result.latency_ms["total"],
                result.risk,
                llm_status=str(result.model.get("status") or ""),
                served_model=str(result.model.get("served_model") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - the reply matters more than the log row
            conversation_id = ""
            await self._publish("turn.failed", {"error": "persist_failed", "detail": str(exc)}, turn_id)
        with self.ctx.db() as conn:
            try:
                self.ctx.apply_turn(
                    conn,
                    user_id=self.user_id,
                    used_chunk_ids=recall.chunk_ids,
                    candidates=result.memory_candidates,
                    embedder=self.ctx.embedding_provider.embed_queries if self.ctx.embedding_provider.available else None,
                    statement_embedder=self.ctx.embedding_provider.embed_documents if self.ctx.embedding_provider.available else None,
                    settings=self.ctx.memory_config,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[esp] apply_turn failed for user={self.user_id}: {exc}", flush=True)

        risk_level = str((result.risk or {}).get("risk_level") or "")
        payload = result.as_dict() | {"conversation_id": conversation_id}
        return {
            "emotion": result.emotion,
            "risk_level": risk_level,
            "segments": list(result.segments),
            "payload": payload,
            "reply": result.reply,
            "streamed": bool(result.model.get("streamed")),
        }

    def _identity(self):
        from services.dialogue.identity import conversation_identity

        # A voiceprint match is a verified speaker, so personal memory may be
        # reached even while IOT_MEMORY_REQUIRE_IDENTITY=1 is on.  Without one,
        # the bound account is still only a *claim*: that flag keeps denying it,
        # which is exactly the behaviour we want for an unrecognised voice.
        if self._speaker_verified and self._speaker_template_id:
            return IdentityResult(
                "accepted",
                self.user_id,
                1.0,
                None,
                self._speaker_template_id,
                "voiceprint_verified",
            )
        return conversation_identity(self.user_id, None)

    async def _speak(self, result: dict[str, Any], turn_id: str) -> None:
        """Speak an already-complete answer (device command / canned reply)."""
        player = self._new_player(turn_id, payload=result.get("payload"))
        for segment in result.get("segments") or []:
            player.offer(segment)
        player.finish()
        await player.wait()
        await self._stop_speech()
        self._set_state(STATE_IDLE, "")
        await self._publish(
            "tts.end",
            {"result": result.get("payload") or {}, "played": player.played, "errors": player.errors},
            turn_id,
        )
        self._turns += 1
        with self.ctx.db() as conn:
            store.increment_turns(conn, self.session_id)

    async def _stream_answer(self, text: str, turn_id: str) -> None:
        """Answer with the model, speaking each sentence the moment it exists.

        This is where the latency win lives: the device starts talking after the
        *first* sentence instead of after the whole completion, and synthesis of
        sentence *n+1* runs while sentence *n* is still playing.
        """
        player = self._new_player(turn_id)
        streamer = SentenceStreamer(
            max_chars=self.settings.tts_max_chars,
            min_chars=self.settings.tts_min_chars,
        )

        loop = asyncio.get_running_loop()
        face_sent = False

        def on_delta(delta: str) -> None:
            # Runs on the pipeline's worker thread; ``offer`` is thread-safe and
            # ``streamer`` is only ever touched from this one thread.
            nonlocal face_sent
            if not face_sent and delta.strip():
                # The model leads its reply with one emoji, and that emoji -- not
                # a sentiment label read off the user's words -- is what the LCD
                # shows.  Sent on the *first* content chunk, so the face is up
                # before ``tts start``; therefore scheduled before the first
                # ``offer`` rather than after it.
                face_sent = True
                message = protocol.face_message(
                    self.session_id,
                    reply_text=delta,
                    fallback_emotion=_provisional_emotion(text),
                )
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._send_json(message)))
            for sentence in streamer.feed(delta):
                player.offer(sentence)

        self._set_state(STATE_THINKING, "正在理解…")

        result = await self._think(text, turn_id, on_delta=on_delta)

        risk_level = str((result or {}).get("risk_level") or "")
        if result is not None and (not face_sent or risk_level in {"attention", "urgent"}):
            # Two reasons to be here, one message: the batch fallback produced no
            # delta to read a face from, or the turn is risk-flagged and safety
            # outranks whatever the model chose to wear.
            await self._send_json(
                protocol.face_message(
                    self.session_id,
                    fallback_emotion=result["emotion"],
                    risk_level=risk_level,
                )
            )

        # Whatever the streamer did not hand out still has to be spoken: a
        # non-streaming fallback produced no deltas at all, and a stream that
        # ended without a terminator left a tail behind.
        tail = streamer.flush() if streamer.emitted else list(result["segments"] if result else [])
        for sentence in tail:
            player.offer(sentence)
        player.finish()
        self._set_state(STATE_SPEAKING, "")
        await player.wait()
        await self._stop_speech()
        if self._state == STATE_SPEAKING:
            self._set_state(STATE_IDLE, "")

        await self._publish(
            "tts.end",
            {
                "result": (result or {}).get("payload") or {},
                "played": player.played,
                "errors": player.errors,
                "streamed": bool((result or {}).get("streamed")),
                "provider": "stream" if streamer.emitted else "batch",
            },
            turn_id,
        )
        self._turns += 1
        with self.ctx.db() as conn:
            store.increment_turns(conn, self.session_id)

    async def _leave_conversation(self, turn_id: str) -> None:
        """Voice-commanded goodbye: farewell, then hand the device back to 待机."""
        await self._enter_standby("voice_end", turn_id=turn_id)

    # ------------------------------------------------------------------ device tools

    def _llm_tools(self) -> list[dict[str, Any]]:
        """Tool schemas for the model, or ``[]`` when the device declared none."""
        if self.ctx.testing or not self.settings.tools_enabled:
            return []
        try:
            return self._toolset.llm_tools() if len(self._toolset) else []
        except Exception:  # noqa: BLE001 - a broken toolset must not kill the turn
            return []

    def _run_tool_sync(self, name: str, arguments: Any) -> str:
        """Synchronous bridge used by the pipeline's tool loop.

        ``process_text`` runs in a worker thread (``asyncio.to_thread``), while the
        tool call has to travel over the session's socket on the event loop.  The
        loop is free while it awaits that thread, so handing the coroutine back to
        it is safe -- and it is the only way to keep one shared prompt/pipeline
        for both the browser and the device.
        """
        loop = self._loop
        if loop is None or loop.is_closed() or self._closed:
            raise RuntimeError("device_unavailable")
        future = asyncio.run_coroutine_threadsafe(self._mcp.call_tool(name, arguments), loop)
        return future.result(timeout=float(self.settings.tool_timeout_seconds) + 2.0)

    async def _maybe_device_command(self, text: str, turn_id: str) -> dict[str, Any] | None:
        """Serve an explicit device-setting instruction without calling the model.

        Unlike the LLM path this is *not* disabled in testing mode: driving a
        declared device tool needs no model, no ASR and no TTS provider, and
        turning it off there would make the whole feature untestable.
        """
        if not self.settings.tools_enabled or not len(self._toolset):
            return None
        command = voicecmd.parse_voice_command(text)
        if command is None:
            return None
        selected = voicecmd.select_tool(command, self._toolset.all())
        if selected is None:
            # The device declared nothing that serves this (no volume tool at
            # all): leave it to the model to explain, never fake success.
            return None
        tool, argument = selected
        schema_property = _tool_property(tool, argument)
        resolved: Any = command.value
        if command.target == "theme":
            resolved = voicecmd.clamp_theme(str(command.value), schema_property)
        elif command.kind == "step":
            current = await self._current_level(command.target)
            base = current if current is not None else 50
            resolved = max(0, min(100, base + int(command.value) * 20))

        arguments = {argument: resolved}
        started = time.perf_counter()
        try:
            outcome = await self._mcp.call_tool(tool.name, arguments)
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}:{exc}"[:200]
            await self._publish(
                "device.command",
                {"tool": tool.name, "arguments": arguments, "status": "failed", "detail": detail},
                turn_id,
            )
            with self.ctx.db() as conn:
                store.record_device_event(
                    conn, self.device_id, "device_command", {"tool": tool.name, "arguments": arguments, "status": "failed", "detail": detail}
                )
            return None
        latency_ms = int((time.perf_counter() - started) * 1000)
        reply = voicecmd.confirmation(
            command, self._language(), level=resolved if command.target != "theme" else None
        )
        conversation_id = self._persist_local(text, reply, "device-tool", latency_ms)
        payload = {
            "text": text,
            "reply": reply,
            "emotion": "neutral",
            "risk": {"risk_level": "none"},
            "source": "device_tool",
            "tool": tool.name,
            "arguments": arguments,
            "result": str(outcome)[:500],
            "conversation_id": conversation_id,
            "latency_ms": {"total": latency_ms},
        }
        await self._publish(
            "device.command",
            {"tool": tool.name, "arguments": arguments, "status": "ok", "latency_ms": latency_ms, "result": str(outcome)[:200]},
            turn_id,
        )
        with self.ctx.db() as conn:
            store.record_device_event(
                conn,
                self.device_id,
                "device_command",
                {"tool": tool.name, "arguments": arguments, "status": "ok", "latency_ms": latency_ms, "matched": command.matched},
            )
        return {
            "emotion": "neutral",
            "risk_level": "",
            "segments": split_speech(reply) or [reply],
            "payload": payload,
            "reply": reply,
        }

    async def _current_level(self, target: str) -> int | None:
        """Read the current value from a status tool, so "大一点" can be relative."""
        for tool in self._toolset.all():
            haystack = f"{tool.name} {tool.description}".lower()
            if not any(word in haystack for word in ("status", "get_", "state", "query")):
                continue
            try:
                raw = await self._mcp.call_tool(tool.name, {})
            except Exception:  # noqa: BLE001 - a status tool is a nicety, not a need
                continue
            level = voicecmd.extract_level(raw, target)
            if level is not None:
                return level
        return None

    async def _speak_local(self, text: str, turn_id: str, *, settle_seconds: float = 0.0) -> bool:
        """Play text through the session's TTS provider, with no model call."""
        player = self._new_player(turn_id, source="local")
        for segment in split_speech(text, max_chars=self.settings.tts_max_chars) or [text]:
            player.offer(segment)
        player.finish()
        await player.wait()
        await self._stop_speech()
        if settle_seconds > 0:
            # The device holds a few frames of jitter buffer; closing the audio
            # channel right after the last frame would cut the tail off.
            await asyncio.sleep(settle_seconds)
        return player.played > 0
        return played

    def _persist_local(self, text: str, reply: str, model: str, latency_ms: int) -> str:
        """Log a locally answered turn so the history is not missing rows."""
        try:
            return self.ctx.save_conversation(
                TurnRequest(
                    user_id=self.user_id,
                    text=text,
                    device_id=self.device_id,
                    voiceprint_id=self._speaker_template_id if self._speaker_verified else None,
                ),
                reply,
                "neutral",
                model,
                latency_ms,
                {"risk_level": "none"},
                llm_status="device_tool",
                served_model="",
            )
        except Exception as exc:  # noqa: BLE001 - the spoken answer matters more
            print(f"[esp] persist failed for {self.device_id}: {exc}", flush=True)
            return ""

    # ------------------------------------------------------------------ commands

    async def _command_loop(self) -> None:
        queue = self.ctx.commands.attach(self.device_id)
        try:
            while not self._closed:
                command = await queue.get()
                try:
                    await self._handle_command(command)
                except Exception as exc:  # noqa: BLE001
                    with self.ctx.db() as conn:
                        store.mark_command(conn, str(command.get("command_id", "")), "failed", reason=str(exc))
        except asyncio.CancelledError:
            return
        finally:
            self.ctx.commands.detach(self.device_id)

    async def _handle_command(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("command_id", ""))
        command_type = str(command.get("type", ""))
        payload = command.get("payload") or {}

        if command_type == "speak":
            text = str(payload.get("text", "")).strip()
            if not text:
                with self.ctx.db() as conn:
                    store.mark_command(conn, command_id, "rejected", reason="text_required")
                return
            with self.ctx.db() as conn:
                store.mark_command(conn, command_id, "delivered")
            await self._start_text_turn(text)
            return

        if command_type == "abort":
            await self._interrupt("dashboard_abort")
            with self.ctx.db() as conn:
                store.mark_command(conn, command_id, "delivered")
            return

        if command_type == "close":
            # Same ending as the spoken "退下": stop talking, say goodbye, hand
            # the device back to 待机.  Closing the socket outright is what the
            # old code did, and it cut the farewell off mid-word.
            with self.ctx.db() as conn:
                store.mark_command(conn, command_id, "delivered")
            await self._interrupt("dashboard_close")
            await self._enter_standby("dashboard_close")
            return

        if command_type in {"mcp", "iot"}:
            # Both reach the same toolset: ``mcp`` is the modern JSON-RPC channel,
            # ``iot`` the descriptor protocol older firmware uses.  Either way the
            # command only goes out when the device declared that tool, so an
            # operator cannot be shown a "delivered" row for a method the
            # firmware never implemented.
            name = str(payload.get("name") or payload.get("method") or "").strip()
            if not name:
                with self.ctx.db() as conn:
                    store.mark_command(conn, command_id, "rejected", reason="tool_name_required")
                return
            tool = self._toolset.get(name)
            if tool is None:
                with self.ctx.db() as conn:
                    store.mark_command(conn, command_id, "unsupported", reason=f"tool_not_declared:{name}")
                return
            arguments = payload.get("arguments")
            if arguments is None:
                arguments = payload.get("parameters") or {}
            try:
                outcome = await self._mcp.call_tool(tool.name, arguments)
            except Exception as exc:  # noqa: BLE001 - the dashboard must see why
                with self.ctx.db() as conn:
                    store.mark_command(conn, command_id, "failed", reason=f"{type(exc).__name__}:{exc}"[:200])
                await self._publish("device.command", {"tool": tool.name, "arguments": arguments, "status": "failed", "detail": str(exc)[:200]}, "")
                return
            with self.ctx.db() as conn:
                store.mark_command(conn, command_id, "delivered", reason=str(outcome)[:200])
            await self._publish("device.command", {"tool": tool.name, "arguments": arguments, "status": "ok", "result": str(outcome)[:200]}, "")
            return

        with self.ctx.db() as conn:
            store.mark_command(conn, command_id, "rejected", reason=f"unsupported_command:{command_type}")

    # ------------------------------------------------------------------ lifecycle

    def _resolve_user(self) -> None:
        self.user_id = self.settings.default_user_id
        with self.ctx.db() as conn:
            device = store.get_device(conn, self.device_id)
            bound = (device or {}).get("bound_user_id")
            if bound:
                row = conn.execute("SELECT status FROM users WHERE user_id=?", (bound,)).fetchone()
                if row is not None and row["status"] == "active":
                    self.user_id = str(bound)
                else:
                    # The bound account is gone or disabled: clear the binding
                    # rather than talking as a deleted user.
                    store.bind_device_user(conn, self.device_id, None)

    def _register(self) -> None:
        now = time.time()
        with self.ctx.db() as conn:
            store.upsert_device(
                conn,
                device_id=self.device_id,
                transport="esp_ws",
                protocol_version=protocol.PROTOCOL_VERSION,
                board_model=self.board_model,
                firmware=self.firmware,
                client_id=self.client_id,
                client_ip=self.client_ip,
                capabilities=["mic", "speaker", "display", "opus"],
                identity_verified=self.identity_verified,
                is_simulator=False,
                now=now,
            )
            store.open_session(
                conn,
                session_id=self.session_id,
                device_id=self.device_id,
                transport="esp_ws",
                protocol_version=protocol.PROTOCOL_VERSION,
                client_ip=self.client_ip,
                board_model=self.board_model,
                firmware=self.firmware,
                user_id=self.user_id,
                now=now,
            )
        self.ctx.live.put(
            LiveDevice(
                device_id=self.device_id,
                session_id=self.session_id,
                state=STATE_IDLE,
                transport="esp_ws",
                client_ip=self.client_ip,
                protocol_version=protocol.PROTOCOL_VERSION,
                board_model=self.board_model,
                firmware=self.firmware,
                user_id=self.user_id,
                identity_verified=self.identity_verified,
                started_at=self._connected_at,
            )
        )

    def _set_state(self, state: str, caption: str = "") -> None:
        if self._state == state and not caption:
            return
        self._state = state
        live = self.ctx.live.get(self.device_id)
        if live is not None:
            live.state = state
            if caption:
                live.caption = caption
            live.last_event_at = time.time()
            self.ctx.live.put(live)
        asyncio.create_task(self._publish_state(state, caption))

    async def _publish_state(self, state: str, caption: str) -> None:
        await self._publish("display.state", {"state": state, "caption": caption, "emotion": "neutral"})

    async def _shutdown(self) -> None:
        self._closed = True
        for task in (self._command_task, self._tools_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        # A player still mid-sentence would otherwise keep pushing frames into a
        # socket that is going away.
        player, self._player = self._player, None
        if player is not None:
            try:
                await player.abort()
            except Exception:  # noqa: BLE001
                pass
        # Pending tool calls would otherwise wait for a reply that can never come.
        self._mcp.close("session_closed")
        self.ctx.commands.detach(self.device_id)
        self.ctx.tools.remove(self.device_id)
        if self.turn_id:
            self.ctx.turn_registry.cancel(self.turn_id)
        try:
            with self.ctx.db() as conn:
                store.close_session(conn, self.session_id, turn_count=self._turns, last_error=self._last_error or None)
                store.set_device_state(conn, self.device_id, "offline")
                store.record_device_event(
                    conn,
                    self.device_id,
                    "esp_disconnected",
                    {"turns": self._turns, "duration_s": round(time.time() - self._connected_at, 1), "error": self._last_error},
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[esp] session cleanup failed for {self.device_id}: {exc}", flush=True)
        self.ctx.live.remove(self.device_id)


def _provisional_emotion(text: str) -> str:
    """The fallback face, used when the model's reply carries no emoji.

    Not the primary source of the device's expression any more -- the emoji the
    prompt makes the model prefix is.  This is the safety net for the paths that
    have no reply text to read: a batch (non-streaming) fallback, or a model that
    ignored the whitelist.  Better a crude guess at the user's mood than a device
    that stares blankly, and far better than upstream's unconditional "🙂".
    """
    from services.dialogue.pipeline import detect_emotion

    return detect_emotion(text)


def _tool_property(tool: Any, argument: str) -> dict | None:
    """The JSON-schema property a tool declares for ``argument``, if any."""
    schema = getattr(tool, "input_schema", None)
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    value = properties.get(argument)
    return value if isinstance(value, dict) else None
