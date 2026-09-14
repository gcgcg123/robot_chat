from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from services.device_gateway.mqtt_udp import UdpFrame, decode_frame


CONTROL_TYPES = {"device.register", "device.heartbeat", "device.capabilities", "turn.cancel"}


@dataclass(frozen=True)
class ControlMessage:
    type: str
    device_id: str
    session_id: str
    payload: dict[str, Any]
    protocol_version: int = 1


def _normalize_control(message_type: str, payload: dict[str, Any]) -> ControlMessage:
    if message_type not in CONTROL_TYPES:
        raise ValueError("unsupported_control_type")
    if not isinstance(payload, dict):
        raise ValueError("invalid_control_payload")
    device_id = str(payload.get("device_id", "")).strip()
    session_id = str(payload.get("session_id", "")).strip()
    if not device_id or not session_id:
        raise ValueError("control_identity_required")
    return ControlMessage(message_type, device_id, session_id, dict(payload))


def encode_control(message_type: str, payload: dict[str, Any]) -> bytes:
    message = _normalize_control(message_type, payload)
    return json.dumps(
        {"protocol_version": 1, "type": message.type, "payload": message.payload},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_control(data: bytes | str) -> ControlMessage:
    try:
        body = json.loads(data)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_control_message") from exc
    if body.get("protocol_version") != 1:
        raise ValueError("unsupported_protocol_version")
    return _normalize_control(str(body.get("type", "")), body.get("payload"))


class ProtocolPeer:
    """Broker-independent P1 peer; real sockets remain a Phase 2 adapter."""

    def __init__(self, session_id: str):
        if not session_id:
            raise ValueError("session_id_required")
        self.session_id = session_id
        self.last_sequence = -1

    def receive_udp(self, packet: bytes) -> UdpFrame | None:
        frame = decode_frame(packet, self.session_id)
        if frame.sequence <= self.last_sequence:
            return None
        self.last_sequence = frame.sequence
        return frame

