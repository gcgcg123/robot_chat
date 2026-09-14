from services.device_gateway.pc import PcDeviceAdapter


def test_pc_adapter_exercises_broker_independent_control_and_udp_loopback():
    pc = PcDeviceAdapter("pc-1", "session-1", user_id="alice")
    registration = pc.control("device.register")
    assert registration.type == "device.register"
    assert registration.device_id == "pc-1"

    first = pc.encode_audio(b"pcm-a")
    second = pc.encode_audio(b"pcm-b")
    assert pc.receive_audio(first).sequence == 0
    assert pc.receive_audio(first) is None
    assert pc.receive_audio(second).sequence == 1

