from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 9


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, display_name TEXT, created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', gender TEXT NOT NULL DEFAULT '不透露',
            age_at_registration INTEGER, age_recorded_at REAL, profile_note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS deleted_users (user_id TEXT PRIMARY KEY);
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
            probe TEXT, slot_key TEXT, tier TEXT NOT NULL DEFAULT 'hot',
            importance REAL NOT NULL DEFAULT 0.5, hits INTEGER NOT NULL DEFAULT 0,
            last_hit_at REAL, updated_at REAL,
            FOREIGN KEY(owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS risk_events (
            risk_id TEXT PRIMARY KEY, conversation_id TEXT, user_id TEXT, risk_level TEXT,
            analysis_status TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'unreviewed',
            evidence_json TEXT NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS voiceprint_samples (
            sample_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, step INTEGER NOT NULL,
            embedding_json TEXT NOT NULL, quality_json TEXT NOT NULL DEFAULT '{}',
            model_version TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(user_id, step),
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS simulator_preferences (
            actor_id TEXT PRIMARY KEY, selected_user_id TEXT, updated_at REAL NOT NULL
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
    if 'preferred_language' not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN preferred_language TEXT NOT NULL DEFAULT 'zh-CN'")
    if 'enrollment_language' not in columns:
        conn.execute('ALTER TABLE users ADD COLUMN enrollment_language TEXT')
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


def _ensure_memory_columns(conn: sqlite3.Connection) -> None:
    """Additive migration for the tiered long-term memory store.

    ``tier`` is deliberately separate from ``active``: ``active=0`` means the row
    was deleted, while ``tier='cold'`` means it is only demoted to a pseudo-deleted
    state that can still be revived by a hit.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_chunks)")}
    additions = (
        ("probe", "TEXT"),
        ("slot_key", "TEXT"),
        ("tier", "TEXT NOT NULL DEFAULT 'hot'"),
        ("importance", "REAL NOT NULL DEFAULT 0.5"),
        ("hits", "INTEGER NOT NULL DEFAULT 0"),
        ("last_hit_at", "REAL"),
        ("updated_at", "REAL"),
    )
    for name, ddl in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE memory_chunks ADD COLUMN {name} {ddl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_owner_tier ON memory_chunks(owner_user_id, tier)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_owner_slot ON memory_chunks(owner_user_id, slot_key)"
    )


def _ensure_knowledge_schema(conn: sqlite3.Connection) -> None:
    """Additive migration for the local knowledge base (RAG), schema version 6.

    Deliberately separate tables from ``memory_chunks``: the flywheel's tiering, decay and
    hit promotion mean nothing for static documents, its 500-candidate budget must not be
    shared with a corpus, and deleting one document has to cascade its chunks away.

    ``embedding`` is a float32 BLOB (512 x 4 = 2 KB) rather than the JSON text that
    ``memory_chunks`` uses: measured 5.6 ms vs 0.007 ms for one 31-chunk scan, because the
    cost is per-row ``json.loads``, not the dot product.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            doc_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            profile TEXT NOT NULL DEFAULT '',
            sha256 TEXT NOT NULL,
            cleaner_version INTEGER NOT NULL,
            pages INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            calibration_status TEXT NOT NULL DEFAULT 'unknown',
            imported_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            ord INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'reference',
            section_path TEXT NOT NULL DEFAULT '',
            page INTEGER,
            source_id TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            embedding BLOB,
            hits INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            FOREIGN KEY(doc_id) REFERENCES knowledge_documents(doc_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_doc ON knowledge_chunks(doc_id, ord);
        CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_kind ON knowledge_chunks(kind);
        """
    )
    _ensure_knowledge_probe(conn)
    _ensure_knowledge_windows(conn)


def _ensure_knowledge_windows(conn: sqlite3.Connection) -> None:
    """Schema version 8: sentence-sized windows per chunk, stored as one float32 BLOB.

    Measured reason: a chunk of ~334 characters embeds several topics into one vector, and this
    project already measured what that costs (a memory scored 0.7172 against the single question
    it answers but 0.5898 against a multi-topic message). Scoring a chunk by its *best* window
    avoids that dilution without any new model download. ``windows`` holds n x 512 float32.
    """

    columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_chunks)")}
    if columns and "windows" not in columns:
        conn.execute("ALTER TABLE knowledge_chunks ADD COLUMN windows BLOB")


def _ensure_knowledge_probe(conn: sqlite3.Connection) -> None:
    """Schema version 7: a canonical question per chunk, the way memories store a probe.

    Measured reason: chunks are statements and questions are questions, and this project already
    measured that question-vs-statement cosine barely separates (0.001) while question-vs-question
    reaches 0.215. With 341 chunks the floors could only be told apart by a 0.0017 margin and four
    scenario questions failed to rank at all, so each chunk now carries the question it answers and
    *that* is what gets embedded (see docs/RAG_KNOWLEDGE_PLAN.md A9.7.9).
    """

    columns = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_chunks)")}
    if columns and "probe" not in columns:
        conn.execute("ALTER TABLE knowledge_chunks ADD COLUMN probe TEXT")


def _ensure_conversation_summary(conn: sqlite3.Connection) -> None:
    """Schema version 9: one rolling summary per user, for turns older than the verbatim window.

    Measured reason: the prompt only ever replayed the last few exchanges, so turn 11 could not
    refer to turn 10 (and before this work, to nothing at all -- "你刚刚说了什么" was answered
    "我還沒開口"). Raising the window alone just moves the cliff; the summary carries everything
    older than the window in a few sentences instead.

    ``covered_count`` is a *count of the user's turns*, not a timestamp: conversations can share a
    ``created_at`` second, and every read of this table is ordered the same way
    (``created_at, rowid``) so a count is exact where a timestamp would need a tie-break twice.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            user_id TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            covered_count INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        """
    )


def migrate(conn: sqlite3.Connection) -> int:
    """Apply all local migrations and return the resulting schema version."""

    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"unsupported_schema_version:{current}")
    # Every step below is idempotent (CREATE IF NOT EXISTS / add-missing-column),
    # so a fresh database and an existing one follow the same path.
    _create_schema(conn)
    _ensure_user_status(conn)
    _ensure_memory_columns(conn)
    _make_user_nullable(conn)
    _ensure_knowledge_schema(conn)
    _ensure_conversation_summary(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return SCHEMA_VERSION
