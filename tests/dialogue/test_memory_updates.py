import json

import pytest
from fastapi.testclient import TestClient

from services.dialogue.app import create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings


class Embedding:
    available = True
    failure = None

    def embed_queries(self, texts):
        self.last_queries = texts
        if self.failure == "raise":
            raise RuntimeError("embedding_unavailable")
        if self.failure == "empty":
            return []
        return [[1.0, 0.0] for text in texts]


def memory_client(tmp_path, embedder):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    app = create_app(settings, {"embedding": embedder})
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
    return TestClient(app, headers={"Authorization": "Bearer " + token}, raise_server_exceptions=False)


def create_memory(client):
    uid = client.post("/api/users", json={"display_name": "Demo"}).json()["user_id"]
    path = f"/api/users/{uid}/memories"
    response = client.post(path, json={"text": "I enjoy walking.", "probe": "What do I enjoy?"})
    assert response.status_code == 201, response.text
    return path + "/" + response.json()["chunk_id"]


@pytest.mark.parametrize("failure", ["raise", "empty", "unavailable"])
def test_memory_edit_survives_embedding_failure_and_clears_stale_vector(tmp_path, failure):
    embedder = Embedding()
    with memory_client(tmp_path, embedder) as client:
        path = create_memory(client)
        embedder.failure = failure
        embedder.available = failure != "unavailable"
        response = client.patch(path, json={"probe": "Where do I go walking?", "text": "I walk in the park."})
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "I walk in the park."
        assert response.json()["probe"] == "Where do I go walking?"
        assert response.json()["embedding_json"] is None


@pytest.mark.parametrize("update_text", [False, True])
def test_clearing_probe_embeds_text_instead_of_previous_probe(tmp_path, update_text):
    embedder = Embedding()
    with memory_client(tmp_path, embedder) as client:
        path = create_memory(client)
        payload = {"probe": None}
        if update_text:
            payload["text"] = "I walk in the park."
        response = client.patch(path, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["probe"] is None
        assert embedder.last_queries == [payload.get("text", "I enjoy walking.")]
        assert json.loads(response.json()["embedding_json"]) == [1.0, 0.0]
