from fastapi.testclient import TestClient

from services.dialogue.app import app, create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings


def test_heartbeat_endpoint_returns_device_status(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "sim-device", "device", device_id="api-test-device")
    isolated_app = create_app(settings, providers={})
    with TestClient(isolated_app) as client:
        response = client.post("/api/device/heartbeat", headers={"Authorization": f"Bearer {token}"}, json={"device_id": "api-test-device", "is_simulator": True})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["device"]["status"] == "online"
    assert body["device"]["is_simulator"] is True


def test_dashboard_summary_endpoint_has_operational_fields(monkeypatch):
    monkeypatch.setenv("IOT_ADMIN_PASSWORD", "test-password")
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"actor_id": "test-admin", "password": "test-password"})
        assert login.status_code == 200
        response = client.get("/api/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert "emotion_distribution" in body
    assert "device_counts" in body
    assert "average_latency_ms" in body
