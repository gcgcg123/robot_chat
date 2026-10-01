"""Renew only the locally managed PC simulator's short-lived device session."""
from pathlib import Path
import time

from services.security.auth import _hash, create_session
from services.storage.database import open_database
from services.storage.migrations import migrate






def ensure_local_token(data_dir: str | Path, device_id: str) -> str:
    root = Path(data_dir).resolve()
    token_path = root / "secrets" / "simulator.token"
    token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    with open_database(root / "emotional_robot.sqlite3") as conn:
        migrate(conn)
        row = conn.execute(
            "SELECT role, device_id, revoked_at, expires_at FROM sessions WHERE token_hash=?",
            (_hash(token),),
        ).fetchone()
        if (row and row["role"] == "device" and row["device_id"] == device_id
                and row["revoked_at"] is None and row["expires_at"] > time.time() + 60):
            return token
        # Never revoke another device or an administrator session.
        conn.execute(
            "UPDATE sessions SET revoked_at=? WHERE token_hash=? AND role='device' AND device_id=?",
            (time.time(), _hash(token), device_id),
        )
        token = create_session(conn, device_id, "device", device_id=device_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # Writing in place retains an existing Windows ACL.
    token_path.write_text(token, encoding="utf-8")
    token_path.chmod(0o600)
    return token

