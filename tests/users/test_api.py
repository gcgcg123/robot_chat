from fastapi.testclient import TestClient

from services.dialogue.app import create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings


def admin_client(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    app = create_app(settings, providers={})
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
        csrf = conn.execute("SELECT csrf_token FROM sessions").fetchone()[0]
    client = TestClient(app)
    client.cookies.set("iot_session", token)
    client.headers.update({"X-CSRF-Token": csrf})
    return client


def test_profile_crud_and_paginated_list(tmp_path):
    with admin_client(tmp_path) as client:
        created = client.post("/api/users", json={"display_name": "阿甲", "gender": "不透露", "age_at_registration": 30}, headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]})
        assert created.status_code == 201
        user_id = created.json()["user_id"]
        listed = client.get("/api/users?q=阿甲&page=1&page_size=10")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["display_name"] == "阿甲"
        detail = client.get(f"/api/users/{user_id}")
        assert detail.status_code == 200
        updated = client.patch(f"/api/users/{user_id}", json={"display_name": "阿乙", "status": "disabled"}, headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]})
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "阿乙"
        assert updated.json()["status"] == "disabled"


def test_delete_requires_exact_confirmation(tmp_path):
    with admin_client(tmp_path) as client:
        created = client.post("/api/users", json={"display_name": "刪除測試"}, headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]})
        user_id = created.json()["user_id"]
        assert client.request("DELETE", f"/api/users/{user_id}", json={"confirm_user_id": "wrong"}, headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]}).status_code == 400
        assert client.request("DELETE", f"/api/users/{user_id}", json={"confirm_user_id": user_id}, headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]}).status_code == 200
        assert client.get(f"/api/users/{user_id}").status_code == 404

