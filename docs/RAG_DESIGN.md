# RAG and Memory Design

The upstream Xiaozhi RAG integration is a RAGFlow HTTP retrieval plugin (`POST /api/v1/retrieval`) that returns chunks; it is not a complete local vector database. This project keeps that integration optional through `RAGFlowProvider`.

## Responsibility boundary

We retain two upstream ideas:

1. **LLM tool-call orchestration:** the dialogue layer may decide that retrieval is needed, call a retrieval tool, and place the returned chunks into the model context.
2. **RAGFlow retrieval contract:** the adapter follows the Xiaozhi-style HTTP request (endpoint, token, dataset IDs and bounded top-k chunks). RAGFlow is an optional external service and is disabled by default in P1.

The following is designed specifically for this emotional-companion project and is not copied from Xiaozhi:

- `shared`: administrator-approved, non-personal knowledge that an authenticated conversation may use.
- `user:<id>`: personal memories owned by one user. They are eligible only when voice identity is `accepted` and memory consent is currently valid.
- `analysis`: emotion, risk and review events for the Dashboard. These are analytics records, not automatically trusted prompt facts.

The access order is **scope and ownership -> consent and approval -> retrieval/ranking -> prompt assembly**. A retrieved chunk is marked as untrusted reference material; it cannot override system policy, issue management commands or approve a new memory.

P1 local retrieval separates `shared` knowledge from `user:<id>` memory. SQL/metadata ownership and approval filters run before ranking. Unknown or ambiguous voice identity can read shared approved chunks only. Context is labelled untrusted reference text, so documents cannot override system policy or trigger administration. Memory candidates proposed by an LLM are not automatically approved facts.

P1 supports an opt-in local sentence-transformers retriever and keeps the deterministic lexical retriever as its offline/test fallback. The embedding model is loaded lazily from local files; configure `IOT_EMBEDDING_MODEL` and `IOT_MEMORY_EMBEDDINGS=1` only after downloading and validating an approved model. Before production use, record revision/dimension/checksum and benchmark recall on an approved fixture set. Never place raw voice, voiceprint vectors, API keys or unconsented private conversation in shared RAG.
