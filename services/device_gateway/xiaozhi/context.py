"""Dependency container shared by the ESP router, session and OTA handler.

Everything the ESP layer needs from the running application is passed in
explicitly.  That keeps the package importable (and unit-testable) without
building a FastAPI app, and it means the ESP layer can never reach into
application internals by accident.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from services.device_gateway.xiaozhi.config import EspSettings
from services.device_gateway.xiaozhi.registry import CommandBus, LiveRegistry, ObserverHub, ToolRegistry


@dataclass
class TurnRequest:
    """Minimal request shape accepted by the app's ``save_conversation``."""

    user_id: str
    text: str
    device_id: str
    voiceprint_id: str | None = None


@dataclass
class GatewayContext:
    settings: EspSettings
    runtime: Any
    providers: dict
    db: Callable[[], Any]
    embedding_provider: Any
    memory_config: Any
    asr_worker: Any
    tts_provider: Any
    turn_registry: Any
    observer: ObserverHub
    live: LiveRegistry
    commands: CommandBus
    admin_auth: Callable
    record_audit: Callable
    save_conversation: Callable
    user_language: Callable[[str], str]
    process_text: Callable
    select_for_turn: Callable
    apply_turn: Callable
    testing: bool = False
    #: What each connected device declared it can do (volume, screen, ...).
    #: Defaulted so a hand-built context in a test needs no extra wiring.
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    #: Speaker verification provider, filled by the lifespan hook.  Sessions use
    #: it to decide *who* is speaking, so identity does not depend on which
    #: device is in the room (nor on a device-to-user binding).
    voiceprint_provider: Any = None
    #: Observability of the codec is resolved once; sessions read it from here.
    opus: dict = field(default_factory=dict)
    #: Which VAD was actually built (the requested one may be unavailable).
    vad_status: dict = field(default_factory=dict)
    #: Live-editable view of ``EspSettings``; replaced when the dashboard saves.
    env_path: Any = None

    def bind_loop(self, loop) -> None:
        self.observer.bind_loop(loop)
