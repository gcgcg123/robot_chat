from tests.users.test_api import admin_client


def test_user_summary_contains_window_and_risk_fields(tmp_path):
    with admin_client(tmp_path) as client:
        created = client.post("/api/users", json={"display_name": "分析甲"}, headers={"X-CSRF-Token": client.headers["X-CSRF-Token"]})
        user_id = created.json()["user_id"]
        response = client.get(f"/api/users/{user_id}/summary?period=week")
        assert response.status_code == 200
        body = response.json()
        assert body["period"] == "week"
        assert "emotion_distribution" in body
        assert "risk" in body
        assert body["risk"]["review_status"] == "unreviewed"
