"""Persistence for ESP devices, sessions, commands and OTA requests.

Kept inside the xiaozhi package rather than in ``services/dashboard/read_model``
because these tables exist only for the ESP transport; the dashboard's existing
read model stays untouched.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def upsert_device(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    transport: str,
    protocol_version: int | None = None,
    board_model: str = "",
    firmware: str = "",
    client_id: str = "",
    client_ip: str = "",
    capabilities: list[str] | None = None,
    identity_verified: bool = False,
    is_simulator: bool = False,
    now: float | None = None,
) -> None:
    """Insert or refresh a device row and append a heartbeat event.

    Mirrors :func:`services.dashboard.read_model.record_heartbeat` so simulator
    and ESP devices live in the same table and the existing device list keeps
    working, while the ESP-only columns are filled in here.
    """
    now = time.time() if now is None else now
    capabilities = capabilities or []
    conn.execute(
        """
        INSERT INTO devices(device_id,user_id,firmware,capabilities_json,is_simulator,last_seen,last_ip,
                            transport,protocol_version,board_model,client_id,last_state,identity_verified,first_seen_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id) DO UPDATE SET
            firmware=excluded.firmware,
            capabilities_json=excluded.capabilities_json,
            is_simulator=excluded.is_simulator,
            last_seen=excluded.last_seen,
            last_ip=excluded.last_ip,
            transport=excluded.transport,
            protocol_version=excluded.protocol_version,
            board_model=CASE WHEN excluded.board_model<>'' THEN excluded.board_model ELSE devices.board_model END,
            client_id=CASE WHEN excluded.client_id<>'' THEN excluded.client_id ELSE devices.client_id END,
            identity_verified=excluded.identity_verified,
            last_state=excluded.last_state,
            first_seen_at=COALESCE(devices.first_seen_at, excluded.first_seen_at)
        """,
        (
            device_id,
            None,
            firmware or None,
            json.dumps(capabilities, ensure_ascii=False),
            int(bool(is_simulator)),
            now,
            client_ip or None,
            transport,
            protocol_version,
            board_model,
            client_id,
            "connected",
            int(bool(identity_verified)),
            now,
        ),
    )
    conn.execute(
        "INSERT INTO device_events(device_id,event_type,created_at,payload_json) VALUES(?,?,?,?)",
        (
            device_id,
            "esp_connected" if not is_simulator else "heartbeat",
            now,
            json.dumps(
                {"transport": transport, "protocol_version": protocol_version, "board_model": board_model, "ip": client_ip},
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()


def set_device_state(conn: sqlite3.Connection, device_id: str, state: str, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute("UPDATE devices SET last_state=?, last_seen=? WHERE device_id=?", (state, now, device_id))
    conn.commit()


def bind_device_user(conn: sqlite3.Connection, device_id: str, user_id: str | None) -> bool:
    cursor = conn.execute("UPDATE devices SET bound_user_id=?, user_id=? WHERE device_id=?", (user_id, user_id, device_id))
    conn.commit()
    return cursor.rowcount > 0


def open_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    device_id: str,
    transport: str = "esp_ws",
    protocol_version: int | None = None,
    client_ip: str = "",
    board_model: str = "",
    firmware: str = "",
    user_id: str | None = None,
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "INSERT OR REPLACE INTO device_sessions(session_id,device_id,user_id,transport,protocol_version,client_ip,board_model,firmware,started_at,ended_at,turn_count,last_error) "
        "VALUES(?,?,?,?,?,?,?,?,?,NULL,0,NULL)",
        (session_id, device_id, user_id, transport, protocol_version, client_ip, board_model, firmware, now),
    )
    conn.execute("UPDATE devices SET last_session_at=?, last_state='connected' WHERE device_id=?", (now, device_id))
    conn.commit()


def close_session(conn: sqlite3.Connection, session_id: str, *, turn_count: int = 0, last_error: str | None = None, now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "UPDATE device_sessions SET ended_at=?, turn_count=?, last_error=? WHERE session_id=?",
        (now, turn_count, last_error, session_id),
    )
    conn.commit()


def count_turns(conn: sqlite3.Connection, session_id: str) -> int:
    row = conn.execute("SELECT turn_count FROM device_sessions WHERE session_id=?", (session_id,)).fetchone()
    return int(row["turn_count"]) if row else 0


def increment_turns(conn: sqlite3.Connection, session_id: str) -> int:
    conn.execute("UPDATE device_sessions SET turn_count=turn_count+1 WHERE session_id=?", (session_id,))
    conn.commit()
    return count_turns(conn, session_id)


def get_device(conn: sqlite3.Connection, device_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["capabilities"] = json.loads(item.pop("capabilities_json") or "[]")
    item["is_simulator"] = bool(item["is_simulator"])
    item["identity_verified"] = bool(item.get("identity_verified"))
    return item


def list_sessions(conn: sqlite3.Connection, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM device_sessions WHERE device_id=? ORDER BY started_at DESC LIMIT ?",
        (device_id, max(1, min(limit, 200))),
    ).fetchall()
    return [dict(row) for row in rows]


def record_ota_request(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    client_id: str = "",
    board_model: str = "",
    device_version: str = "",
    client_ip: str = "",
    granted_ws_url: str = "",
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "INSERT INTO ota_requests(device_id,client_id,board_model,device_version,client_ip,granted_ws_url,created_at) VALUES(?,?,?,?,?,?,?)",
        (device_id, client_id, board_model, device_version, client_ip, granted_ws_url, now),
    )
    conn.commit()


def list_ota_requests(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM ota_requests ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    return [dict(row) for row in rows]


def list_pending_devices(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """Devices seen through OTA that are not bound to a user yet."""
    rows = conn.execute(
        "SELECT device_id, board_model, client_id, last_ip, last_seen, transport FROM devices "
        "WHERE bound_user_id IS NULL AND is_simulator=0 ORDER BY last_seen DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    return [dict(row) for row in rows]


def create_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    device_id: str,
    command_type: str,
    payload: dict[str, Any] | None = None,
    actor_id: str = "",
    status: str = "pending",
    reason: str = "",
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "INSERT INTO device_commands(command_id,device_id,type,payload_json,status,actor_id,created_at,sent_at,reason) VALUES(?,?,?,?,?,?,?,?,?)",
        (command_id, device_id, command_type, json.dumps(payload or {}, ensure_ascii=False), status, actor_id, now, now if status == "sent" else None, reason),
    )
    conn.commit()


def mark_command(conn: sqlite3.Connection, command_id: str, status: str, *, reason: str = "", now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute(
        "UPDATE device_commands SET status=?, reason=?, sent_at=COALESCE(sent_at, ?) WHERE command_id=?",
        (status, reason, now if status in {"sent", "delivered"} else None, command_id),
    )
    conn.commit()


def list_commands(conn: sqlite3.Connection, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM device_commands WHERE device_id=? ORDER BY created_at DESC LIMIT ?",
        (device_id, max(1, min(limit, 200))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        result.append(item)
    return result


def record_device_event(conn: sqlite3.Connection, device_id: str, event_type: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO device_events(device_id,event_type,created_at,payload_json) VALUES(?,?,?,?)",
        (device_id, event_type, time.time(), json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()


def recent_device_events(conn: sqlite3.Connection, device_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM device_events WHERE device_id=? ORDER BY created_at DESC LIMIT ?",
        (device_id, max(1, min(limit, 200))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        result.append(item)
    return result


def latest_device_event(
    conn: sqlite3.Connection,
    device_id: str,
    event_types: tuple[str, ...],
    *,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """The most recent rows of the given types, newest first.

    A targeted lookup rather than a filter over ``recent_device_events``: heartbeats and
    ``speaker_identified`` rows arrive constantly, so "the last enrollment attempt" would fall out of
    any fixed-size window within minutes.
    """

    if not event_types:
        return []
    placeholders = ",".join("?" for _ in event_types)
    rows = conn.execute(
        f"SELECT * FROM device_events WHERE device_id=? AND event_type IN ({placeholders})"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (device_id, *event_types, max(1, min(int(limit), 50))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        result.append(item)
    return result


def list_active_templates(conn: sqlite3.Connection, model_version: str) -> list[dict[str, Any]]:
    """Enrolled voiceprints a device turn may be matched against.

    Deliberately the same join and filter as ``POST /api/voiceprint/identify``
    (active template, active user, same provider version): if the two ever drift
    apart, a speaker would be recognised in the dashboard and not on the device.
    """
    from services.voiceprint.storage import open_sealed

    rows = conn.execute(
        "SELECT t.template_id, t.user_id, t.embedding_json, t.active "
        "FROM voiceprint_templates t JOIN users u ON u.user_id = t.user_id "
        "WHERE t.active = 1 AND u.status = 'active' AND t.model_version = ?",
        (model_version,),
    ).fetchall()
    templates: list[dict[str, Any]] = []
    for row in rows:
        try:
            values = open_sealed(row["embedding_json"])
        except (ValueError, TypeError):
            # An unreadable template must not abort the turn (or the enrolment
            # of everybody else); skip it the way the identify endpoint does.
            continue
        templates.append(
            {
                "template_id": row["template_id"],
                "user_id": row["user_id"],
                "embedding": values,
                "active": bool(row["active"]),
            }
        )
    return templates
