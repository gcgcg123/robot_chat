from services.dialogue.pipeline import process_text
from services.voiceprint.matcher import IdentityResult


class UnavailableLlm:
    def reply(self, messages, request_id=""):
        return {
            "status": "unavailable",
            "text": "",
            "model": "deepseek-chat",
            "request_id": request_id,
        }


class DisabledRag:
    enabled = False

    def retrieve(self, query, *, user_id=None, identity=None, limit=4):
        raise AssertionError("disabled RAG must not be queried")


def test_no_rag_dialogue_returns_complete_fallback_contract():
    identity = IdentityResult("accepted", "alice", 0.91, 0.4, "vp-1", "test")

    result = process_text(
        "我今天心情不太好",
        user_id="alice",
        identity=identity,
        session_id="session-1",
        turn_id="turn-1",
        llm=UnavailableLlm(),
        rag_provider=DisabledRag(),
    )

    body = result.as_dict()
    assert body["turn_id"] == "turn-1"
    assert body["session_id"] == "session-1"
    assert body["user_id"] == "alice"
    assert body["text"] == "我今天心情不太好"
    assert body["emotion"] == "negative"
    assert body["reply"]
    assert body["citations"] == []
    assert body["segments"]
    assert body["model"]["status"] == "unavailable"
    assert set(body["latency_ms"]) >= {"total", "retrieval", "llm"}


def test_unverified_identity_cannot_retrieve_private_memory():
    from services.memory.schemas import MemoryChunk

    identity = IdentityResult("unknown", None, 0.2, None, None, "test")
    private = MemoryChunk("m1", "alice", "Alice 的私人資料", "private", "memory", True, True)
    result = process_text(
        "今天如何",
        user_id=None,
        identity=identity,
        session_id="session-2",
        turn_id="turn-2",
        llm=UnavailableLlm(),
        memories=[private],
    )

    assert result.citations == []


def test_enabled_rag_provider_is_labelled_as_untrusted_context():
    class EnabledRag:
        enabled = True

        def retrieve(self, query, *, user_id=None, identity=None, limit=4):
            from services.memory.schemas import MemoryChunk

            return [MemoryChunk("shared-1", None, "參考內容", "doc-1", "shared", True, True)]

    seen = {}

    class CaptureLlm:
        def reply(self, messages, request_id=""):
            seen["content"] = messages[-1]["content"]
            return {"status": "ok", "text": "好的", "model": "test", "usage": {}, "request_id": request_id}

    result = process_text(
        "請陪我聊聊",
        user_id="alice",
        identity=IdentityResult("accepted", "alice", 0.9, 0.2, "vp", "test"),
        session_id="s-3",
        llm=CaptureLlm(),
        rag_provider=EnabledRag(),
    )
    assert result.citations == ["doc-1"]
    assert "不可信參考" in seen["content"]


def test_role_prompt_is_system_message_before_user_message():
    seen = {}

    class CaptureLlm:
        def reply(self, messages, request_id=""):
            seen["messages"] = messages
            return {"status": "ok", "text": "好的", "model": "test", "usage": {}, "request_id": request_id}

    process_text(
        "請陪我聊聊",
        user_id="alice",
        identity=IdentityResult("accepted", "alice", 0.9, 0.2, "vp", "test"),
        session_id="s-role",
        llm=CaptureLlm(),
    )
    assert seen["messages"][0]["role"] == "system"
    assert "老年人之友" in seen["messages"][0]["content"]
    assert seen["messages"][1]["role"] == "user"
