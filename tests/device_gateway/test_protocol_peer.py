import pytest

from services.device_gateway.mqtt_udp import UdpFrame, encode_frame
from services.device_gateway.protocol_peer import ProtocolPeer, decode_control, encode_control
from services.device_gateway.contracts import DeviceEvent


def test_control_contract_accepts_only_phase1_message_types():
    encoded = encode_control("device.heartbeat", {"device_id": "pc-1", "session_id": "s-1"})
    message = decode_control(encoded)
    assert message.type == "device.heartbeat"
    assert message.device_id == "pc-1"
    assert message.session_id == "s-1"

    with pytest.raises(ValueError, match="unsupported_control_type"):
        encode_control("admin.delete", {"device_id": "pc-1", "session_id": "s-1"})


def test_protocol_peer_rejects_wrong_session_duplicate_and_out_of_order_frames():
    peer = ProtocolPeer("s-1")
    assert peer.receive_udp(encode_frame(UdpFrame("s-1", 2, b"new"))).payload == b"new"
    assert peer.receive_udp(encode_frame(UdpFrame("s-1", 2, b"duplicate"))) is None
    assert peer.receive_udp(encode_frame(UdpFrame("s-1", 1, b"old"))) is None
    with pytest.raises(ValueError, match="wrong_session"):
        peer.receive_udp(encode_frame(UdpFrame("another", 3, b"wrong")))


def test_display_state_is_a_supported_device_event():
    event = DeviceEvent("esp-1", "session-1", "turn-1", "display.state", {"state": "thinking", "emotion": "neutral"})
    payload = event.as_dict()
    assert payload["type"] == "display.state"
    assert payload["payload"]["state"] == "thinking"
