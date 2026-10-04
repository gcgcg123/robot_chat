#!/usr/bin/env python
"""OCR a scanned book into the project's Markdown corpus format.

    python scripts/ocr-book.py --pdf data/RAG_data/某某书.pdf --pages 281-305 \
        --out data/RAG_data/md/某某书.md --dpi 150

Why this exists: the two books added on 2026-10-03 are image-only scans (zero extractable
characters on all 251 and 306 pages), so `scripts/pdf_to_md.py` -- which reads a real text
layer with layout geometry -- cannot touch them. This script renders each page and runs a
local ONNX OCR engine (RapidOCR: PP-OCR models on the already-installed onnxruntime, no
torch, no paddle), then rebuilds paragraphs from the text-line boxes.

Output conventions match the existing corpus so the rest of the pipeline works unchanged:

* a standalone ``<sub>pN</sub>`` page marker per page (the importer and chunker both
  understand it, and it beats the table-of-contents page map for precision),
* one paragraph per line,
* candidate headings as ``###`` lines, to be kept or dropped by the document's profile.

RapidOCR is ~10 s/page at 150 DPI on this CPU-only laptop, so `--pages` is the normal way to
run it: read the chapter you need, not all 557 pages.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The progress/report lines carry CJK and box-drawing text; a Windows console defaults to
# cp936 and would raise UnicodeEncodeError once the output is redirected.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pypdfium2 as pdfium  # noqa: E402

PAGE_NUMBER_CHARS = "0123456789·•.-—– "
HEADER_BAND = 0.09      # top 9% of the page is a running head
FOOTER_BAND = 0.91      # bottom 9% holds the printed page number

# Measured on the two scanned books: RapidOCR's most frequent mistakes are these, and each is
# safe to fix because the wrong form is not a word ("进人/深人" etc.). Everything is a
# context-bound bigram, never a blanket character swap.
CONFUSIONS = (
    ("进人", "进入"), ("深人", "深入"), ("加人", "加入"), ("列人", "列入"), ("收人", "收入"),
    ("投人", "投入"), ("纳人", "纳入"), ("步人", "步入"), ("融人", "融入"), ("介人", "介入"),
    ("人眠", "入眠"), ("人睡", "入睡"), ("人学", "入学"), ("人住", "入住"),
    ("自已", "自己"), ("作票", "作祟"),
    ("-些", "一些"), ("-系列", "一系列"), ("-个", "一个"), ("-直", "一直"), ("-定", "一定"),
    ("-般", "一般"), ("-样", "一样"), ("-起", "一起"), ("-方", "一方"), ("-部分", "一部分"),
    ("·一下", "一下"), ("·一定", "一定"), ("·一些", "一些"), ("坚持不解", "坚持不懈"),
)

# Decoration characters the recogniser emits for a single "一" (the books use it constantly).
_DASH_RUNS = __import__("re").compile(r"[-—–·]{1,}")
_NUMBERED = __import__("re").compile(r"[（(]\s*(\d{1,2})\s*[)）]")


def fix_confusions(text: str) -> str:
    """Clean the systematic OCR mistakes measured on these two books.

    Substitutions only: a blanket character swap would corrupt real words, so every entry is a
    bigram (or longer) whose wrong form is not a word. The dash handling collapses the runs the
    recogniser produces in place of "一" and then repairs the doubles it can create, and numbered
    items are normalised to full-width parentheses because the scans mix both styles.
    """

    for wrong, right in CONFUSIONS:
        text = text.replace(wrong, right)
    if _DASH_RUNS.search(text):
        text = _DASH_RUNS.sub("一", text)
        for double in ("一一个", "一一些", "一一直", "一一定", "一一般", "一一样"):
            text = text.replace(double, double[1:])
    text = _NUMBERED.sub(r"（\1）", text)
    return text


def parse_pages(spec: str, total: int) -> list[int]:
    """1-based page selection: "281-305", "12,60,150", "7-" or "" (= all)."""

    if not spec:
        return list(range(1, total + 1))
    selected: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            first = int(start) if start else 1
            last = int(end) if end else total
            selected.extend(range(first, min(last, total) + 1))
        else:
            selected.append(int(part))
    return [page for page in dict.fromkeys(selected) if 1 <= page <= total]


def printed_page(lines: list[dict], height: float) -> int | None:
    """The printed page number, read from the footer (or header) band."""

    for line in sorted(lines, key=lambda item: item["y"], reverse=True) + sorted(lines, key=lambda item: item["y"]):
        in_band = line["y"] > height * FOOTER_BAND or line["y"] < height * HEADER_BAND
        if not in_band or len(line["text"]) > 12:
            continue
        digits = ""
        for char in line["text"]:
            if char.isdigit():
                digits += char
            elif digits:
                break
        if digits and len(digits) <= 3:
            return int(digits)
    return None


def to_paragraphs(
    lines: list[dict],
    height: float,
    width: float,
    *,
    running_heads: set[str] | None = None,
    headings: bool = True,
) -> tuple[list[str], int | None]:
    """Rebuild paragraphs from text lines using the boxes' geometry.

    Three things are handled here because OCR alone gets all three wrong:

    * the printed page number (footer band) is read out and also returned, so the caller can
      check the PDF→printed offset instead of guessing it;
    * running heads (the chapter title repeated at the top of every page) are removed using
      ``running_heads``, which the caller builds by counting the top band across pages;
    * a heading is only claimed when the line is short, free of sentence punctuation, and
      either centred or preceded by an extra gap **and** the previous line ended a sentence.
      Without that last condition a wrapped sentence hands its tail ("重要原因") a heading.
    """

    running_heads = running_heads or set()
    number = printed_page(lines, height)
    body = []
    for line in lines:
        text = line["text"].strip()
        if not text or not (HEADER_BAND * height <= line["y"] <= FOOTER_BAND * height):
            continue
        if text in running_heads:
            continue
        body.append(line)
    if not body:
        return [], number

    body.sort(key=lambda item: (round(item["y"] / 6), item["x"]))
    left = min(line["x"] for line in body)
    char_widths = [line["width"] / max(1, len(line["text"])) for line in body if line["text"]]
    char_width = statistics.median(char_widths) if char_widths else 12.0
    # Paragraph break detection uses the line *pitch*, not the whitespace between boxes:
    # detection boxes often overlap slightly, which makes the median whitespace ~0 and would
    # turn every line into its own paragraph (measured: that is exactly what happened).
    pitches = [body[index]["y"] - body[index - 1]["y"] for index in range(1, len(body))]
    pitch = statistics.median([value for value in pitches if value > 0]) if pitches else 12.0
    sentence_end = "。！？；.!?;：:"

    paragraphs: list[str] = []
    buffer = ""
    previous_y = None
    previous_ended_sentence = True
    for line in body:
        text = fix_confusions(line["text"].strip())
        indented = (line["x"] - left) > 0.8 * char_width
        spaced = previous_y is not None and (line["y"] - previous_y) > 1.35 * pitch
        centered = abs((line["x"] + line["width"] / 2) - width / 2) < width * 0.06
        short = len(text) <= 18 and not any(mark in text for mark in "。！？；，、：")

        if buffer and (indented or spaced):
            paragraphs.append(buffer.strip())
            buffer = ""
        if not buffer and headings and short and previous_ended_sentence and (centered or spaced):
            paragraphs.append(f"### {text}")
            previous_y = line["y"]
            previous_ended_sentence = True
            continue
        buffer = f"{buffer}{text}" if buffer else text
        previous_y = line["y"]
        previous_ended_sentence = text.endswith(tuple(sentence_end))
    if buffer.strip():
        paragraphs.append(buffer.strip())
    return [item for item in paragraphs if item], number


def find_running_heads(pages: list[list[dict]], height: float, minimum: int = 3) -> set[str]:
    """Text repeated in the top band of several pages is a running head, not content."""

    counts: dict[str, int] = {}
    for lines in pages:
        seen = {
            line["text"].strip()
            for line in lines
            if line["text"].strip() and line["y"] < height * (HEADER_BAND * 1.6)
        }
        for text in seen:
            counts[text] = counts.get(text, 0) + 1
    return {text for text, count in counts.items() if count >= minimum and len(text) <= 30}


def ocr_pdf(
    pdf_path: Path,
    pages: list[int],
    dpi: int,
    quiet: bool,
    offset: int | None = None,
    headings: bool = True,
    threads: int = 4,
) -> tuple[list[str], list[dict]]:
    from rapidocr_onnxruntime import RapidOCR

    # Threads are capped by default: the OCR engine's config asks for -1 (all cores), and the
    # running service already claims ASR_CPU_THREADS=8 on the same 4-core CPU, so an
    # unlimited OCR pass would slow voice input down while a turn is in flight.
    engine = RapidOCR(intra_op_num_threads=threads)
    document = pdfium.PdfDocument(str(pdf_path))
    markdown: list[str] = []
    report: list[dict] = []
    try:
        # Pass 1: read every selected page, keeping the raw text lines so the running head can
        # be identified from repetition before any paragraph is built.
        raw: list[tuple[int, list[dict], float, float]] = []
        timings: dict[int, float] = {}
        for page_number in pages:
            page = document[page_number - 1]
            image = page.render(scale=dpi / 72).to_pil()
            width, height = image.size
            started = time.perf_counter()
            result, _elapsed = engine(image)
            timings[page_number] = time.perf_counter() - started
            lines = []
            for box, text, score in result or []:
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                lines.append(
                    {
                        "text": str(text),
                        "score": float(score),
                        "x": min(xs),
                        "y": min(ys),
                        "width": max(xs) - min(xs),
                        "height": max(ys) - min(ys),
                    }
                )
            raw.append((page_number, lines, width, height))
            if not quiet:
                print(f"  p{page_number:>4} 讀取完成 {len(lines):>3} 行 {timings[page_number]:5.1f}s")

        heights = [item[3] for item in raw]
        running_heads = find_running_heads([item[1] for item in raw], statistics.median(heights))
        if running_heads and not quiet:
            print(f"  頁眉（重複 {3}+ 次，已移除）：{sorted(running_heads)}")

        detected = [item for item in (printed_page(lines, height) for _n, lines, _w, height in raw) if item]
        guessed = None
        if detected:
            by_page = [page_number - printed for (page_number, _l, _w, _h), printed in zip(raw, detected)]
            if by_page:
                guessed = statistics.mode(by_page)
        effective = offset if offset is not None else (guessed or 0)
        if not quiet:
            print(f"  偏移：{'指定' if offset is not None else '偵測'} +{effective}（頁腳樣本 {len(detected)}）")

        # Pass 2: build the Markdown, with the printed page number on every marker so a
        # citation always points at the page a reader can find. A paragraph that runs to the
        # foot of a page is carried into the next page (the PDF only indents the first line of
        # a paragraph, so page-local OCR splits sentences otherwise -- the same carry rule
        # scripts/pdf_to_md.py uses for the text-layer corpus).
        carry = ""
        for (page_number, lines, width, height) in raw:
            paragraphs, printed = to_paragraphs(
                lines, height, width, running_heads=running_heads, headings=headings
            )
            if carry:
                if paragraphs and not paragraphs[0].startswith("###"):
                    paragraphs[0] = f"{carry}{paragraphs[0]}"
                else:
                    paragraphs.insert(0, carry)
                carry = ""
            if paragraphs and not paragraphs[-1].startswith("###") and not paragraphs[-1].endswith(tuple("。！？；.!?;”」』）")):
                carry = paragraphs.pop()
            shown = page_number - effective
            if printed is not None and printed != shown and not quiet:
                print(f"  ⚠ p{page_number}: 頁腳讀到 {printed}，偏移推得 {shown}")
            markdown.append(f"<sub>p{shown}</sub>")
            markdown.extend(paragraphs)
            markdown.append("")
            report.append(
                {
                    "pdf_page": page_number,
                    "printed_page": shown,
                    "footer_page": printed,
                    "offset": effective,
                    "lines": len(lines),
                    "paragraphs": len(paragraphs),
                    "chars": sum(len(p) for p in paragraphs),
                    "seconds": round(timings.get(page_number, 0.0), 1),
                    "mean_score": round(statistics.fmean([l["score"] for l in lines]), 3) if lines else None,
                    "page_size": [width, height],
                }
            )
    finally:
        document.close()
    return markdown, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--pages", default="", help='1-based selection: "281-305", "12,60", "7-" or "" for all')
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--offset", type=int, default=None, help="PDF page minus printed page; detected from the footers when omitted")
    parser.add_argument("--no-headings", action="store_true", help="emit no ### headings (safest for prose-only chapters)")
    parser.add_argument("--threads", type=int, default=4, help="ONNX Runtime intra-op threads (default 4; the service shares this CPU)")
    parser.add_argument("--out", type=Path, default=None, help="Markdown output (default: alongside the PDF, md/ subdir)")
    parser.add_argument("--report", type=Path, default=None, help="write the per-page report as JSON")
    parser.add_argument("--dry-run", action="store_true", help="report page size/offset only, no OCR")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    pdf_path = args.pdf if args.pdf.is_absolute() else (ROOT / args.pdf)
    if not pdf_path.is_file():
        print(f"no such PDF: {pdf_path}")
        return 1

    document = pdfium.PdfDocument(str(pdf_path))
    total = len(document)
    document.close()
    pages = parse_pages(args.pages, total)
    print(f"  {pdf_path.name[:50]}")
    print(f"  {total} 頁；本次處理 {len(pages)} 頁（{pages[0]}–{pages[-1]}）  DPI {args.dpi}")

    if args.dry_run:
        return 0

    markdown, report = ocr_pdf(
        pdf_path,
        pages,
        args.dpi,
        args.quiet,
        offset=args.offset,
        headings=not args.no_headings,
        threads=args.threads,
    )
    offsets = [item["offset"] for item in report]
    if offsets:
        print(f"  偏移 +{statistics.mode(offsets)}")

    out = args.out
    if out is None:
        out = ROOT / "data" / "RAG_data" / "md" / f"{pdf_path.stem}.md"
    elif not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(markdown), encoding="utf-8")
    characters = sum(item["chars"] for item in report)
    seconds = sum(item["seconds"] for item in report)
    try:
        shown = out.relative_to(ROOT)
    except ValueError:  # an explicit --out outside the checkout
        shown = out
    print(f"  → {shown}  {characters} 字  {len(report)} 頁  {seconds / 60:.1f} 分鐘")
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
