import sqlite3
import time

from services.dashboard.read_model import (
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_STALE,
    dashboard_summary,
    device_status,
    ensure_schema,
    list_devices,
    record_heartbeat,
    emotion_trend,
)


def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_device_status_thresholds():
    now = 1_000.0
    assert device_status(now - 30, now) == STATUS_ONLINE
    assert device_status(now - 60, now) == STATUS_STALE
    assert device_status(now - 301, now) == STATUS_OFFLINE


def test_heartbeat_upserts_device_and_event():
    conn = connection()
    record_heartbeat(conn, {"device_id": "esp-1", "user_id": "u-1", "firmware": "0.2.0", "capabilities": ["audio"]}, now=100.0)

    devices = list_devices(conn, now=100.0)
    assert devices[0]["device_id"] == "esp-1"
    assert devices[0]["status"] == STATUS_ONLINE
    assert devices[0]["is_simulator"] is False
    assert conn.execute("SELECT COUNT(*) FROM device_events").fetchone()[0] == 1


def test_summary_reports_conversations_and_device_counts():
    conn = connection()
    ensure_schema(conn)
    conn.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)", ("c1", "u1", "x", "y", "positive", 90.0, '{}'))
    record_heartbeat(conn, {"device_id": "sim-1", "user_id": "u1", "is_simulator": True}, now=100.0)

    summary = dashboard_summary(conn, now=100.0)
    assert summary["conversation_count"] == 1
    assert summary["active_user_count"] == 1
    assert summary["emotion_distribution"] == {"positive": 1, "neutral": 0, "negative": 0}
    assert summary["device_counts"][STATUS_ONLINE] == 1


def test_emotion_trend_returns_daily_counts_for_user():
    conn = connection()
    ensure_schema(conn)
    conn.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)", ("c1", "u1", "x", "y", "negative", 86_400.0, '{}'))
    conn.commit()
    trend = emotion_trend(conn, "u1", now=172_800.0, days=3)
    assert any(item["counts"]["negative"] == 1 for item in trend)
