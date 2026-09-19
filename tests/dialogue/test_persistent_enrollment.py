import io
import math
import struct
import wave

from fastapi.testclient import TestClient

from services.dialogue.app import create_app
from services.security.auth import create_session
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings
from services.voiceprint.storage import open_sealed


class Voiceprint:
    model_version = "test-speaker-v1"

    def embed(self, pcm):
        return [1.0, 0.0, 0.0]


def audio():
    data = io.BytesIO()
    with wave.open(data, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(struct.pack("<h", int(4000 * math.sin(i * .12))) for i in range(64000)))
    return data.getvalue()


def client_for(settings, provider):
    app = create_app(settings, {"voiceprint": provider})
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
    return TestClient(app, headers={"Authorization": "Bearer " + token})


def start(client, uid):
    response = client.post("/api/enrollments", json={"user_id": uid})
    assert response.status_code == 201, response.text
    return response.json()


def save(client, enrollment, step=None):
    return client.post(
        f"/api/enrollments/{enrollment['enrollment_id']}/samples",
        data={} if step is None else {"step": step}, files={"file": ("sample.wav", audio(), "audio/wav")},
    )


def complete(client, enrollment):
    return client.post(f"/api/enrollments/{enrollment['enrollment_id']}/complete")


def test_samples_resume_after_restart_and_replace_active_templates(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    provider = Voiceprint()
    with client_for(settings, provider) as client:
        uid = client.post("/api/users", json={"display_name": "Demo"}).json()["user_id"]
        enrollment = start(client, uid)
        assert save(client, enrollment, 1).status_code == 200
    with client_for(settings, provider) as client:
        enrollment = start(client, uid)
        assert enrollment["saved_steps"] == [1]
        for step in [2, 3]:
            assert save(client, enrollment, step).status_code == 200
        result = complete(client, enrollment)
        assert result.status_code == 200, result.text
        assert result.json()["model_version"] == provider.model_version
        assert len(result.json()["template_ids"]) == 3
        assert complete(client, enrollment).json() == result.json()
        replacement = start(client, uid)
        provider.embed = lambda pcm: [0.0, 1.0, 0.0]
        assert save(client, replacement, 2).status_code == 200
        assert complete(client, replacement).status_code == 200
    with open_database(settings.database_path) as conn:
        assert {row[0] for row in conn.execute("SELECT model_version FROM voiceprint_samples")} == {provider.model_version}
        active = conn.execute("SELECT embedding_json FROM voiceprint_templates WHERE active=1").fetchall()
        assert len(active) == 3
        assert [0.0, 1.0, 0.0] in [open_sealed(row[0]) for row in active]
        assert conn.execute("SELECT COUNT(*) FROM voiceprint_templates").fetchone()[0] == 6


def test_old_model_samples_require_rerecording_after_provider_change(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    provider = Voiceprint()
    with client_for(settings, provider) as client:
        uid = client.post("/api/users", json={"display_name": "Demo"}).json()["user_id"]
        enrollment = start(client, uid)
        for step in [1, 2, 3]:
            assert save(client, enrollment, step).status_code == 200
        assert complete(client, enrollment).status_code == 200
        provider.model_version = "test-speaker-v2"
        replacement = start(client, uid)
        assert replacement["saved_steps"] == []
        assert client.get(f"/api/users/{uid}/voiceprint/samples").json() == []
        assert complete(client, replacement).status_code == 422
        for step in [1, 2, 3]:
            result = save(client, replacement, step)
            assert result.status_code == 200, result.text
            assert result.json()["sample_count"] == step
        assert complete(client, replacement).status_code == 200


def test_unavailable_voiceprint_model_returns_service_error_without_saving(tmp_path):
    provider = Voiceprint()
    def unavailable(pcm):
        raise RuntimeError("model_unavailable")
    provider.embed = unavailable
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    with client_for(settings, provider) as client:
        uid = client.post("/api/users", json={"display_name": "Demo"}).json()["user_id"]
        response = save(client, start(client, uid), 1)
        assert response.status_code == 503
        assert response.json()["detail"] == "model_unavailable"
        assert client.get(f"/api/users/{uid}/voiceprint/samples").json() == []


def test_omitted_step_appends_current_model_samples_until_full(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    provider = Voiceprint()
    with client_for(settings, provider) as client:
        uid = client.post("/api/users", json={"display_name": "Demo"}).json()["user_id"]
        original = start(client, uid)
        assert save(client, original, 1).status_code == 200
        provider.model_version = "test-speaker-v2"
        enrollment = start(client, uid)
        for step in [1, 2, 3]:
            response = save(client, enrollment)
            assert response.status_code == 200, response.text
            assert response.json()["step"] == step
            assert response.json()["sample_count"] == step
        full = client.get(f"/api/users/{uid}/voiceprint/samples").json()
        response = save(client, enrollment)
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid_sample_step"
        assert client.get(f"/api/users/{uid}/voiceprint/samples").json() == full
        response = save(client, enrollment, 2)
        assert response.status_code == 200, response.text
        assert response.json()["step"] == 2
        assert response.json()["sample_count"] == 3
        assert complete(client, enrollment).status_code == 200
