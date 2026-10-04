from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import time
from typing import Any, Iterable, Sequence

import numpy as np

from services.knowledge.chunking import ChunkDraft

# Above this many chunks the matrix is loaded with mmap instead of being copied into RAM:
# 2 KB per 512-dim float32 chunk means 20k chunks = 41 MB, 100k = 205 MB, and this project
# runs on a 16 GB laptop that already holds a 1.4 GB service. See docs/RAG_KNOWLEDGE_PLAN.md A8.3.
MMAP_THRESHOLD = 20_000
MATRIX_FILENAME = "knowledge_matrix.npy"


def slugify(value: str) -> str:
    """A stable id from a document path, used as ``doc_id`` across re-imports."""

    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", Path(value).stem).strip("-")
    return cleaned[:80] or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def embedding_to_blob(values: Sequence[float]) -> bytes:
    return np.asarray(values, dtype=np.float32).tobytes()


def blob_to_embedding(blob: bytes) -> list[float]:
    return np.frombuffer(blob, dtype=np.float32).tolist()


@dataclass
class DocumentStatus:
    doc_id: str
    path: str
    sha256: str
    cleaner_version: int
    chunk_count: int
    calibration_status: str
    imported_at: float


@dataclass
class KnowledgeIndex:
    """Everything the retriever needs, loaded once per process."""

    chunk_ids: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    pages: list[int | None] = field(default_factory=list)
    matrix: Any = None
    mode: str = "empty"
    probes: list[str | None] = field(default_factory=list)
    # Sentence windows flattened across chunks: `window_matrix` is (windows, dim) and
    # `window_owner[i]` is the chunk index window i belongs to. A chunk is scored by its best
    # window, which keeps one topic per vector (see services/knowledge/windows.py).
    window_matrix: Any = None
    window_owner: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.chunk_ids)


def documents(conn: Any) -> list[DocumentStatus]:
    rows = conn.execute(
        "SELECT doc_id, path, sha256, cleaner_version, chunk_count, calibration_status, imported_at "
        "FROM knowledge_documents ORDER BY path"
    ).fetchall()
    return [
        DocumentStatus(
            doc_id=row["doc_id"],
            path=row["path"],
            sha256=row["sha256"],
            cleaner_version=int(row["cleaner_version"]),
            chunk_count=int(row["chunk_count"]),
            calibration_status=str(row["calibration_status"]),
            imported_at=float(row["imported_at"]),
        )
        for row in rows
    ]


def needs_import(conn: Any, doc_id: str, sha256: str, cleaner_version: int) -> bool:
    """Whether the stored copy is stale.

    The import key is (document hash, cleaner version): editing the cleaning rules must
    re-import, and so must editing the document. Anything else would leave chunks that no
    longer correspond to either.
    """

    row = conn.execute(
        "SELECT sha256, cleaner_version FROM knowledge_documents WHERE doc_id=?", (doc_id,)
    ).fetchone()
    if row is None:
        return True
    return str(row["sha256"]) != sha256 or int(row["cleaner_version"]) != cleaner_version


def replace_document(
    conn: Any,
    *,
    doc_id: str,
    path: str,
    title: str,
    profile: str,
    sha256: str,
    cleaner_version: int,
    pages: int,
    chunks: Sequence[ChunkDraft],
    vectors: Sequence[Sequence[float]],
    source_ids: Sequence[str],
    probes: Sequence[str | None] | None = None,
    windows: Sequence[Sequence[Sequence[float]]] | None = None,
    now: float | None = None,
) -> int:
    """Insert or replace one document and its chunks in a single transaction.

    ``vectors`` are the embeddings actually stored and searched. ``windows`` holds per chunk the
    sentence-window embeddings used for max-window scoring (see services/knowledge/windows.py).
    """

    moment = time.time() if now is None else now
    if len(chunks) != len(vectors) or len(chunks) != len(source_ids):
        raise ValueError("chunks, vectors and source_ids must be the same length")
    probe_values = list(probes) if probes is not None else [None] * len(chunks)
    if len(probe_values) != len(chunks):
        raise ValueError("probes must be the same length as chunks")
    window_values = list(windows) if windows is not None else [[] for _ in chunks]
    if len(window_values) != len(chunks):
        raise ValueError("windows must be the same length as chunks")

    # No ``with conn:`` here: the project's ManagedConnection closes on context exit, so a
    # transaction block would close the caller's connection. Commit explicitly instead.
    conn.execute("DELETE FROM knowledge_chunks WHERE doc_id=?", (doc_id,))
    conn.execute(
        "INSERT INTO knowledge_documents(doc_id, path, title, profile, sha256, cleaner_version, "
        "pages, chunk_count, calibration_status, imported_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(doc_id) DO UPDATE SET path=excluded.path, title=excluded.title, "
        "profile=excluded.profile, sha256=excluded.sha256, cleaner_version=excluded.cleaner_version, "
        "pages=excluded.pages, chunk_count=excluded.chunk_count, imported_at=excluded.imported_at",
        (doc_id, path, title, profile, sha256, cleaner_version, pages, len(chunks), "unknown", moment),
    )
    conn.executemany(
        "INSERT INTO knowledge_chunks(chunk_id, doc_id, ord, kind, section_path, page, source_id, "
        "text, probe, embedding, windows, hits, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)",
        [
            (
                f"{doc_id}#{order:04d}",
                doc_id,
                order,
                chunk.kind,
                chunk.section_path,
                chunk.page,
                source_ids[order],
                chunk.text,
                probe_values[order],
                embedding_to_blob(vectors[order]),
                b"".join(embedding_to_blob(vector) for vector in window_values[order]),
                moment,
            )
            for order, chunk in enumerate(chunks)
        ],
    )
    conn.commit()
    return len(chunks)


def delete_document(conn: Any, doc_id: str) -> int:
    removed = conn.execute("DELETE FROM knowledge_chunks WHERE doc_id=?", (doc_id,)).rowcount
    conn.execute("DELETE FROM knowledge_documents WHERE doc_id=?", (doc_id,))
    conn.commit()
    return int(removed or 0)


def set_calibration_status(conn: Any, doc_id: str, status: str) -> None:
    conn.execute("UPDATE knowledge_documents SET calibration_status=? WHERE doc_id=?", (status, doc_id))
    conn.commit()


def counts(conn: Any) -> dict[str, int]:
    total = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0])
    by_kind = {
        str(row["kind"]): int(row["count"])
        for row in conn.execute("SELECT kind, COUNT(*) AS count FROM knowledge_chunks GROUP BY kind")
    }
    docs = int(conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0])
    return {"documents": docs, "chunks": total, **{f"chunks_{key}": value for key, value in by_kind.items()}}


def bump_hits(conn: Any, chunk_ids: Iterable[str]) -> None:
    """Hit counters feed the Dashboard's read-only list; they never re-rank anything."""

    ids = [chunk_id for chunk_id in chunk_ids if chunk_id]
    if not ids:
        return
    conn.executemany(
        "UPDATE knowledge_chunks SET hits = hits + 1 WHERE chunk_id=?", [(chunk_id,) for chunk_id in ids]
    )
    conn.commit()


def load_index(conn: Any, *, matrix_path: str | Path | None = None) -> KnowledgeIndex:
    rows = conn.execute(
        "SELECT chunk_id, kind, text, source_id, page, probe, embedding, windows FROM knowledge_chunks "
        "ORDER BY doc_id, ord"
    ).fetchall()
    if not rows:
        return KnowledgeIndex()

    index = KnowledgeIndex(
        chunk_ids=[str(row["chunk_id"]) for row in rows],
        kinds=[str(row["kind"]) for row in rows],
        texts=[str(row["text"]) for row in rows],
        source_ids=[str(row["source_id"]) for row in rows],
        pages=[int(row["page"]) if row["page"] is not None else None for row in rows],
        probes=[str(row["probe"]) if row["probe"] else None for row in rows],
    )

    path = Path(matrix_path) if matrix_path else None
    if len(index) > MMAP_THRESHOLD and path is not None and path.is_file():
        index.matrix = np.load(path, mmap_mode="r")
        index.mode = "mmap"
    else:
        index.matrix = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
        index.mode = "memory"

    vectors: list[Any] = []
    owners: list[int] = []
    for position, row in enumerate(rows):
        blob = row["windows"]
        if not blob:
            continue
        # One BLOB holds every window of the chunk: n x 512 float32.
        count, remainder = divmod(len(blob), 2048)
        if remainder or count == 0:
            continue
        for window in range(count):
            vectors.append(np.frombuffer(blob[window * 2048:(window + 1) * 2048], dtype=np.float32))
            owners.append(position)
    if vectors:
        index.window_matrix = np.vstack(vectors)
        index.window_owner = owners
    return index


def export_matrix(conn: Any, path: str | Path) -> str:
    """Write the whole matrix as one .npy file (used above MMAP_THRESHOLD).

    Returns "mmap" when a file was written and "memory" when the corpus is small enough to
    stay in RAM, in which case a stale file is removed so the loader cannot pick it up.
    """

    target = Path(path)
    count = int(conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0])
    if count <= MMAP_THRESHOLD:
        target.unlink(missing_ok=True)
        return "memory"
    rows = conn.execute("SELECT embedding FROM knowledge_chunks ORDER BY doc_id, ord").fetchall()
    matrix = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
    np.save(target, matrix)
    return "mmap"
