from __future__ import annotations

import time
import uuid
from typing import Any


def create_user(conn, profile: dict[str, Any]) -> dict[str, Any]:
    user_id = f"user-{uuid.uuid4().hex[:12]}"
    now = time.time()
    conn.execute("INSERT INTO users(user_id,display_name,created_at,status,gender,age_at_registration,age_recorded_at,profile_note) VALUES(?,?,?,?,?,?,?,?)", (user_id, profile["display_name"].strip(), now, "active", profile.get("gender", "不透露"), profile.get("age_at_registration"), now if profile.get("age_at_registration") is not None else None, profile.get("profile_note", "")))
    conn.execute("UPDATE users SET preferred_language=? WHERE user_id=?", (profile.get("preferred_language", "yue-HK"), user_id))
    conn.commit()
    return get_user(conn, user_id)


def get_user(conn, user_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT user_id,display_name,gender,age_at_registration,age_recorded_at,profile_note,created_at,status,preferred_language,enrollment_language FROM users WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users(conn, q: str = "", status: str | None = None, page: int = 1, page_size: int = 20) -> tuple[list[dict[str, Any]], int]:
    page, page_size = max(1, page), max(1, min(page_size, 100))
    where, args = [], []
    if q:
        where.append("(user_id LIKE ? OR display_name LIKE ?)"); args.extend([f"%{q}%", f"%{q}%"])
    if status:
        where.append("status=?"); args.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM users" + clause, args).fetchone()[0]
    rows = conn.execute("SELECT user_id,display_name,gender,age_at_registration,age_recorded_at,profile_note,created_at,status,preferred_language,enrollment_language FROM users" + clause + " ORDER BY created_at DESC LIMIT ? OFFSET ?", args + [page_size, (page - 1) * page_size]).fetchall()
    return [dict(row) for row in rows], total


def update_user(conn, user_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    current = get_user(conn, user_id)
    if not current: return None
    fields, args = [], []
    for key in ("display_name", "gender", "profile_note", "status", "preferred_language"):
        if key in changes and changes[key] is not None:
            fields.append(f"{key}=?"); args.append(changes[key].strip() if isinstance(changes[key], str) else changes[key])
    if "age_at_registration" in changes:
        fields.append("age_at_registration=?"); args.append(changes["age_at_registration"])
        fields.append("age_recorded_at=?"); args.append(time.time())
    if fields:
        conn.execute("UPDATE users SET " + ",".join(fields) + " WHERE user_id=?", args + [user_id]); conn.commit()
    return get_user(conn, user_id)


def delete_user_data(conn, user_id: str) -> dict[str, int]:
    counts = {}
    conn.execute('INSERT OR IGNORE INTO deleted_users(user_id) VALUES(?)', (user_id,))
    conn.execute("UPDATE devices SET user_id=NULL WHERE user_id=?", (user_id,))
    for table, column in (("emotion_events", "user_id"), ("conversation_analysis", "user_id"), ("risk_events", "user_id"), ("memory_chunks", "owner_user_id"), ("voiceprint_templates", "user_id"), ("conversations", "user_id"), ("audio_turns", "user_id"), ("consents", "user_id"), ("users", "user_id")):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (user_id,)).fetchone()[0]
        conn.execute(f"DELETE FROM {table} WHERE {column}=?", (user_id,))
    conn.commit()
    return counts
