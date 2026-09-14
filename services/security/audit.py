from __future__ import annotations

import json
import time
import uuid


def record_audit(conn, actor_id: str, action: str, target_type: str | None = None, target_id: str | None = None, metadata: dict | None = None) -> str:
    audit_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO audit_events(audit_id,actor_id,action,target_type,target_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (audit_id, actor_id, action, target_type, target_id, json.dumps(metadata or {}, ensure_ascii=False), time.time()),
    )
    conn.commit()
    return audit_id

