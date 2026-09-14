from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from services.dialogue.app import create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings


def test_simulator_websocket_emits_turn_subtitle_and_tts_events(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
    app = create_app(settings, providers={})

    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        with client.websocket_connect("/ws/simulator/session-1") as socket:
            socket.send_json({"type": "chat", "text": "I feel sad", "user_id": "alice", "device_id": "pc-1"})
            events = []
            while not events or events[-1]["type"] != "tts.end":
                events.append(socket.receive_json())

    types = [event["type"] for event in events]
    assert types[:3] == ["turn.started", "stt.final", "display.state"]
    assert events[2]["payload"]["state"] == "thinking"
    assert not any(e["type"] == "display.state" and e["payload"]["state"] in {"listening", "speaking", "idle"} for e in events)
    assert "tts.segment" in types
    assert types[-1] == "tts.end"
    assert all(event["session_id"] == "session-1" for event in events)
    assert events[-1]["payload"]["result"]["citations"] == []


def test_simulator_websocket_can_cancel_an_active_turn(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
    app = create_app(settings, providers={})

    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        with client.websocket_connect("/ws/simulator/session-2") as socket:
            socket.send_json({"type": "turn.cancel", "turn_id": "missing"})
            event = socket.receive_json()

    assert event["type"] == "turn.interrupted"
    assert event["turn_id"] == "missing"
    assert event["payload"]["cancelled"] is False


def test_simulator_websocket_rejects_device_role(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "pc-device", "device", device_id="pc-1")
    app = create_app(settings, providers={})

    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/simulator/session-device"):
                pass
    assert exc_info.value.code == 4401
