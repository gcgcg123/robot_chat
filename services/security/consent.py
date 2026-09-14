from __future__ import annotations

import time
import uuid


def record_consent(conn, user_id: str, purpose: str, granted: bool, version: str, actor_id: str | None = None) -> str:
    consent_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO consents(consent_id,user_id,purpose,granted,consent_version,actor_id,decided_at) VALUES(?,?,?,?,?,?,?)",
        (consent_id, user_id, purpose, int(bool(granted)), version, actor_id, time.time()),
    )
    conn.commit()
    return consent_id


def has_consent(conn, user_id: str, purpose: str) -> bool:
    row = conn.execute(
        "SELECT granted FROM consents WHERE user_id=? AND purpose=? ORDER BY decided_at DESC, rowid DESC LIMIT 1",
        (user_id, purpose),
    ).fetchone()
    return bool(row and row[0])

