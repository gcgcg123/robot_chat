#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verify a Markdown conversion against its source PDF.

Checks that no body content was lost, that every referenced figure exists, and
that icon-font glyphs were removed. Lines the converter drops on purpose
(running headers, page numbers, the printed table of contents, the cover
metadata table and the worksheet table labels) are classified separately so any
genuinely lost content stands out.

Usage
-----
    python scripts/verify_md_output.py <source.pdf> <output.md>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import fitz

RUNNING_HEADERS = {"老年人沟通技能指导手册", "项目1 沟通技巧的基础", "目  录"}
TOC_LEADER_RE = re.compile(r"[.…]{4,}")
TOC_ENTRY_RE = re.compile(r"^(项目|任务)\s*\d+")
TOC_PAGE_MAX = 4
# Worksheet tables of 四、案例练习, printed as loose cells.
TABLE_CELLS = {
    "发送者", "信息", "媒介", "接收者", "如何避免沟通噪音",
    "第一个沟通过程(与班主任)", "第二个沟通过程(与养老院负责人)",
    "第三个沟通过程(向班主任回馈)", "续 表",
}
# Cover scaffolding the converter replaces with front matter.
WORDMARK = {"东北", "师范", "大", "学出", "版社"}
COVER_META = {"图书特色：", "图书简介："}


def is_pua(ch: str) -> bool:
    code = ord(ch)
    return (
        (0xE000 <= code <= 0xF8FF)
        or (0xF0000 <= code <= 0xFFFFD)
        or (0x100000 <= code <= 0x10FFFD)
    )


def pdf_lines(pdf: Path) -> list[tuple[int, str]]:
    """(page, text) for every body line of the PDF."""
    rows: list[tuple[int, str]] = []
    with fitz.open(pdf) as doc:
        for number, page in enumerate(doc, start=1):
            for block in page.get_text("rawdict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for raw in block["lines"]:
                    text = "".join(c["c"] for s in raw["spans"] for c in s["chars"])
                    text = "".join(ch for ch in text if not is_pua(ch)).strip()
                    if not text:
                        continue
                    y0, y1 = raw["bbox"][1], raw["bbox"][3]
                    if y1 < 66 or y0 > 693:
                        continue
                    fonts = [s["font"].split("+")[-1] for s in raw["spans"]]
                    if "E-HD" in fonts and len(text) <= 4 and text.isdigit():
                        continue
                    if text in RUNNING_HEADERS and y0 < 70:
                        continue
                    if number <= TOC_PAGE_MAX and (
                        TOC_LEADER_RE.search(text) or text.isdigit()
                    ):
                        continue
                    rows.append((number, text))
    return rows


def classify(text: str, page: int) -> str:
    if text in TABLE_CELLS:
        return "table-cell"
    if text in WORDMARK or text in COVER_META:
        return "cover-meta"
    if page <= TOC_PAGE_MAX and TOC_ENTRY_RE.match(text):
        return "printed-toc"
    if re.fullmatch(r"\d\.\S+", text) and page == 1:
        return "cover-meta"
    if re.fullmatch(r"[书名主编定价]+", text):
        return "cover-meta"
    # The cover prints some field names as two runs ("书" + "名：老年人…"), so a
    # bare label character or a "名：…" tail is still cover metadata.
    if len(text) == 1 and text in "书名主编定价":
        return "cover-meta"
    if re.match(r"^(名|号|编|价|书号|图书性质|图书特色)[:：]", text):
        return "cover-meta"
    if re.fullmatch(r"\d+\.\d+\s*元", text) or text.startswith("978-"):
        return "cover-meta"
    return "body"


PUNCTUATION = "，。、；：！？（）【】《》“”,.;:!?()[]<>\"'`|*_ \u3000"


def squash(text: str) -> str:
    """Reduce text to its content characters for comparison.

    Punctuation, emphasis markers and every kind of space are removed on both
    sides, so a line counts as kept when its words survive even though the
    converter normalises punctuation and block layout.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"^#{1,6} ", "", text, flags=re.M)
    return "".join(ch for ch in text if ch not in PUNCTUATION and ch != "\n")


def normalise(markdown: str) -> str:
    return squash(markdown)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path)
    parser.add_argument("markdown", type=Path)
    args = parser.parse_args(argv)

    markdown = args.markdown.read_text(encoding="utf-8")
    flat = normalise(markdown)

    kinds = ("body", "table-cell", "cover-meta", "printed-toc")
    stats: dict[str, list[str]] = {kind: [] for kind in kinds}
    for page, text in pdf_lines(args.pdf):
        kind = classify(text, page)
        prefix = "KEPT" if squash(text) in flat else "MISS"
        stats[kind].append(f"{prefix} p{page} {text}")

    for kind in kinds:
        entries = stats[kind]
        missed = [e for e in entries if e.startswith("MISS")]
        print(f"{kind:12s}: {len(entries) - len(missed)}/{len(entries)} kept")
        for entry in missed[:10]:
            print(f"    {entry}")

    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
    missing_files = [r for r in refs if not (args.markdown.parent / r).exists()]
    print(f"figures     : {len(refs)} referenced, {len(missing_files)} missing")

    pua = sorted({ch for ch in markdown if is_pua(ch)})
    print(f"PUA glyphs  : {len(pua)}")

    body_missed = [e for e in stats["body"] if e.startswith("MISS")]
    print()
    if body_missed or missing_files or pua:
        print("VERIFICATION FAILED")
        return 1
    print("No body content lost. VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
