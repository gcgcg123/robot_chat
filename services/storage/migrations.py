from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, display_name TEXT, created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', gender TEXT NOT NULL DEFAULT '不透露',
            age_at_registration INTEGER, age_recorded_at REAL, profile_note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, user_id TEXT, input_text TEXT NOT NULL,
            response_text TEXT NOT NULL, emotion TEXT NOT NULL, created_at REAL NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC);
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
        CREATE TABLE IF NOT EXISTS consents (
            consent_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, purpose TEXT NOT NULL,
            granted INTEGER NOT NULL, consent_version TEXT NOT NULL, actor_id TEXT,
            decided_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_consents_latest ON consents(user_id, purpose, decided_at DESC);
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, actor_id TEXT NOT NULL,
            role TEXT NOT NULL, device_id TEXT, csrf_token TEXT NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
        CREATE TABLE IF NOT EXISTS audio_turns (
            turn_id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT UNIQUE,
            user_id TEXT, device_id TEXT, status TEXT NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            audit_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL,
            target_type TEXT, target_id TEXT, metadata_json TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS voiceprint_templates (
            template_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, embedding_json TEXT NOT NULL,
            model_version TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS memory_chunks (
            chunk_id TEXT PRIMARY KEY, owner_user_id TEXT, text TEXT NOT NULL,
            source_id TEXT NOT NULL, source_kind TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, embedding_json TEXT, created_at REAL NOT NULL,
            FOREIGN KEY(owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS risk_events (
            risk_id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT, risk_level TEXT,
            analysis_status TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'unreviewed',
            evidence_json TEXT NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL
        );
        """
    )


def _make_user_nullable(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(conversations)").fetchall()
    user_column = next((row for row in columns if row[1] == "user_id"), None)
    if user_column is None or user_column[3] == 0:
        return
    conn.execute("ALTER TABLE conversations RENAME TO conversations_legacy")
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, user_id TEXT, input_text TEXT NOT NULL,
            response_text TEXT NOT NULL, emotion TEXT NOT NULL, created_at REAL NOT NULL,
            metadata_json TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL
        );
        INSERT INTO conversations(id,user_id,input_text,response_text,emotion,created_at,metadata_json)
            SELECT id,user_id,input_text,response_text,emotion,created_at,metadata_json FROM conversations_legacy;
        DROP TABLE conversations_legacy;
        CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC);
        """
    )


def _ensure_user_status(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "status" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "gender" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN gender TEXT NOT NULL DEFAULT '不透露'")
    if "age_at_registration" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN age_at_registration INTEGER")
    if "age_recorded_at" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN age_recorded_at REAL")
    if "profile_note" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN profile_note TEXT NOT NULL DEFAULT ''")


def migrate(conn: sqlite3.Connection) -> int:
    """Apply all local migrations and return the resulting schema version."""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"unsupported_schema_version:{current}")
    if current < 1:
        _create_schema(conn)
        _ensure_user_status(conn)
        _make_user_nullable(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    else:
        _create_schema(conn)
        _ensure_user_status(conn)
        _make_user_nullable(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return SCHEMA_VERSION
