#!/usr/bin/env python
"""Give every knowledge chunk a canonical question (a "probe"), then re-embed with it.

    python scripts/generate-probes.py                 # generate + cache + store embeddings
    python scripts/generate-probes.py --doc 老年人沟通技巧 --batch 6
    python scripts/generate-probes.py --dry-run       # show what would be asked, no API calls

Why: a chunk is a *statement* and a user asks a *question*. This project measured that
question-vs-statement cosine barely separates at all (0.001, see services/memory/retriever.py)
while question-vs-question reaches 0.215, and the memory flywheel's whole design rests on probes
for that reason. With 341 knowledge chunks the floors could only be told apart by a margin of
0.0017 and four scenario questions failed to rank at all -- including "奶奶不肯吃饭怎么劝？",
whose answer sits on pages 226-234 of the A book.

The probe is embedded in place of the statement (statement stays in ``text`` for the lexical
term), so ``score_chunk()`` needs no change: it already prefers the chunk's embedding and adds a
small bigram overlap against the text.

Probes are cached in ``data/knowledge/probes/<doc_id>.json`` keyed by a hash of the chunk text,
so re-importing or re-chunking only pays for what actually changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Box-drawing output; a Windows console defaults to cp936 and would raise UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services.knowledge import repository  # noqa: E402
from services.memory.embeddings import create_embedding_provider  # noqa: E402
from services.storage.database import open_database  # noqa: E402
from services.storage.migrations import migrate  # noqa: E402
from services.storage.settings import RuntimeSettings  # noqa: E402

PROBE_DIR = ROOT / "data" / "knowledge" / "probes"
API_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

INSTRUCTION = (
    "下面是从一本老年人沟通教材里摘出的若干段落。请为每一段写一个**用户会用来问这件事的问句**"
    "（口语化、第一人称、像照护者真的会问的），而不是给段落起标题、也不是总结。"
    "只输出 JSON 数组，每个元素形如 {\"i\": 序号, \"q\": \"问句\"}，不要解释、不要代码块标记。\n\n"
)


def chunk_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_cache(doc_id: str) -> dict[str, str]:
    path = PROBE_DIR / f"{doc_id}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cache(doc_id: str, cache: dict[str, str]) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    path = PROBE_DIR / f"{doc_id}.json"
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text)[:240]


def ask(client, items: list[tuple[int, str]], depth: int = 0) -> dict[int, str]:
    """One batched request: (index, text) pairs in, {index: question} out.

    The endpoint's model spends its output budget on reasoning before it answers: measured with
    max_tokens=256 the content came back empty (finish_reason=length, 440 characters of
    reasoning), while 1200 produced the JSON. So the budget is generous and a truncated or
    unparsable answer is retried as two smaller batches, down to single items.
    """

    body = INSTRUCTION + "\n\n".join(f"[{index}] {_flatten(text)}" for index, text in items)
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": body}],
        "temperature": 0.2,
        "max_tokens": 700 + 300 * len(items),
    }
    response = client.post(
        API_URL,
        headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]},
        json=payload,
    )
    response.raise_for_status()
    choice = response.json()["choices"][0]
    content = choice["message"].get("content") or ""
    found: dict[int, str] = {}
    match = re.search(r"\[.*\]", content, re.S)
    if match:
        try:
            rows = json.loads(match.group(0))
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            try:
                index = int(row["i"])
                question = str(row["q"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if question:
                found[index] = question

    if len(found) < len(items) and len(items) > 1 and depth < 4:
        half = len(items) // 2
        merged = ask(client, items[:half], depth + 1)
        merged.update(ask(client, items[half:], depth + 1))
        return merged
    if not found:
        print(f"   (無輸出：finish={choice['finish_reason']} content={len(content)} 字)")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default="")
    parser.add_argument("--doc", default="", help="only documents whose doc_id contains this text")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="no API calls; report what is missing")
    args = parser.parse_args()

    settings = RuntimeSettings.from_env()
    database = Path(args.database) if args.database else settings.database_path
    conn = open_database(database)
    migrate(conn)

    documents = [d for d in repository.documents(conn) if args.doc in d.doc_id]
    if not documents:
        print("no documents in the knowledge base; run scripts/import-knowledge.py first")
        return 1

    if not args.dry_run and not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set in this process; the launcher injects it from data\\secrets")
        return 2

    embedder = None
    total_new = 0
    for document in documents:
        rows = conn.execute(
            "SELECT chunk_id, ord, text FROM knowledge_chunks WHERE doc_id=? ORDER BY ord", (document.doc_id,)
        ).fetchall()
        cache = load_cache(document.doc_id)
        missing = [
            (index, str(row["text"]))
            for index, row in enumerate(rows)
            if chunk_key(str(row["text"])) not in cache
        ]
        print(f"\n── {document.doc_id}  {len(rows)} 段；已有 probe {len(rows) - len(missing)}，待生成 {len(missing)}")
        if args.dry_run:
            continue
        total_new += len(missing)

        if missing:
            import httpx

            with httpx.Client(timeout=180, trust_env=False) as client:
                for start in range(0, len(missing), args.batch):
                    batch = missing[start:start + args.batch]
                    try:
                        found = ask(client, batch)
                    except Exception as exc:
                        print(f"   ✗ 批次 {start // args.batch + 1} 失敗：{type(exc).__name__}: {exc}")
                        continue
                    for index, text in batch:
                        question = found.get(index)
                        if question:
                            cache[chunk_key(text)] = question
                    done = min(start + args.batch, len(missing))
                    print(f"   {done}/{len(missing)} 段已生成（本批得到 {len(found)}/{len(batch)}）")
                    save_cache(document.doc_id, cache)
                    time.sleep(0.3)

        probes = [cache.get(chunk_key(str(row["text"]))) for row in rows]
        have = sum(1 for probe in probes if probe)
        print(f"   → probe 覆蓋 {have}/{len(rows)}")

        if not embedder:
            provider = create_embedding_provider()
            if not getattr(provider, "available", True):
                print("the embedding model is unavailable; run .\\scripts\\download-model.ps1 -Model embedding")
                return 2
            embedder = provider.embed_documents

        # Re-embed with the probe where one exists, and write it back so the retriever can show it.
        texts = [probe or str(row["text"]) for probe, row in zip(probes, rows)]
        vectors = list(embedder(texts))
        for (probe, row), vector in zip(zip(probes, rows), vectors):
            conn.execute(
                "UPDATE knowledge_chunks SET probe=?, embedding=? WHERE chunk_id=?",
                (probe, repository.embedding_to_blob(vector), str(row["chunk_id"])),
            )
        conn.commit()
        print(f"   ✓ 已用 probe 重新嵌入 {len(rows)} 段")

    print(f"\n生成 {total_new} 條新 probe；緩存目錄 {PROBE_DIR.relative_to(ROOT)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
