-- Schema produced by main 02e7cd29526decc2b7ff17ff24ed706870a2a06c.
-- Keep this fixture independent of the current migration implementation.
CREATE TABLE users (
    user_id TEXT PRIMARY KEY, display_name TEXT, created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', gender TEXT NOT NULL DEFAULT '不透露',
    age_at_registration INTEGER, age_recorded_at REAL, profile_note TEXT NOT NULL DEFAULT '',
    preferred_language TEXT NOT NULL DEFAULT 'zh-CN', enrollment_language TEXT
);
CREATE TABLE deleted_users (user_id TEXT PRIMARY KEY);
CREATE TABLE conversations (
    id TEXT PRIMARY KEY, user_id TEXT, input_text TEXT NOT NULL,
    response_text TEXT NOT NULL, emotion TEXT NOT NULL, created_at REAL NOT NULL,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL
);
CREATE INDEX idx_conversations_created ON conversations(created_at DESC);
CREATE TABLE devices (
    device_id TEXT PRIMARY KEY, user_id TEXT, firmware TEXT, capabilities_json TEXT NOT NULL,
    is_simulator INTEGER NOT NULL DEFAULT 0, last_seen REAL NOT NULL, last_ip TEXT
);
CREATE TABLE device_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, event_type TEXT NOT NULL,
    created_at REAL NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE emotion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, user_id TEXT,
    emotion TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE conversation_analysis (
    conversation_id TEXT PRIMARY KEY, user_id TEXT, device_id TEXT,
    model TEXT, latency_ms INTEGER, created_at REAL NOT NULL
);
CREATE TABLE consents (
    consent_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, purpose TEXT NOT NULL,
    granted INTEGER NOT NULL, consent_version TEXT NOT NULL, actor_id TEXT,
    decided_at REAL NOT NULL
);
CREATE INDEX idx_consents_latest ON consents(user_id, purpose, decided_at DESC);
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, actor_id TEXT NOT NULL,
    role TEXT NOT NULL, device_id TEXT, csrf_token TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
);
CREATE INDEX idx_sessions_token ON sessions(token_hash);
CREATE TABLE audio_turns (
    turn_id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT UNIQUE,
    user_id TEXT, device_id TEXT, status TEXT NOT NULL, created_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);
CREATE TABLE audit_events (
    audit_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, action TEXT NOT NULL,
    target_type TEXT, target_id TEXT, metadata_json TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE voiceprint_templates (
    template_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, embedding_json TEXT NOT NULL,
    model_version TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE TABLE memory_chunks (
    chunk_id TEXT PRIMARY KEY, owner_user_id TEXT, text TEXT NOT NULL,
    source_id TEXT NOT NULL, source_kind TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, embedding_json TEXT, created_at REAL NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE TABLE risk_events (
    risk_id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT, risk_level TEXT,
    analysis_status TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'unreviewed',
    evidence_json TEXT NOT NULL, created_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL
);
PRAGMA user_version = 3;
