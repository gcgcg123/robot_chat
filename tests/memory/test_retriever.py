from services.memory.retriever import retrieve
from services.memory.schemas import MemoryChunk
from services.voiceprint.matcher import IdentityResult


def test_embedding_provider_is_opt_in_without_model_download(monkeypatch):
    monkeypatch.delenv("IOT_MEMORY_EMBEDDINGS", raising=False)
    monkeypatch.setenv("IOT_EMBEDDING_MODEL", "not-downloaded")
    identity = IdentityResult("accepted", "alice", 1.0, 1.0, "vp", "test")
    chunk = MemoryChunk("m1", "alice", "喜歡喝茶", "memory", "memory", True, True)
    assert retrieve([chunk], identity, "茶") == [chunk]
