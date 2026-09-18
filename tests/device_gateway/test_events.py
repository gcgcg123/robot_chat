from services.device_gateway.events import normalize_heartbeat


def test_normalize_heartbeat_applies_defaults_and_limits():
    result = normalize_heartbeat({"device_id": "esp-1", "capabilities": ["audio", "audio", ""], "firmware": "1.0"})
    assert result["device_id"] == "esp-1"
    assert result["user_id"] is None
    assert result["capabilities"] == ["audio"]
    assert result["is_simulator"] is False


def test_normalize_heartbeat_rejects_missing_device_id():
    try:
        normalize_heartbeat({})
    except ValueError as exc:
        assert "device_id" in str(exc)
    else:
        raise AssertionError("missing device id should fail")
