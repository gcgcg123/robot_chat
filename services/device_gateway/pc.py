from __future__ import annotations

from services.device_gateway.mqtt_udp import UdpFrame, encode_frame
from services.device_gateway.protocol_peer import ControlMessage, ProtocolPeer, decode_control, encode_control


class PcDeviceAdapter:
    """In-process PC stand-in for ESP control and audio transport tests."""

    def __init__(self, device_id: str, session_id: str, *, user_id: str | None = None, capabilities: dict | None = None):
        self.device_id = device_id
        self.session_id = session_id
        self.user_id = user_id
        self.capabilities = capabilities or {"mic": True, "speaker": True, "display": {"width": 320, "height": 240}}
        self._send_sequence = 0
        self._peer = ProtocolPeer(session_id)

    def control(self, message_type: str, **extra) -> ControlMessage:
        payload = {"device_id": self.device_id, "session_id": self.session_id, **extra}
        if self.user_id is not None:
            payload.setdefault("user_id", self.user_id)
        if message_type == "device.capabilities":
            payload.setdefault("capabilities", self.capabilities)
        return decode_control(encode_control(message_type, payload))

    def encode_audio(self, payload: bytes) -> bytes:
        packet = encode_frame(UdpFrame(self.session_id, self._send_sequence, payload))
        self._send_sequence += 1
        return packet

    def receive_audio(self, packet: bytes) -> UdpFrame | None:
        return self._peer.receive_udp(packet)

