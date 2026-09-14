from __future__ import annotations

from dataclasses import dataclass
import json
import struct


MAGIC = b"IOT1"


@dataclass(frozen=True)
class UdpFrame:
    session_id: str
    sequence: int
    payload: bytes


def encode_frame(frame: UdpFrame) -> bytes:
    sid = frame.session_id.encode("utf-8")
    if len(sid) > 255 or not 0 <= frame.sequence <= 0xFFFFFFFF:
        raise ValueError("invalid_frame")
    return MAGIC + bytes([len(sid)]) + sid + struct.pack("!I", frame.sequence) + frame.payload


def decode_frame(data: bytes, expected_session: str | None = None) -> UdpFrame:
    if len(data) < 9 or data[:4] != MAGIC:
        raise ValueError("invalid_frame")
    n = data[4]; end = 5 + n
    if len(data) < end + 4:
        raise ValueError("invalid_frame")
    session = data[5:end].decode("utf-8")
    if expected_session is not None and session != expected_session:
        raise ValueError("wrong_session")
    return UdpFrame(session, struct.unpack("!I", data[end:end + 4])[0], data[end + 4:])


class LoopbackPeer:
    def __init__(self):
        self.last_sequence = -1

    def receive(self, packet: bytes, session_id: str) -> UdpFrame | None:
        frame = decode_frame(packet, session_id)
        if frame.sequence <= self.last_sequence:
            return None
        self.last_sequence = frame.sequence
        return frame


def mqtt_control(topic: str, payload: dict) -> bytes:
    return json.dumps({"topic": topic, "payload": payload}, ensure_ascii=False, separators=(",", ":")).encode()
