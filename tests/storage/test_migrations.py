import sqlite3

from services.storage.database import open_database
from services.storage.migrations import migrate


def test_migration_creates_security_tables_and_is_idempotent(tmp_path):
    path = tmp_path / "db.sqlite3"
    conn = open_database(path)
    first = migrate(conn)
    second = migrate(conn)
    assert first == second
    assert first >= 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "consents", "sessions", "audio_turns", "audit_events"} <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migration_allows_nullable_conversation_user_id(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(conversations)")}
    assert columns["user_id"][3] == 0


def test_future_schema_version_is_rejected(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    conn.execute("PRAGMA user_version = 999")
    try:
        migrate(conn)
    except RuntimeError as exc:
        assert "unsupported_schema_version" in str(exc)
    else:
        raise AssertionError("future schema must fail closed")


def test_nullable_migration_preserves_user_foreign_key(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
    conn.executescript("CREATE TABLE users(user_id TEXT PRIMARY KEY); CREATE TABLE conversations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, input_text TEXT NOT NULL, response_text TEXT NOT NULL, emotion TEXT NOT NULL, created_at REAL NOT NULL, metadata_json TEXT NOT NULL); INSERT INTO users VALUES ('u1'); INSERT INTO conversations VALUES ('c1','u1','i','r','neutral',1,'{}');")
    conn.commit(); conn.close()
    conn = open_database(tmp_path / "legacy.sqlite3")
    migrate(conn)
    fk = conn.execute("PRAGMA foreign_key_list(conversations)").fetchall()
    assert any(row[2] == "users" for row in fk)
