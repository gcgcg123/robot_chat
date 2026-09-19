import json

import pytest

from services.memory.recall import select_for_turn
from services.memory.flywheel import MemorySettings
from services.memory.repository import set_tier, upsert_memory
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
        conn, user_id=user_id, query=query, identity=identity,
        embedder=lambda questions: [vector for _ in questions],
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
        slot_key="home", approved=True, embedding=[1.0, 0.0],
    )
    upsert_memory(
        memory_db, user_id="alice", text="Favorite food is pasta", probe="What food is preferred?",
        slot_key="home", approved=True, embedding=None,
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


def test_category_key_keeps_distinct_facts_with_identical_probes(memory_db):
    statements = {
        "Plays piano": [1.0, 0.0],
        "Writes poems": [0.0, 1.0],
        "Enjoys playing piano": [1.0, 0.0],
    }
    def embed_statements(texts):
        return [statements[text] for text in texts]

    first = upsert_memory(
        memory_db, user_id="alice", text="Plays piano", probe="What are my hobbies?",
        slot_key="hobby", approved=True, embedding=[1.0, 0.0], statement_embedder=embed_statements,
    )
    second = upsert_memory(
        memory_db, user_id="alice", text="Writes poems", probe="What are my hobbies?",
        slot_key="hobby", approved=True, embedding=[1.0, 0.0], statement_embedder=embed_statements,
    )
    repeated = upsert_memory(
        memory_db, user_id="alice", text="Enjoys playing piano", probe="What are my hobbies?",
        slot_key="hobby", approved=True, embedding=[1.0, 0.0], statement_embedder=embed_statements,
    )
    assert first["chunk_id"] != second["chunk_id"]
    assert repeated["chunk_id"] == first["chunk_id"]
    assert repeated["action"] == "merged"
    assert {chunk.text for chunk in _recall(memory_db, "alice", "What are my hobbies?", [1.0, 0.0])} == {
        "Enjoys playing piano", "Writes poems",
    }


def test_multi_question_recall_keeps_matches_from_hot_and_cold_tiers(memory_db):
    hot = upsert_memory(
        memory_db, user_id="alice", text="Lives in Paris", probe="Where is home?",
        slot_key="home", approved=True, embedding=[1.0, 0.0],
    )
    cold = upsert_memory(
        memory_db, user_id="alice", text="Plays piano", probe="What are my hobbies?",
        slot_key="hobby", approved=True, embedding=[0.0, 1.0],
    )
    set_tier(memory_db, cold["chunk_id"], "cold")
    vectors = {"Where is home": [1.0, 0.0], "What are my hobbies": [0.0, 1.0]}
    result = select_for_turn(
        memory_db, user_id="alice", query="Where is home? What are my hobbies?",
        identity=IdentityResult("accepted", "alice", 1.0, None, "template", "test"),
        embedder=lambda questions: [vectors[question] for question in questions],
    )
    assert set(result.chunk_ids) == {hot["chunk_id"], cold["chunk_id"]}
    assert result.revived == [cold["chunk_id"]]


def test_relevant_basic_outside_unconditional_budget_is_still_recalled(memory_db):
    name = upsert_memory(
        memory_db, user_id="alice", text="Called Alice", probe="What is my name?",
        slot_key="name", approved=True, embedding=[1.0, 0.0], now=1,
    )
    language = upsert_memory(
        memory_db, user_id="alice", text="Speaks English", probe="What language do I speak?",
        slot_key="language", approved=True, embedding=[0.0, 1.0], now=2,
    )
    for saved in (name, language):
        set_tier(memory_db, saved["chunk_id"], "slot")
    result = select_for_turn(
        memory_db, user_id="alice", query="What is my name?",
        identity=IdentityResult("accepted", "alice", 1.0, None, "template", "test"),
        embedder=lambda questions: [[1.0, 0.0] for _ in questions],
        settings=MemorySettings(slot_top_n=1, inject_top_n=1),
    )
    assert set(result.chunk_ids) == {name["chunk_id"], language["chunk_id"]}
