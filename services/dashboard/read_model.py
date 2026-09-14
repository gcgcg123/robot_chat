"""SQLite read model and persistence helpers for the operator dashboard."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Any

STATUS_ONLINE = "online"
STATUS_STALE = "stale"
STATUS_OFFLINE = "offline"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, user_id TEXT, input_text TEXT NOT NULL,
            response_text TEXT NOT NULL, emotion TEXT NOT NULL, created_at REAL NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC);
        CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, display_name TEXT, created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active', gender TEXT NOT NULL DEFAULT '不透露', age_at_registration INTEGER, age_recorded_at REAL, profile_note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY, user_id TEXT, firmware TEXT, capabilities_json TEXT NOT NULL,
            is_simulator INTEGER NOT NULL DEFAULT 0, last_seen REAL NOT NULL, last_ip TEXT
        );
        CREATE TABLE IF NOT EXISTS device_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, event_type TEXT NOT NULL,
            created_at REAL NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS emotion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, user_id TEXT,
            emotion TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_analysis (
            conversation_id TEXT PRIMARY KEY, user_id TEXT, device_id TEXT,
            model TEXT, latency_ms INTEGER, created_at REAL NOT NULL
        );
        """
    )


def device_status(last_seen: float, now: float | None = None) -> str:
    age = max(0.0, (time.time() if now is None else now) - last_seen)
    if age < 60:
        return STATUS_ONLINE
    if age <= 300:
        return STATUS_STALE
    return STATUS_OFFLINE


def record_heartbeat(conn: sqlite3.Connection, event: dict[str, Any], now: float | None = None) -> None:
    ensure_schema(conn)
    now = time.time() if now is None else now
    device_id = event["device_id"]
    caps = json.dumps(event.get("capabilities", []), ensure_ascii=False)
    conn.execute(
        """INSERT INTO devices(device_id,user_id,firmware,capabilities_json,is_simulator,last_seen,last_ip)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
           user_id=excluded.user_id, firmware=excluded.firmware, capabilities_json=excluded.capabilities_json,
           is_simulator=excluded.is_simulator, last_seen=excluded.last_seen, last_ip=excluded.last_ip""",
        (device_id, event.get("user_id"), event.get("firmware"), caps, int(bool(event.get("is_simulator"))), now, event.get("ip")),
    )
    conn.execute("INSERT INTO device_events(device_id,event_type,created_at,payload_json) VALUES(?,?,?,?)", (device_id, "heartbeat", now, json.dumps(event, ensure_ascii=False)))
    if event.get("user_id"):
        conn.execute("INSERT OR IGNORE INTO users(user_id,display_name,created_at) VALUES(?,?,?)", (event["user_id"], event.get("user_id"), now))
    conn.commit()


def list_devices(conn: sqlite3.Connection, now: float | None = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["status"] = device_status(item["last_seen"], now)
        item["is_simulator"] = bool(item["is_simulator"])
        item["capabilities"] = json.loads(item.pop("capabilities_json") or "[]")
        result.append(item)
    return result


def dashboard_summary(conn: sqlite3.Connection, now: float | None = None) -> dict[str, Any]:
    ensure_schema(conn)
    emotions = {name: 0 for name in ("positive", "neutral", "negative")}
    for row in conn.execute("SELECT emotion, COUNT(*) AS count FROM conversations GROUP BY emotion"):
        emotions[row["emotion"]] = row["count"]
    devices = list_devices(conn, now)
    latencies = [row[0] for row in conn.execute("SELECT latency_ms FROM conversation_analysis WHERE latency_ms IS NOT NULL")]
    return {
        "conversation_count": conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
        "active_user_count": conn.execute("SELECT COUNT(DISTINCT user_id) FROM conversations").fetchone()[0],
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "emotion_distribution": emotions,
        "device_counts": {status: sum(d["status"] == status for d in devices) for status in (STATUS_ONLINE, STATUS_STALE, STATUS_OFFLINE)},
        "simulator_mode": any(d["is_simulator"] for d in devices),
    }


def emotion_trend(conn: sqlite3.Connection, user_id: str, now: float | None = None, days: int = 7) -> list[dict[str, Any]]:
    ensure_schema(conn)
    now_value = time.time() if now is None else now
    today = datetime.fromtimestamp(now_value, timezone.utc).date()
    start = today - timedelta(days=max(1, min(days, 90)) - 1)
    result = []
    for offset in range((today - start).days + 1):
        date = start + timedelta(days=offset)
        day_start = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        day_end = day_start + 86400
        counts = {name: 0 for name in ("positive", "neutral", "negative")}
        for row in conn.execute("SELECT emotion, COUNT(*) AS count FROM conversations WHERE user_id=? AND created_at>=? AND created_at<? GROUP BY emotion", (user_id, day_start, day_end)):
            counts[row["emotion"]] = row["count"]
        result.append({"date": date.isoformat(), "counts": counts})
    return result


def analytics_overview(conn: sqlite3.Connection, days: int, now: float | None = None) -> dict[str, Any]:
    """Aggregate the bounded 7/30-day operator view used by the Dashboard."""
    if days not in {7, 30}:
        raise ValueError("unsupported_analytics_window")
    ensure_schema(conn)
    now_value = time.time() if now is None else now
    since = now_value - days * 86400
    emotions = {name: 0 for name in ("positive", "neutral", "negative")}
    for row in conn.execute(
        "SELECT emotion, COUNT(*) AS count FROM conversations WHERE created_at>=? GROUP BY emotion",
        (since,),
    ):
        emotions[row["emotion"]] = row["count"]
    latencies = [
        row[0]
        for row in conn.execute(
            "SELECT latency_ms FROM conversation_analysis WHERE created_at>=? AND latency_ms IS NOT NULL",
            (since,),
        )
    ]
    risks = {name: 0 for name in ("attention", "urgent")}
    for row in conn.execute(
        "SELECT risk_level, COUNT(*) AS count FROM risk_events WHERE created_at>=? GROUP BY risk_level",
        (since,),
    ):
        if row["risk_level"] in risks:
            risks[row["risk_level"]] = row["count"]
    daily = []
    today = datetime.fromtimestamp(now_value, timezone.utc).date()
    for offset in range(days - 1, -1, -1):
        date = today - timedelta(days=offset)
        day_start = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc).timestamp()
        day_end = day_start + 86400
        row = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE created_at>=? AND created_at<?",
            (day_start, day_end),
        ).fetchone()
        daily.append({"date": date.isoformat(), "conversation_count": row[0]})
    return {
        "days": days,
        "conversation_count": sum(emotions.values()),
        "emotion_distribution": emotions,
        "risk_counts": risks,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "daily": daily,
    }


def list_user_risk_events(conn: sqlite3.Connection, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM risk_events WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 200))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
        result.append(item)
    return result


def review_risk_event(conn: sqlite3.Connection, risk_id: str, review_status: str) -> dict[str, Any] | None:
    allowed = {"unreviewed", "reviewing", "resolved", "dismissed"}
    if review_status not in allowed:
        raise ValueError("invalid_review_status")
    cursor = conn.execute(
        "UPDATE risk_events SET review_status=? WHERE risk_id=?",
        (review_status, risk_id),
    )
    if cursor.rowcount == 0:
        return None
    conn.commit()
    row = conn.execute("SELECT * FROM risk_events WHERE risk_id=?", (risk_id,)).fetchone()
    item = dict(row)
    item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
    return item
