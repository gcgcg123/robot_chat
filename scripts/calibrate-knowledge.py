#!/usr/bin/env python
"""Gate a document before trusting it: measure hit and miss bands, then set the floors.

    python scripts/calibrate-knowledge.py --dry-run
    python scripts/calibrate-knowledge.py --database path/to/other.sqlite3
    python scripts/calibrate-knowledge.py --doc 手册 --limit 5

Reads ``data/knowledge/calibration/<slug>.jsonl`` (one JSON object per line:

    {"question": "...", "expect": ["三、沟通的影响因素"], "note": "..."}

``expect`` empty means "the corpus should not answer this" -- those questions decide how
high the floor has to be, and a corpus that answers them is worse than one that says
"书上没写".

Per docs/RAG_KNOWLEDGE_PLAN.md A8.2 the gate is: hit rate >= 80% **and** the hit band must
not overlap the miss band. Failing that, fix the corpus (or drop the document) -- do not
lower the floor to make it pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The report uses box-drawing and check marks; a Windows console defaults to cp936 and would
# raise UnicodeEncodeError on the *first* redirected line, losing the whole run's output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services.knowledge import repository  # noqa: E402
from services.knowledge.profiles import load_profiles, profile_for  # noqa: E402
from services.knowledge.rerank import create_reranker  # noqa: E402
from services.knowledge.retriever import KnowledgeProvider, KnowledgeSettings  # noqa: E402
from services.memory.embeddings import create_embedding_provider  # noqa: E402
from services.storage.database import open_database  # noqa: E402
from services.storage.migrations import migrate  # noqa: E402
from services.storage.settings import RuntimeSettings  # noqa: E402

CALIBRATION_DIR = ROOT / "data" / "knowledge" / "calibration"
CORPUS_DIR = ROOT / "data" / "RAG_data"
NEGATIVES_FILE = CALIBRATION_DIR / "_negatives.jsonl"
HIT_RATE_GATE = 0.80


def read_cases(path) -> list[dict]:
    if not path.is_file():
        return []
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


def cases_for(profile_doc: str) -> list[dict]:
    """The questions one document must be able to answer.

    Negatives do not live here: retrieval runs over the whole corpus, so "this question must
    return nothing" is a property of the corpus, not of one document. Keeping them per document
    made the gate measure the wrong thing -- book 1's old negative "奶奶不肯吃饭怎么劝？" became
    a question another document legitimately answers.
    """

    return read_cases(CALIBRATION_DIR / f"{profile_doc}.jsonl")


def negatives() -> list[dict]:
    return read_cases(NEGATIVES_FILE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default="", help="target SQLite file (default: the project database)")
    parser.add_argument("--doc", default="", help="only this profile/document")
    parser.add_argument("--limit", type=int, default=5, help="how many candidates to inspect per question")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not store the status")
    parser.add_argument("--show", action="store_true", help="print every candidate with its score")
    parser.add_argument(
        "--rerank",
        choices=("off", "local", "llm"),
        default="off",
        help="run the second stage too (local = bge-reranker-base via torch; see rerank.py)",
    )
    parser.add_argument("--rerank-keep", type=int, default=5, help="candidates the reranker may keep")
    parser.add_argument(
        "--rerank-floor",
        type=float,
        default=None,
        help="stage-2 admission threshold on the reranker's own score (default: the built-in value)",
    )
    parser.add_argument(
        "--rerank-below",
        type=float,
        default=None,
        help="only rerank when the best hybrid score is under this (default: the built-in value)",
    )
    parser.add_argument(
        "--rerank-max-length",
        type=int,
        default=None,
        help="token window the cross-encoder reads (default: the built-in value; 512 is its limit)",
    )
    args = parser.parse_args()

    settings = RuntimeSettings.from_env()
    database = Path(args.database) if args.database else settings.database_path
    profiles = load_profiles(ROOT / "data" / "knowledge" / "profiles")
    documents = [(path, profile_for(path, profiles)) for path in sorted(CORPUS_DIR.rglob("*.md"))]
    documents = [(path, profile) for path, profile in documents if profile is not None and args.doc in str(path)]
    if not documents:
        print("no profiled document selected")
        return 1

    provider_embedding = create_embedding_provider()
    if not getattr(provider_embedding, "available", True):
        print("the embedding model is unavailable; run .\\scripts\\download-model.ps1 -Model embedding")
        return 2

    reranker = None
    if args.rerank != "off":
        overrides = {**os.environ, "IOT_KNOWLEDGE_RERANK": args.rerank}
        if args.rerank_max_length is not None:
            overrides["IOT_KNOWLEDGE_RERANK_MAX_LENGTH"] = str(args.rerank_max_length)
        reranker = create_reranker(overrides)
        if reranker is None:
            print(f"the {args.rerank} reranker could not be built")
            return 2

    conn = open_database(database)
    migrate(conn)
    knowledge_settings = KnowledgeSettings(
        rerank_enabled=args.rerank != "off",
        rerank_keep=args.rerank_keep,
        **({"rerank_floor": args.rerank_floor} if args.rerank_floor is not None else {}),
        **({"rerank_below": args.rerank_below} if args.rerank_below is not None else {}),
    )
    provider = KnowledgeProvider(
        connection_factory=lambda: open_database(database),
        embedder=provider_embedding.embed_queries,
        matrix_path=settings.data_dir / repository.MATRIX_FILENAME,
        settings=knowledge_settings,
        reranker=reranker,
    )
    provider.refresh()
    if not provider.enabled:
        print(f"knowledge index is empty in {database}; run scripts/import-knowledge.py first")
        return 1

    print(f"database={database}  candidates per question={args.limit}  rerank={args.rerank}")
    if args.rerank != "off":
        print(
            f"rerank settings: keep={knowledge_settings.rerank_keep}"
            f"  below={knowledge_settings.rerank_below}  floor={knowledge_settings.rerank_floor}"
            f"  max_length={getattr(reranker, 'max_length', '—')}"
        )
    print(f"index: {provider.stats()}")
    if reranker is not None:
        available = getattr(reranker, "available", True)
        print(f"reranker: {type(reranker).__name__}  available={available}"
              f"  {getattr(reranker, 'last_error', '') or ''}")

    failures = 0
    for path, profile in documents:
        cases = cases_for(profile.doc)
        print(f"\n── {profile.doc}  ({len(cases)} questions)")
        if not cases:
            print(f"   ✗ no calibration file at {CALIBRATION_DIR / (profile.doc + '.jsonl')}")
            failures += 1
            continue

        hit_scores: list[float] = []
        miss_scores: list[float] = []
        rerank_hits: list[float] = []
        missing = 0

        def is_expected(chunk) -> bool:
            """A candidate counts as expected if its source matches a key or its page a range.

            Page ranges matter for the scanned books: their sections are long and one answer
            spans several printed pages, so pinning a single page would call a correct hit a
            miss (measured: "老人用绝食来要挟子女" landed on p234 inside the 226-234 task and
            was scored MISS before ranges existed).
            """

            if any(key in chunk.source_id for key in expect):
                return True
            page = provider.chunk_detail(chunk).get("page")
            return bool(page is not None and any(low <= page <= high for low, high in expect_pages))

        for case in cases:
            question = case["question"]
            expect = case.get("expect") or []
            expect_pages = [tuple(pair) for pair in (case.get("expect_pages") or [])]
            ranked = provider.rank(question, limit=args.limit)
            # A hit is decided by what the *production* path injects, because that is what the model
            # sees: `rank` is stage 1, and with a reranker enabled the served set differs. The score
            # bands come from stage 1, since the floors are defined on those scores -- plus the
            # reranker's own bands when it ran, since those are what stage 2 admits on.
            trace: dict = {}
            served = provider.retrieve(question, limit=args.limit, trace=trace)
            rerank_scores = trace.get("rerank_scores") or {}
            top = ranked[0][0] if ranked else 0.0
            wanted = bool(expect or expect_pages)
            if wanted:
                hit_chunk = next((chunk for chunk in served if is_expected(chunk)), None)
                if hit_chunk is not None:
                    hit_scores.append(
                        next((score for score, chunk in ranked if chunk.chunk_id == hit_chunk.chunk_id), top)
                    )
                    if hit_chunk.chunk_id in rerank_scores:
                        rerank_hits.append(rerank_scores[hit_chunk.chunk_id])
                else:
                    missing += 1
                    miss_scores.append(top)
            else:
                miss_scores.append(top)
            if args.show:
                print(f"   ? {question}")
                for score, chunk in ranked:
                    mark = "✓" if is_expected(chunk) else " "
                    extra = (
                        f"  rerank {rerank_scores[chunk.chunk_id]:+7.2f}"
                        if chunk.chunk_id in rerank_scores
                        else ""
                    )
                    print(f"       {mark} {score:.4f}{extra}  {chunk.source_id}")
            else:
                label = "HIT " if hit_chunk is not None else ("MISS" if wanted else "none")
                print(f"   {label} {top:.4f}  {question[:34]:<34} → {ranked[0][1].source_id if ranked else '—'}")

        expected_hits = len(cases)
        answered = expected_hits - missing
        rate = answered / expected_hits if expected_hits else 0.0
        hit_low = min(hit_scores) if hit_scores else 0.0
        print(f"   命中 {answered}/{expected_hits}（{rate * 100:.0f}%）  命中帶最低 {hit_low:.4f}")
        if not args.dry_run:
            repository.set_calibration_status(
                conn, repository.slugify(profile.doc), "passed" if rate >= HIT_RATE_GATE else "failed"
            )

    # The gate itself is global: retrieval sees one shared index, so whether a floor can separate
    # answers from non-answers is a property of the whole corpus, not of a single document.
    #
    # Two different failures are counted separately, because they call for different fixes:
    #   * a *negative* that scores high  -> the floor is wrong (or the corpus is too broad);
    #   * a *positive* that is not ranked -> retrieval quality (the section exists but does not
    #     win), which no floor can repair.
    # Conflating them made the band test meaningless: a failed positive scoring 0.68 was compared
    # against hits scoring 0.62 and reported as "bands overlap".
    pooled_hits: list[float] = []
    pooled_negatives: list[float] = []
    rerank_hit_band: list[float] = []
    rerank_negative_band: list[float] = []
    failed_positives: list[tuple[float, str]] = []
    pooled_total = 0
    for path, profile in documents:
        for case in cases_for(profile.doc):
            expect = case.get("expect") or []
            expect_pages = [tuple(pair) for pair in (case.get("expect_pages") or [])]
            ranked = provider.rank(case["question"], limit=args.limit)
            trace: dict = {}
            served = provider.retrieve(case["question"], limit=args.limit, trace=trace)
            rerank_scores = trace.get("rerank_scores") or {}
            pooled_total += 1
            top = ranked[0][0] if ranked else 0.0

            def expected(chunk) -> bool:
                return any(key in chunk.source_id for key in expect) or (
                    (page := provider.chunk_detail(chunk).get("page")) is not None
                    and any(low <= page <= high for low, high in expect_pages)
                )

            hit_chunk = next((chunk for chunk in served if expected(chunk)), None)
            if hit_chunk is not None:
                pooled_hits.append(
                    next((score for score, chunk in ranked if chunk.chunk_id == hit_chunk.chunk_id), top)
                )
                if hit_chunk.chunk_id in rerank_scores:
                    rerank_hit_band.append(rerank_scores[hit_chunk.chunk_id])
            else:
                failed_positives.append((top, case["question"]))
    for case in negatives():
        ranked = provider.rank(case["question"], limit=args.limit)
        trace = {}
        provider.retrieve(case["question"], limit=args.limit, trace=trace)
        pooled_negatives.append(ranked[0][0] if ranked else 0.0)
        # For a negative, the reranker's verdict on the *best* candidate in its pool is what stage 2
        # would have to reject. Using only what was served would be circular: an empty served list
        # would read as a perfect band.
        if trace.get("rerank_scores"):
            rerank_negative_band.append(max(trace["rerank_scores"].values()))

    if pooled_total:
        hit_low = min(pooled_hits) if pooled_hits else 0.0
        negative_high = max(pooled_negatives) if pooled_negatives else 0.0
        # Which pair of bands the gate tests depends on what actually admits candidates in
        # production: stage 1's hybrid floor, or -- once the reranker has run -- its own score.
        rerank_low = min(rerank_hit_band) if rerank_hit_band else 0.0
        rerank_high = max(rerank_negative_band) if rerank_negative_band else 0.0
        stage2 = bool(rerank_hit_band and rerank_negative_band)
        tested_low, tested_high = (rerank_low, rerank_high) if stage2 else (hit_low, negative_high)
        overlap = tested_low <= tested_high if (tested_low and tested_high) else False
        rate = (pooled_total - len(failed_positives)) / pooled_total
        margin = tested_low - tested_high
        print("\n══ 全域閘門（所有文檔的正例 + 全域負例）")
        print(
            f"   命中 {pooled_total - len(failed_positives)}/{pooled_total}（{rate * 100:.0f}%）"
            f"  命中帶最低 {hit_low:.4f}  真負例最高 {negative_high:.4f}"
            f"  {'✗ 重疊' if hit_low <= negative_high else '✓ 不重疊'}（第一階段混合分）"
        )
        if stage2:
            print(
                f"   重排帶：命中最低 {rerank_low:+.2f}  真負例最高 {rerank_high:+.2f}"
                f"  {'✗ 重疊' if rerank_low <= rerank_high else '✓ 不重疊'}"
                f"（第二階段以此為准入，{len(rerank_hit_band)} 正例 / {len(rerank_negative_band)} 負例）"
            )
        if failed_positives:
            print(f"   排不上來的正例 {len(failed_positives)} 題（檢索品質問題，不是門檻問題）：")
            for score, question in sorted(failed_positives, reverse=True):
                print(f"      {score:.4f}  {question}")
        passed = rate >= HIT_RATE_GATE and not overlap
        print(f"   閘門：{'✓ 通過' if passed else '✗ 不通過'}（需 ≥ {HIT_RATE_GATE * 100:.0f}% 且兩帶不重疊）")
        if passed:
            advice = (
                "充足"
                if margin >= 0.01
                else "⚠ 極薄：門檻能分開但沒有安全邊際；本地 cross-encoder（--rerank local）實測能抬高命中率，代價是每回合數秒"
            )
            print(f"   餘量 {margin:.4f}（{advice}）")
        if stage2 and rerank_low > rerank_high:
            print(
                f"   建議 rerank floor：{round((rerank_low + rerank_high) / 2, 2)}"
                f"  目前 {knowledge_settings.rerank_floor}"
            )
        elif stage2:
            print("   重排帶重疊：沒有任何門檻能分開（比混合分的重疊更嚴重，提高門檻會同時丟掉命中）")
        elif hit_low and negative_high:
            print(
                f"   建議 floor：{round((hit_low + negative_high) / 2, 3)}"
                f"  目前 scenario={provider.floor_for('scenario')} reference={provider.floor_for('reference')}"
            )
        if not passed:
            failures += 1
    conn.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
