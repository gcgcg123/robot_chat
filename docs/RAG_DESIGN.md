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

Local long-term memory is now wired end to end: a SQLite repository, admin CRUD routes, a CPU ONNX embedding provider (`Xenova/bge-small-zh-v1.5`, 512-dim, revision and checksum recorded in `models/manifest.json`) and a three-tier recall cascade. Relevance is judged by cosine similarity between the question and each memory's stored *probe* (a canonical question), with a calibrated floor; the statement text only adds a small lexical weight. See [MEMORY_FLYWHEEL.md](MEMORY_FLYWHEEL.md). The optional external RAGFlow adapter is still unconfigured (P1 does not call out). Never place raw voice, voiceprint vectors, API keys or unconsented private conversation in shared RAG.
