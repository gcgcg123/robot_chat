import time
from pathlib import Path
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.security.auth import create_session, _hash


def test_managed_token_refreshes_expiry_without_touching_other_sessions(tmp_path):
    from simulator.local_session import ensure_local_token
    database = tmp_path / "emotional_robot.sqlite3"
    token_file = tmp_path / "secrets" / "simulator.token"
    token_file.parent.mkdir()
    with open_database(database) as conn:
        migrate(conn)
        old = create_session(conn, "sim-device", "device", device_id="sim-device", ttl_seconds=-1)
        other = create_session(conn, "admin", "admin")
    token_file.write_text(old)
    fresh = ensure_local_token(tmp_path, "sim-device")
    assert fresh != old
    assert ensure_local_token(tmp_path, "sim-device") == fresh
    with open_database(database) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (_hash(fresh),)).fetchone()
        assert row["role"] == "device" and row["device_id"] == "sim-device"
        assert row["expires_at"] > time.time()
        assert conn.execute("SELECT revoked_at FROM sessions WHERE token_hash=?", (_hash(other),)).fetchone()[0] is None
    assert token_file.read_text().strip() == fresh


def test_managed_token_creates_missing_token_and_rejects_wrong_binding(tmp_path):
    from simulator.local_session import ensure_local_token
    first = ensure_local_token(tmp_path, "sim-device")
    with open_database(tmp_path / "emotional_robot.sqlite3") as conn:
        conn.execute("UPDATE sessions SET device_id='another' WHERE token_hash=?", (_hash(first),))
        conn.commit()
    fresh = ensure_local_token(tmp_path, "sim-device")
    assert fresh != first

