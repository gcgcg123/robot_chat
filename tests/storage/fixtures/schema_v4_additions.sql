-- Changes from v3 in lqq commit 2ffde3c; apply after schema_v3.sql.
CREATE TABLE voiceprint_samples (
    sample_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, step INTEGER NOT NULL,
    embedding_json TEXT NOT NULL, quality_json TEXT NOT NULL DEFAULT '{}',
    model_version TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(user_id, step),
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE TABLE simulator_preferences (
    actor_id TEXT PRIMARY KEY, selected_user_id TEXT, updated_at REAL NOT NULL
);
PRAGMA user_version = 4;
