from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from services.security.auth import (
    create_session,
    require_admin,
    require_device,
)
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.dialogue.app import create_app
from services.storage.settings import RuntimeSettings


def test_admin_session_is_required_and_session_cookie_is_httponly(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    token = create_session(conn, actor_id="admin-1", role="admin")
    app = FastAPI()

    @app.get("/admin")
    def admin(request: Request):
        return {"actor_id": require_admin(request, conn)}

    with TestClient(app) as client:
        assert client.get("/admin").status_code == 401
        response = client.get("/admin", cookies={"iot_session": token})
        assert response.status_code == 200
        assert response.json() == {"actor_id": "admin-1"}


def test_device_token_cannot_manage_users(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    token = create_session(conn, actor_id="device-1", role="device", device_id="dev-a")
    app = FastAPI()

    @app.get("/admin")
    def admin(request: Request):
        return {"actor_id": require_admin(request, conn)}

    with TestClient(app) as client:
        response = client.get("/admin", cookies={"iot_session": token})
        assert response.status_code == 403


def test_require_device_checks_bound_device(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    token = create_session(conn, actor_id="device-1", role="device", device_id="dev-a")
    app = FastAPI()

    @app.get("/devices/{device_id}")
    def device(request: Request, device_id: str):
        return {"actor_id": require_device(request, conn, device_id)}

    with TestClient(app) as client:
        assert client.get("/devices/dev-a", cookies={"iot_session": token}).status_code == 200
        assert client.get("/devices/dev-b", cookies={"iot_session": token}).status_code == 403


def test_logout_requires_csrf_token(tmp_path, monkeypatch):
    monkeypatch.setenv("IOT_ADMIN_PASSWORD", "unit-password")
    settings = RuntimeSettings(data_dir=tmp_path / "runtime", testing=True)
    app = create_app(settings=settings, providers={})
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"actor_id": "admin", "password": "unit-password"})
        assert response.status_code == 200
        assert client.post("/api/auth/logout").status_code == 403
