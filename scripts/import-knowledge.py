#!/usr/bin/env python
"""Import the Markdown corpus into the local knowledge base (schema version 6).

    python scripts/import-knowledge.py --dry-run        # show what would be indexed
    python scripts/import-knowledge.py                  # import everything changed
    python scripts/import-knowledge.py --doc 手册 --force
    python scripts/import-knowledge.py --database path/to/other.sqlite3

Behaviour:

* A document with no profile in ``data/knowledge/profiles/`` is **refused**, not indexed
  with rules that do not fit it. See docs/RAG_KNOWLEDGE_PLAN.md A8.1.
* Idempotency key is (file SHA-256, CLEANER_VERSION), so editing either the document or
  the cleaning rules re-imports, and re-running import does nothing.
* Embeddings come from the project's existing provider (the same 512-dim ONNX model the
  memory flywheel uses) rather than a second implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Box-drawing output; a Windows console defaults to cp936 and would raise UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services.knowledge.chunking import chunk_document, citation  # noqa: E402
from services.knowledge.markdown import front_matter  # noqa: E402
from services.knowledge.profiles import CLEANER_VERSION, KnowledgeProfile, load_profiles, profile_for  # noqa: E402
from services.knowledge.windows import split_windows  # noqa: E402
from services.knowledge import repository  # noqa: E402
from services.memory.embeddings import create_embedding_provider  # noqa: E402
from services.storage.database import open_database  # noqa: E402
from services.storage.migrations import migrate  # noqa: E402
from services.storage.settings import RuntimeSettings  # noqa: E402

CORPUS_DIRS = (ROOT / "data" / "RAG_data", ROOT / "data" / "knowledge")
PROFILES_DIR = ROOT / "data" / "knowledge" / "profiles"
PROBE_DIR = ROOT / "data" / "knowledge" / "probes"


def probe_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_probe_cache(doc_id: str) -> dict[str, str]:
    path = PROBE_DIR / f"{doc_id}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
# Hand-written scenario answers live here. They have no chapter vocabulary for the cleaning
# rules to match, so they get a built-in profile instead of a JSON file per document: every
# heading is scenario content, nothing is treated as an exercise, and the whole file may be
# a single section.
CURATED_DIR = ROOT / "data" / "knowledge" / "curated"


def curated_profile(path: Path) -> KnowledgeProfile | None:
    if CURATED_DIR not in path.parents:
        return None
    return KnowledgeProfile(
        doc=path.stem,
        source_label=path.stem,
        drop_pure_exercises=False,
        min_chars=20,
        max_chars=600,
        min_heading_level=1,
        default_kind="scenario",
    )


def documents(selected: str) -> list[Path]:
    found: set[Path] = set()
    for base in CORPUS_DIRS:
        if base.is_dir():
            found.update(
                path for path in base.rglob("*.md") if path.is_file() and selected in str(path)
            )
    return sorted(found)


def open_target(database: str | None):
    settings = RuntimeSettings.from_env()
    path = Path(database) if database else settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(path)
    migrate(conn)
    return conn, path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="clean and chunk, but write nothing")
    parser.add_argument("--force", action="store_true", help="re-import even when the hash and rules match")
    parser.add_argument("--doc", default="", help="only documents whose path contains this text")
    parser.add_argument("--database", default="", help="target SQLite file (default: the project database)")
    parser.add_argument("--chunks", action="store_true", help="list every chunk and dropped section")
    parser.add_argument(
        "--with-probes",
        action="store_true",
        help="embed cached canonical questions instead of the statements (see generate-probes.py; measured to help one book and hurt two, so it is opt-in)",
    )
    args = parser.parse_args()

    profiles = load_profiles(PROFILES_DIR)
    paths = documents(args.doc)
    if not paths:
        print(f"no Markdown corpus under {CORPUS_DIR}")
        return 1

    conn = None
    if not args.dry_run:
        conn, target = open_target(args.database or None)
        print(f"database: {target}")
    print(f"cleaner version: {CLEANER_VERSION}  profiles: {len(profiles)}")

    embedder = None
    refused = 0
    imported = 0
    skipped = 0

    for path in paths:
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        profile = curated_profile(path) or profile_for(path, profiles)
        print(f"\n── {relative}")
        if profile is None:
            refused += 1
            print(f"   ✗ REFUSED: no profile in {PROFILES_DIR.relative_to(ROOT)} matches this document.")
            print(f"     Add one (see docs/RAG_KNOWLEDGE_PLAN.md A8.1), then run --dry-run again.")
            continue

        text = path.read_text(encoding="utf-8")
        meta = front_matter(text.splitlines())
        chunks, dropped = chunk_document(text, profile)
        scenario = sum(1 for chunk in chunks if chunk.kind == "scenario")
        print(
            f"   profile={profile.doc}  chunks={len(chunks)} (scenario {scenario}, "
            f"reference {len(chunks) - scenario})  dropped={len(dropped)}"
        )
        print(f"   pages={meta.get('pages', '?')}  title={meta.get('title', profile.doc)}")

        if args.chunks:
            for index, chunk in enumerate(chunks, start=1):
                page = f"p{chunk.page}" if chunk.page else "p?"
                print(f"     {index:>3}. [{chunk.kind:<9}] {page:>4} {len(chunk.text):>5} 字  {citation(chunk, profile.source_label)}")
            for item in dropped:
                print(f"        dropped [{item.reason:<20}] {item.chars:>5} 字  {item.section_path}")

        if args.dry_run:
            continue

        sha = repository.file_sha256(path)
        doc_id = repository.slugify(profile.doc or path.stem)
        if not args.force and not repository.needs_import(conn, doc_id, sha, CLEANER_VERSION):
            skipped += 1
            print(f"   = up to date (sha256 {sha[:12]}, cleaner {CLEANER_VERSION}); use --force to redo")
            continue

        if embedder is None:
            provider = create_embedding_provider()
            if not getattr(provider, "available", True):
                print("   ✗ the embedding model is unavailable; the knowledge base needs it.")
                print("     Download it with: .\\scripts\\download-model.ps1 -Model embedding")
                return 2
            embedder = provider.embed_documents

        # Embeddings are the *statement's* by default. `--with-probes` swaps in the cached
        # canonical questions instead; measured on this corpus that helps the scanned book A
        # (71% -> 79% hit rate) but hurts the manual (100% -> 82%) and the expository book B
        # (100% -> 33%), so it stays opt-in and the statement vectors are the default.
        probes = None
        if args.with_probes:
            cache = load_probe_cache(doc_id)
            probes = [cache.get(probe_key(chunk.text)) for chunk in chunks]
            covered = sum(1 for probe in probes if probe)
            print(f"   probes: {covered}/{len(chunks)}（scripts/generate-probes.py 產生）")
        vectors = list(embedder([probe or chunk.text for probe, chunk in zip(probes or [None] * len(chunks), chunks)]))
        source_ids = [citation(chunk, profile.source_label) for chunk in chunks]
        # Sentence windows per chunk, embedded so retrieval can score a chunk by its best window
        # instead of its topic-averaged whole (services/knowledge/windows.py).
        window_texts = [split_windows(chunk.text) for chunk in chunks]
        flat_windows = [text for windows in window_texts for text in windows]
        flat_vectors = list(embedder(flat_windows)) if flat_windows else []
        window_vectors: list[list[Sequence[float]]] = []
        cursor = 0
        for windows in window_texts:
            window_vectors.append(flat_vectors[cursor:cursor + len(windows)])
            cursor += len(windows)
        written = repository.replace_document(
            conn,
            doc_id=doc_id,
            path=relative,
            title=str(meta.get("title") or profile.doc),
            profile=profile.doc,
            sha256=sha,
            cleaner_version=CLEANER_VERSION,
            pages=int(meta.get("pages") or 0),
            chunks=chunks,
            vectors=vectors,
            source_ids=source_ids,
            probes=probes,
            windows=window_vectors,
        )
        imported += 1
        total_windows = sum(len(windows) for windows in window_vectors)
        print(f"   ✓ imported {written} chunks（{total_windows} 個句子視窗）as doc_id={doc_id}")

    if conn is not None:
        mode = repository.export_matrix(conn, ROOT / "IoTGroup5" / repository.MATRIX_FILENAME)
        print(f"\nsummary: imported={imported} skipped={skipped} refused={refused} matrix={mode}")
        print(f"counts: {repository.counts(conn)}")
        print("note: gate a new document with scripts/calibrate-knowledge.py before trusting it (A8.2)")
    else:
        print(f"\nsummary (dry run): documents={len(paths)} refused={refused}")
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
