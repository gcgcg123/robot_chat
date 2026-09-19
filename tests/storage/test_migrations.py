import sqlite3
from pathlib import Path

import pytest

from services.storage.database import open_database
from services.storage.migrations import SCHEMA_VERSION, migrate


FIXTURES = Path(__file__).parent / "fixtures"


def _legacy_database(path, version):
    conn = open_database(path)
    conn.executescript((FIXTURES / "schema_v3.sql").read_text(encoding="utf-8"))
    if version == 4:
        conn.executescript((FIXTURES / "schema_v4_additions.sql").read_text(encoding="utf-8"))
    conn.executescript(
        """
        INSERT INTO users(user_id, display_name, created_at, preferred_language, enrollment_language)
            VALUES ('u1', 'Legacy user', 1, 'en-US', 'zh-HK');
        INSERT INTO conversations VALUES ('c1', 'u1', 'Hello', 'Hi', 'neutral', 2, '{}');
        INSERT INTO consents VALUES ('consent1', 'u1', 'memory', 1, 'v1', 'admin', 3);
        INSERT INTO sessions VALUES ('s1', 'token1', 'admin', 'admin', NULL, 'csrf1', 4, 99, NULL);
        INSERT INTO audio_turns VALUES ('turn1', 's1', 'request1', 'u1', NULL, 'done', 5);
        INSERT INTO voiceprint_templates VALUES ('template1', 'u1', '[0.1,0.2]', 'model1', 1, 6);
        INSERT INTO memory_chunks VALUES ('memory1', 'u1', 'Likes music', 'c1', 'conversation', 1, 1, '[0.3]', 7);
        INSERT INTO memory_chunks VALUES ('deleted1', 'u1', 'Old fact', 'c1', 'conversation', 1, 0, NULL, 8);
        INSERT INTO risk_events VALUES ('risk1', 'c1', 'u1', 'low', 'done', 'reviewed', '{}', 9);
        """
    )
    if version == 4:
        conn.executescript(
            """
            INSERT INTO voiceprint_samples VALUES ('sample1', 'u1', 1, '[0.1,0.2]', '{"snr":20}', 'model1', 10, 11);
            INSERT INTO simulator_preferences VALUES ('admin', 'u1', 12);
            """
        )
    return conn


def _snapshot_rows(conn):
    snapshot = {}
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        table = row[0]
        columns = tuple(column[1] for column in conn.execute(f'PRAGMA table_info("{table}")'))
        rows = [tuple(item) for item in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        snapshot[table] = (columns, rows)
    return snapshot


def _assert_rows_preserved(conn, snapshot):
    for table, (columns, rows) in snapshot.items():
        selection = ", ".join(f'"{column}"' for column in columns)
        actual = conn.execute(f'SELECT {selection} FROM "{table}" ORDER BY rowid')
        assert [tuple(row) for row in actual] == rows, table


def _assert_current_schema(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"voiceprint_samples", "simulator_preferences", "memory_chunks"} <= tables
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    assert {"probe", "slot_key", "tier", "importance", "hits", "last_hit_at", "updated_at"} <= columns
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(memory_chunks)")}
    assert {"idx_memory_owner_tier", "idx_memory_owner_slot"} <= indexes


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
    _assert_current_schema(conn)
    conn.close()


def test_migration_allows_nullable_conversation_user_id(tmp_path):
    conn = open_database(tmp_path / "db.sqlite3")
    migrate(conn)
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(conversations)")}
    assert columns["user_id"][3] == 0


def test_future_schema_version_is_rejected(tmp_path):
    with _legacy_database(tmp_path / "db.sqlite3", 4) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
        before = list(conn.iterdump())
        with pytest.raises(RuntimeError, match="unsupported_schema_version"):
            migrate(conn)
        assert list(conn.iterdump()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1


@pytest.mark.parametrize("version", [3, 4])
def test_legacy_database_upgrade_preserves_data_and_survives_restart(tmp_path, version):
    path = tmp_path / "legacy.sqlite3"
    with _legacy_database(path, version) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version
        assert "tier" not in {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
        before = _snapshot_rows(conn)
        assert migrate(conn) == SCHEMA_VERSION
        _assert_current_schema(conn)
        _assert_rows_preserved(conn, before)
        defaults = conn.execute(
            "SELECT tier, importance, hits, last_hit_at, updated_at FROM memory_chunks WHERE chunk_id='memory1'"
        ).fetchone()
        assert tuple(defaults) == ("hot", 0.5, 0, None, None)
        conn.execute(
            "UPDATE memory_chunks SET tier='cold', importance=0.8, hits=3, last_hit_at=15, "
            "updated_at=16, probe='music', slot_key='preference' WHERE chunk_id='memory1'"
        )
        conn.commit()
        upgraded = list(conn.iterdump())

    with open_database(path) as conn:
        migrate(conn)
        _assert_current_schema(conn)
        assert list(conn.iterdump()) == upgraded


@pytest.mark.parametrize("version", [3, 4])
def test_legacy_upgrade_preserves_foreign_key_delete_behavior(tmp_path, version):
    with _legacy_database(tmp_path / "legacy.sqlite3", version) as conn:
        migrate(conn)
        conn.execute("DELETE FROM users WHERE user_id='u1'")
        assert conn.execute("SELECT user_id FROM conversations WHERE id='c1'").fetchone()[0] is None
        assert conn.execute("SELECT user_id FROM risk_events WHERE risk_id='risk1'").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM voiceprint_templates").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM voiceprint_samples").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_nullable_migration_preserves_user_foreign_key(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
    conn.executescript("CREATE TABLE users(user_id TEXT PRIMARY KEY); CREATE TABLE conversations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, input_text TEXT NOT NULL, response_text TEXT NOT NULL, emotion TEXT NOT NULL, created_at REAL NOT NULL, metadata_json TEXT NOT NULL); INSERT INTO users VALUES ('u1'); INSERT INTO conversations VALUES ('c1','u1','i','r','neutral',1,'{}');")
    conn.commit(); conn.close()
    conn = open_database(tmp_path / "legacy.sqlite3")
    migrate(conn)
    fk = conn.execute("PRAGMA foreign_key_list(conversations)").fetchall()
    assert any(row[2] == "users" for row in fk)
