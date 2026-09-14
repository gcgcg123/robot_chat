import time

from fastapi.testclient import TestClient

from services.dialogue.app import create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings


def _admin(settings):
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
        csrf = conn.execute("SELECT csrf_token FROM sessions WHERE token_hash IS NOT NULL").fetchone()[0]
    return token, csrf


def test_analytics_window_and_risk_review_routes(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    token, csrf = _admin(settings)
    now = time.time()
    with open_database(settings.database_path) as conn:
        conn.execute("INSERT INTO users(user_id,display_name,created_at) VALUES(?,?,?)", ("alice", "Alice", now))
        conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?,?)", ("c1", "alice", "我不想活", "我在", "negative", now, "{}"))
        conn.execute("INSERT INTO conversation_analysis VALUES(?,?,?,?,?,?)", ("c1", "alice", "pc-1", "fallback", 25, now))
        conn.execute("INSERT INTO risk_events VALUES(?,?,?,?,?,?,?,?)", ("r1", "c1", "alice", "urgent", "partial", "unreviewed", '["不想活"]', now))
        conn.commit()

    app = create_app(settings, providers={})
    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        overview = client.get("/api/analytics/overview?days=7")
        events = client.get("/api/users/alice/risk-events")
        reviewed = client.patch(
            "/api/risk-events/r1",
            headers={"X-CSRF-Token": csrf},
            json={"review_status": "resolved"},
        )

    assert overview.status_code == 200
    assert overview.json()["conversation_count"] == 1
    assert overview.json()["days"] == 7
    assert events.status_code == 200
    assert events.json()[0]["evidence"] == ["不想活"]
    assert reviewed.status_code == 200
    assert reviewed.json()["review_status"] == "resolved"


def test_analytics_rejects_unsupported_window(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    token, _ = _admin(settings)
    app = create_app(settings, providers={})
    with TestClient(app) as client:
        client.cookies.set("iot_session", token)
        response = client.get("/api/analytics/overview?days=14")
    assert response.status_code == 422

