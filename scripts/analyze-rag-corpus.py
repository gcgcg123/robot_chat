#!/usr/bin/env python
"""Measure the RAG corpus *before* anything is imported. Read-only: no database, no network.

    python scripts/analyze-rag-corpus.py            # every document found
    python scripts/analyze-rag-corpus.py --json     # machine-readable summary
    python scripts/analyze-rag-corpus.py --doc 手册  # one document

Per Markdown document it reports:

  * size        lines, paragraphs, characters, token estimate (the project's own tokenizer)
  * structure   headings per level, ``<sub>pN</sub>`` page anchors, images, captions, table rows
  * profile     which profile applies, and the keep/drop verdict with its reason per section
  * chunks      heading-based chunks after cleaning: count, size spread, how many are still
                over ``max_chars``, and how many sections were dropped and why

PDFs are probed for a text layer when ``pdfplumber`` is installed, because a scanned book
cannot go through ``scripts/pdf_to_md.py`` at all: the two books added on 2026-10-03 have
zero extractable characters on all 306 and 251 pages, so they need OCR, not conversion.

Why this exists: the numbers behind the cleaning rules and the relevance floor have to be
reproducible. See docs/RAG_KNOWLEDGE_PLAN.md sections A8.1 and A8.6.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The report uses box-drawing characters; a Windows console defaults to cp936 and would raise
# UnicodeEncodeError on redirected output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from services.knowledge.chunking import chunk_document  # noqa: E402
from services.knowledge.markdown import parse_sections  # noqa: E402
from services.knowledge.profiles import is_pure_exercise, load_profiles, profile_for  # noqa: E402

CORPUS_DIR = ROOT / "data" / "RAG_data"
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
PAGE_MARKER = re.compile(r"<sub>\s*p(\d+)\s*</sub>", re.IGNORECASE)


def documents() -> list[Path]:
    return sorted(path for path in CORPUS_DIR.rglob("*.md") if path.is_file())


def token_count(text: str) -> int | None:
    """Tokens by the project's own embedding tokenizer (a proxy for the LLM's)."""

    tokenizer_path = ROOT / "models" / "embedding" / "bge-small-zh-v1.5" / "tokenizer.json"
    if not tokenizer_path.is_file():
        return None
    try:
        from tokenizers import Tokenizer

        return len(Tokenizer.from_file(str(tokenizer_path)).encode(text).ids)
    except Exception:
        return None


def pdf_report() -> list[dict]:
    """Text-layer probe for the PDFs sitting next to the Markdown."""

    try:
        import pdfplumber
    except ImportError:
        return [{"note": "pdfplumber not installed; PDFs not probed"}]

    found: list[dict] = []
    for path in sorted(CORPUS_DIR.rglob("*.pdf")):
        entry: dict = {"name": path.name, "mb": round(path.stat().st_size / 1048576, 2)}
        try:
            with pdfplumber.open(str(path)) as pdf:
                pages = len(pdf.pages)
                sample = min(pages, 30)
                step = max(1, pages // sample)
                chars = sum(
                    len(pdf.pages[index].extract_text() or "") for index in range(0, pages, step)
                )
                entry.update({"pages": pages, "sampled_pages": len(range(0, pages, step)), "sampled_chars": chars})
                entry["text_layer"] = chars > 0
        except Exception as exc:  # pragma: no cover - unreadable PDF
            entry["error"] = f"{type(exc).__name__}: {exc}"
        found.append(entry)
    return found


def analyze(path: Path, profiles) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    nodes = parse_sections(lines)
    profile = profile_for(path, profiles)

    levels: dict[int, int] = {}
    for node in nodes:
        levels[node.level] = levels.get(node.level, 0) + 1

    summary: dict = {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "lines": len(lines),
        "paragraphs": sum(1 for line in lines if line.strip() and not HEADING.match(line.rstrip())),
        "chars": len(re.sub(r"\s", "", text)),
        "tokens": token_count(text),
        "headings": {f"h{level}": count for level, count in sorted(levels.items())},
        "page_anchors": len(PAGE_MARKER.findall(text)),
        "images": len(re.findall(r"!\[", text)),
        "table_rows": sum(1 for line in lines if line.count("|") >= 2),
        "profile": profile.doc if profile else None,
    }

    if profile is None:
        summary["error"] = "no profile: the importer refuses this document (add data/knowledge/profiles/*.json)"
        return summary

    chunks, dropped = chunk_document(text, profile)
    sizes = sorted(len(chunk.text) for chunk in chunks)
    kinds: dict[str, int] = {}
    for chunk in chunks:
        kinds[chunk.kind] = kinds.get(chunk.kind, 0) + 1
    reasons: dict[str, int] = {}
    for item in dropped:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1

    summary.update(
        {
            "chunks": len(chunks),
            "chunk_kinds": kinds,
            "chunk_chars": sum(sizes),
            "chunk_median": sizes[len(sizes) // 2] if sizes else 0,
            "chunk_min": sizes[0] if sizes else 0,
            "chunk_max": sizes[-1] if sizes else 0,
            "chunks_over_max": sum(1 for size in sizes if size > profile.max_chars),
            "dropped_sections": len(dropped),
            "dropped_reasons": reasons,
            "dropped_chars": sum(item.chars for item in dropped),
            "scenario_chars": sum(len(chunk.text) for chunk in chunks if chunk.kind == "scenario"),
            "reference_chars": sum(len(chunk.text) for chunk in chunks if chunk.kind == "reference"),
            "chunk_list": [
                {
                    "kind": chunk.kind,
                    "page": chunk.page,
                    "chars": len(chunk.text),
                    "section": chunk.section_path,
                    "preview": chunk.text[:60],
                }
                for chunk in chunks
            ],
            "dropped_list": [
                {"section": item.section_path, "reason": item.reason, "chars": item.chars} for item in dropped
            ],
        }
    )
    return summary


def print_document(summary: dict, show_chunks: bool) -> None:
    print(f"\n── {summary['path']}")
    if summary.get("error"):
        print(f"   ✗ {summary['error']}")
        return
    print(
        f"   {summary['chars']} 字 / {summary['lines']} 行 / {summary['paragraphs']} 段"
        f" / {summary['tokens']} tokens（bge 分詞器）"
    )
    print(
        f"   標題 {summary['headings']}  頁碼錨點 {summary['page_anchors']}"
        f"  圖 {summary['images']}  表格行 {summary['table_rows']}"
    )
    print(f"   profile: {summary['profile']}")
    print(
        f"   → chunk {summary['chunks']} 個（{summary['chunk_kinds']}）"
        f" 中位 {summary['chunk_median']} 字、最大 {summary['chunk_max']} 字、超 {600} 字 {summary['chunks_over_max']} 個"
    )
    print(
        f"   scenario {summary['scenario_chars']} 字 / reference {summary['reference_chars']} 字"
        f"（全書 {summary['chars']} 字）"
    )
    print(f"   丟棄 {summary['dropped_sections']} 段 / {summary['dropped_chars']} 字：{summary['dropped_reasons']}")
    if show_chunks:
        print("   ── chunk 一覽")
        for index, item in enumerate(summary["chunk_list"], start=1):
            page = f"p{item['page']}" if item["page"] else "p?"
            print(f"     {index:>3}. [{item['kind']:<9}] {page:>4} {item['chars']:>5} 字  {item['section'][:52]}")
        print("   ── 丟棄明細")
        for item in summary["dropped_list"]:
            print(f"        [{item['reason']:<20}] {item['chars']:>5} 字  {item['section'][:56]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doc", default="", help="only documents whose path contains this text")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--chunks", action="store_true", help="list every kept and dropped section")
    args = parser.parse_args()

    profiles = load_profiles(ROOT / "data" / "knowledge" / "profiles")
    paths = [path for path in documents() if args.doc in str(path)]
    if not paths:
        print(f"no Markdown corpus found under {CORPUS_DIR}")
        return 1

    summaries = [analyze(path, profiles) for path in paths]
    pdfs = pdf_report()

    if args.json:
        print(json.dumps({"documents": summaries, "pdfs": pdfs}, ensure_ascii=False, indent=2))
        return 0

    print(f"== 語料體檢（{len(summaries)} 份 Markdown）==")
    for summary in summaries:
        print_document(summary, args.chunks)

    print("\n== PDF 入口體檢 ==")
    for entry in pdfs:
        if entry.get("note"):
            print(f"   {entry['note']}")
        elif entry.get("error"):
            print(f"   {entry['name'][:60]}  ✗ {entry['error']}")
        else:
            layer = "有文字層" if entry["text_layer"] else "無文字層（需 OCR，無法走 pdf_to_md.py）"
            print(
                f"   {entry['name'][:60]}\n      {entry['mb']} MB / {entry['pages']} 頁"
                f" → 抽樣 {entry['sampled_pages']} 頁共 {entry['sampled_chars']} 字：{layer}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
