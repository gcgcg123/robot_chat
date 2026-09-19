from services.security.consent import has_consent, record_consent
from services.storage.database import open_database
from services.storage.migrations import migrate


def test_latest_explicit_consent_decision_wins(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    record_consent(conn, "user-1", "profile", granted=True, version="v1", actor_id="admin")
    assert has_consent(conn, "user-1", "profile") is True
    record_consent(conn, "user-1", "profile", granted=False, version="v1", actor_id="admin")
    assert has_consent(conn, "user-1", "profile") is False


def test_unknown_purpose_has_no_consent(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    assert has_consent(conn, "user-1", "cloud_dialogue") is False

