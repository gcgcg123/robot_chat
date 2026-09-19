from fastapi.testclient import TestClient

from services.dialogue.app import create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings


def test_legacy_data_routes_require_authentication(tmp_path):
    app = create_app(RuntimeSettings(tmp_path / "runtime", testing=True), providers={})
    with TestClient(app) as client:
        assert client.get("/api/conversations").status_code == 401
        assert client.get("/api/devices").status_code == 401
        assert client.get("/api/dashboard/summary").status_code == 401
        assert client.get("/api/users/alice/emotion-trend").status_code == 401


def test_admin_write_routes_require_csrf(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    app = create_app(settings, providers={})
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        assert client.post("/api/chat", json={"text": "hello", "user_id": "alice"}).status_code == 403


def test_testing_provider_boundary_disables_network_calls(tmp_path, monkeypatch):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    app = create_app(settings, providers={"deepseek": object()})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "would-be-real-key")
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"text": "hello"})
    assert response.status_code == 401


def test_transcribe_rejects_oversized_audio_before_model_load(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    app = create_app(settings, providers={})
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
        csrf = conn.execute("SELECT csrf_token FROM sessions").fetchone()[0]
    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        response = client.post("/api/transcribe", headers={"X-CSRF-Token": csrf}, files={"file": ("x.wav", b"x" * (10 * 1024 * 1024 + 1), "audio/wav")})
    assert response.status_code == 413
