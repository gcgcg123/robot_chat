import json

import pytest

from services.memory.recall import select_for_turn
from services.memory.repository import upsert_memory
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.voiceprint.matcher import IdentityResult


@pytest.fixture
def memory_db(tmp_path):
    with open_database(tmp_path / "memory.sqlite3") as conn:
        migrate(conn)
        conn.executemany(
            "INSERT INTO users(user_id,created_at) VALUES(?,?)", [("alice", 1), ("bob", 1)]
        )
        conn.commit()
        yield conn


def _recall(conn, user_id, query, vector):
    identity = IdentityResult("accepted", user_id, 1.0, None, "template", "test")
    return select_for_turn(
        conn, user_id=user_id, query=query, identity=identity, embedder=lambda _: vector
    ).selected


def test_unapproved_proposal_cannot_replace_verified_memory_embedding(memory_db):
    saved = upsert_memory(
        memory_db, user_id="alice", text="Lives in Paris", probe="Where is home?",
        slot_key="home", approved=True, embedding=[1.0, 0.0],
    )
    upsert_memory(
        memory_db, user_id="alice", text="Favorite food is pasta", probe="What food is preferred?",
        slot_key="home", approved=False, embedding=[0.0, 1.0],
    )

    recalled = _recall(memory_db, "alice", "Where is home?", [1.0, 0.0])
    assert [chunk.chunk_id for chunk in recalled] == [saved["chunk_id"]]
    assert recalled[0].text == "Lives in Paris"
    assert recalled[0].probe == "Where is home?"
    assert recalled[0].embedding == (1.0, 0.0)


def test_changed_probe_without_embedding_falls_back_to_lexical_recall(memory_db):
    saved = upsert_memory(
        memory_db, user_id="alice", text="Lives in Paris", probe="Where is home?",
        slot_key="profile", approved=True, embedding=[1.0, 0.0],
    )
    upsert_memory(
        memory_db, user_id="alice", text="Favorite food is pasta", probe="What food is preferred?",
        slot_key="profile", approved=True, embedding=None,
    )

    recalled = _recall(memory_db, "alice", "What food is preferred?", [0.0, 1.0])
    assert [chunk.chunk_id for chunk in recalled] == [saved["chunk_id"]]
    assert recalled[0].text == "Favorite food is pasta"
    assert recalled[0].embedding is None


def test_unchanged_probe_keeps_existing_embedding_if_provider_unavailable(memory_db):
    saved = upsert_memory(
        memory_db, user_id="alice", text="Lives in Paris", probe="Where is home?",
        slot_key="home", approved=True, embedding=[1.0, 0.0],
    )
    upsert_memory(
        memory_db, user_id="alice", text="Lives in Lyon", probe="Where is home?",
        slot_key="home", approved=True, embedding=None,
    )
    row = memory_db.execute(
        "SELECT text, embedding_json FROM memory_chunks WHERE chunk_id=?", (saved["chunk_id"],)
    ).fetchone()
    assert row["text"] == "Lives in Lyon"
    assert json.loads(row["embedding_json"]) == [1.0, 0.0]


def test_upsert_and_recall_keep_users_with_identical_keys_separate(memory_db):
    alice = upsert_memory(
        memory_db, user_id="alice", text="Lives in Paris", probe="Where is home?",
        slot_key="home", approved=True, embedding=[1.0, 0.0],
    )
    bob = upsert_memory(
        memory_db, user_id="bob", text="Lives in London", probe="Where is home?",
        slot_key="home", approved=True, embedding=[1.0, 0.0],
    )
    assert alice["chunk_id"] != bob["chunk_id"]
    assert [chunk.text for chunk in _recall(memory_db, "alice", "Where is home?", [1.0, 0.0])] == ["Lives in Paris"]
    assert [chunk.text for chunk in _recall(memory_db, "bob", "Where is home?", [1.0, 0.0])] == ["Lives in London"]
