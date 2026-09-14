import io
import math
import struct
import time
import wave

from fastapi.testclient import TestClient
from services.dialogue.app import create_app
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.storage.settings import RuntimeSettings
from services.security.auth import create_session
from services.dialogue.pipeline import process_text
from services.voiceprint.matcher import IdentityResult


def client_for(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime", testing=True)
    app = create_app(settings)
    with open_database(settings.database_path) as conn:
        migrate(conn)
        token = create_session(conn, "admin", "admin")
        csrf = conn.execute("SELECT csrf_token FROM sessions").fetchone()[0]
    client = TestClient(app)
    client.cookies.set("iot_session", token)
    client.headers["X-CSRF-Token"] = csrf
    return client, settings


def wav(seconds=4, amplitude=4000):
    pcm = b"".join(struct.pack("<h", int(amplitude * math.sin(i * 0.12))) for i in range(int(16000 * seconds)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    return out.getvalue()


def new_user(client, language="yue-HK"):
    r = client.post("/api/users", json={"display_name": "流程測試", "preferred_language": language})
    assert r.status_code == 201
    return r.json()["user_id"]


def test_language_profile_and_three_confirmed_samples(tmp_path):
    client, settings = client_for(tmp_path)
    with client:
        uid = new_user(client)
        r = client.post("/api/enrollments", json={"user_id": uid, "language": "en-US"})
        assert r.status_code == 201
        enrollment = r.json()
        assert len(enrollment["prompts"]) == 3
        path = "/api/enrollments/" + enrollment["enrollment_id"]
        audio = wav()
        # Silence, short audio, and invalid formats must never increment progress.
        for content in [wav(amplitude=0), wav(seconds=0.3), b"x" * 3000]:
            assert client.post(path+"/samples", data={"step": 1}, files={"file": ("a.wav", content, "audio/wav")}).status_code == 422
        assert client.post(path+"/samples", data={"step": 2}, files={"file": ("a.wav", audio, "audio/wav")}).status_code == 422
        for step in [1, 1, 2, 3, 3]:
            r = client.post(path+"/samples", data={"step": step}, files={"file": ("a.wav", audio, "audio/wav")})
            assert r.status_code == 200, r.text
            assert r.json()["sample_count"] == step
        result = client.post(path+"/complete")
        assert result.status_code == 200, result.text
        # A lost final response must be safely retryable without a second template.
        assert client.post(path+"/complete").json() == result.json()
        user = client.get("/api/users/"+uid).json()
        assert user["enrollment_language"] == "en-US"
        assert user["preferred_language"] == "yue-HK"
        changed = client.patch("/api/users/"+uid, json={"preferred_language": "en-US"})
        assert changed.status_code == 200
        assert client.patch("/api/users/"+uid, json={"preferred_language": "invalid"}).status_code == 422
        assert client.get("/api/users").json()["items"][0]["preferred_language"] == "en-US"
        # Decoder must remove RIFF header and normalize before embedding.
        with open_database(settings.database_path) as conn:
            row = conn.execute("SELECT model_version,active FROM voiceprint_templates").fetchone()
            assert tuple(row) == ("pc-baseline-v2", 1)


def test_cancel_expire_and_delete_do_not_resurrect_user_or_remove_device(tmp_path):
    client, settings = client_for(tmp_path)
    with client:
        uid = new_user(client)
        def start():
            r=client.post("/api/enrollments", json={"user_id": uid})
            return r.json()["enrollment_id"]
        eid = start()
        client.delete("/api/enrollments/"+eid)
        assert client.post("/api/enrollments/"+eid+"/samples", files={"file": ("a.wav",wav(),"audio/wav")}).status_code == 422
        eid = start()
        client.app.state.enrollment_service.items[eid].expires_at = time.time()-1
        assert client.post("/api/enrollments/"+eid+"/samples", files={"file": ("a.wav",wav(),"audio/wav")}).status_code == 422
        eid = start()
        with open_database(settings.database_path) as conn:
            conn.execute("INSERT INTO devices(device_id,user_id,capabilities_json,last_seen) VALUES('device',?,'[]',0)", (uid,))
            conn.commit()
        r=client.request("DELETE","/api/users/"+uid,json={"confirm_user_id":uid})
        assert r.status_code == 200
        assert client.app.state.enrollment_service.items[eid].state == "canceled"
        assert client.post("/api/chat", json={"user_id":uid,"text":"hello"}).status_code == 404
        with open_database(settings.database_path) as conn:
            assert conn.execute("SELECT user_id FROM devices WHERE device_id='device'").fetchone()[0] is None
            assert conn.execute("SELECT COUNT(*) FROM users WHERE user_id=?", (uid,)).fetchone()[0] == 0


def test_language_reaches_prompt_and_fallback():
    class Capture:
        def reply(self, messages, request_id):
            self.messages = messages
            return {"text":"Hello!", "status":"ok"}
    llm = Capture()
    result = process_text("hi", user_id="u", identity=IdentityResult("accepted","u",1,None,None,"test"), session_id="s", llm=llm, language="en-US")
    assert "English" in llm.messages[0]["content"]
    assert result.reply == "Hello!"
    result = process_text("hi", user_id="u", identity=IdentityResult("accepted","u",1,None,None,"test"), session_id="s", language="en-US")
    assert "I am here to listen" in result.reply


def test_disabled_users_cannot_enroll_or_chat(tmp_path):
    client, _ = client_for(tmp_path)
    with client:
        uid = new_user(client)
        client.patch("/api/users/"+uid, json={"status":"disabled"})
        assert client.post("/api/enrollments",json={"user_id":uid}).status_code == 409
        assert client.post("/api/chat",json={"user_id":uid,"text":"hello"}).status_code == 409
