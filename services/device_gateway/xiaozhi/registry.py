"""In-process registries for ESP sessions.

Two separate concerns, kept apart on purpose:

* :class:`LiveRegistry` -- a snapshot of what each connected device is doing
  right now.  The dashboard reads it over HTTP, so a device detail page works
  even before its WebSocket observer attaches.
* :class:`ObserverHub` -- a fan-out of the same events to dashboard observer
  sockets.  It drops for slow consumers instead of applying back-pressure to a
  device session: a stuck browser tab must never stall a conversation.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LiveDevice:
    device_id: str
    session_id: str
    state: str = "idle"
    transport: str = "esp_ws"
    client_ip: str = ""
    protocol_version: int = 1
    board_model: str = ""
    firmware: str = ""
    user_id: str = ""
    identity_verified: bool = False
    emotion: str = "neutral"
    risk_level: str = ""
    caption: str = ""
    transcript: str = ""
    user_text: str = ""
    turn_id: str = ""
    turns: int = 0
    started_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "session_id": self.session_id,
            "state": self.state,
            "transport": self.transport,
            "client_ip": self.client_ip,
            "protocol_version": self.protocol_version,
            "board_model": self.board_model,
            "firmware": self.firmware,
            "user_id": self.user_id,
            "identity_verified": self.identity_verified,
            "emotion": self.emotion,
            "risk_level": self.risk_level,
            "caption": self.caption,
            "transcript": self.transcript,
            "user_text": self.user_text,
            "turn_id": self.turn_id,
            "turns": self.turns,
            "started_at": self.started_at,
            "last_event_at": self.last_event_at,
            "age_seconds": round(max(0.0, time.time() - self.last_event_at), 1),
        }


class LiveRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, LiveDevice] = {}

    def put(self, device: LiveDevice) -> None:
        self._devices[device.device_id] = device

    def get(self, device_id: str) -> LiveDevice | None:
        return self._devices.get(device_id)

    def remove(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    def all(self) -> list[LiveDevice]:
        return sorted(self._devices.values(), key=lambda item: item.last_event_at, reverse=True)

    def count(self) -> int:
        return len(self._devices)


class ObserverHub:
    """Fan-out of device events to dashboard observers."""

    def __init__(self, *, queue_size: int = 256, max_observers: int = 8) -> None:
        self.queue_size = queue_size
        self.max_observers = max_observers
        self._queues: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dropped = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue | None:
        if len(self._queues) >= self.max_observers:
            return None
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        """Deliver one event. Must be awaited from the event loop."""
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Keep the newest events: drop the oldest and count it, so the
                # loss is visible on /api/esp/status instead of silent.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                self._dropped += 1

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        """Deliver from a worker thread (e.g. ``asyncio.to_thread`` bodies)."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.publish(event)))

    def snapshot(self) -> dict[str, Any]:
        return {"observers": len(self._queues), "dropped_events": self._dropped, "max_observers": self.max_observers}


class ToolRegistry:
    """Per-device tool sets, published as soon as a session discovers them.

    The session owns the transport; this registry exists so the dashboard and
    the command API can see *what a device can do* without holding a reference to
    a live WebSocket -- and so "why is voice control doing nothing" has an
    answer (``status['reason']``) instead of a shrug.
    """

    def __init__(self) -> None:
        self._toolsets: dict[str, Any] = {}
        self._status: dict[str, dict[str, Any]] = {}

    def put(self, device_id: str, toolset: Any, status: dict[str, Any] | None = None) -> None:
        self._toolsets[device_id] = toolset
        self._status[device_id] = dict(status or {})

    def remove(self, device_id: str) -> None:
        self._toolsets.pop(device_id, None)
        self._status.pop(device_id, None)

    def get(self, device_id: str) -> Any:
        return self._toolsets.get(device_id)

    def status(self, device_id: str) -> dict[str, Any]:
        return dict(self._status.get(device_id) or {})

    def names(self, device_id: str) -> list[str]:
        toolset = self._toolsets.get(device_id)
        if toolset is None:
            return []
        return [tool.name for tool in toolset.all()]

    def snapshot(self) -> dict[str, Any]:
        return {device_id: dict(status) for device_id, status in self._status.items()}


class CommandBus:
    """Delivers dashboard commands to a *live* device session.

    Commands are only pushed to a connected session; the HTTP layer records the
    command row first and marks it ``offline`` when no session is attached, so
    the dashboard never reports a command as applied to a device that is gone.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def attach(self, device_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._queues[device_id] = queue
        return queue

    def detach(self, device_id: str) -> None:
        self._queues.pop(device_id, None)

    def connected(self, device_id: str) -> bool:
        return device_id in self._queues

    def push(self, device_id: str, command: dict[str, Any]) -> bool:
        queue = self._queues.get(device_id)
        if queue is None:
            return False
        try:
            queue.put_nowait(command)
        except asyncio.QueueFull:
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {"attached_devices": sorted(self._queues)}
