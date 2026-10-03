#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Convert textbook PDFs into structured Markdown for the RAG corpus.

The converter is written for Chinese textbook PDFs whose layout carries the
document structure in the character formatting:

* running headers / footers and page numbers are dropped by geometry,
* headings are recognised from font family, size and colour,
* body paragraphs are rebuilt from the paragraph-indent convention
  (a new paragraph starts with a two-character indent),
* decorative artwork (corner leaves, sidebars, underlines, footers) is
  discarded, while real figures are exported next to the Markdown and
  embedded at the point where they appear in the flow,
* every heading carries a machine-readable provenance comment
  (``<!-- src: p5-7 | 项目1 / 任务1 -->``) so chunkers can attach
  document position metadata.

Usage
-----
    python scripts/pdf_to_md.py <pdf-or-directory> [-o OUT_DIR] [--no-images]

Examples
--------
    python scripts/pdf_to_md.py data/RAG_data
    python scripts/pdf_to_md.py data/RAG_data/book.pdf -o data/RAG_data/md
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - dependency guidance
    sys.exit("PyMuPDF is required: python -m pip install pymupdf")

# --------------------------------------------------------------------------
# Layout constants (points). The source book uses a 541x754pt page with a
# 65.6pt left margin, an 89.7pt paragraph indent, 20.3pt line advance and
# headers/footers outside the 66..692pt band.
# --------------------------------------------------------------------------
BODY_TOP = 66.0          # running header sits above this
BODY_BOTTOM = 693.0      # footer art / page numbers sit below this
INDENT_MIN = 80.0        # x0 above this means "new paragraph"
PARA_INDENT = 24.0       # nominal two-character indent, in points
COLOR_CYAN = 0x00AEEF    # accent colour used for every heading in this book
HEADING_MAX_CHARS = 34   # headings are short; longer text is a sentence
PUA_RANGES = (                       # Unicode private-use areas (icon fonts)
    (0xE000, 0xF8FF),                # BMP private use
    (0xF0000, 0xFFFFD),              # plane 15 private use
    (0x100000, 0x10FFFD),            # plane 16 private use
)

CYAN_HEADING_FONTS = (
    "FZDBSK",  # banner title
    "FZXBSK",  # task title
    "FZHTK",   # numbered section title
    "FZY3K",   # numbered item title
    "E-YT1",   # numbered item title
)

SECTION_FONTS = ("FZHTK", "FZLSK")        # 一、二、… and 学习目标/知识学习
NUMBERED_ITEM_RE = re.compile(r"^[1-9]\d?[.、]\s*\S")
PAREN_ITEM_RE = re.compile(r"^[(（][1-9]\d?[)）]\s*\S")
SUB_ITEM_RE = re.compile(r"^[①-⑳]")
OPTION_RE = re.compile(r"^[A-DＡ-Ｄ][.、]")     # multiple-choice answer options
# A line holding only punctuation/spacing: a fill-in-the-blank rule in the
# exercises. These never start a paragraph; they extend the current premise.
PUNCT_ONLY_RE = re.compile(r"^[\s,，。.、;；:：()（）]*$")
# Line classes returned by Converter.line_type.
H2, H3, H4, H5 = "h2", "h3", "h4", "h5"
HEADING_KINDS = (H2, H3, H4, H5)
BODY, ITEM = "body", "item"
FIGURE_LINE, CONSUMED = "figure", "consumed"
OBJECTIVES, OPTION_LINE = "objectives", "option"
TABLE_ROW = "table"
# Geometry of the 学习目标 box: a narrow label column plus wrapped bullet items.
OBJECTIVES_BOX_HEIGHT = 200.0
OBJECTIVE_COLUMN_MAX = 150.0
OBJECTIVE_LABEL_SIZE = 10.5
OBJECTIVE_BOUNDARY_SLACK = 2.0  # dead zone around a label boundary
# Characters that close a sentence; a page ending on one of these ends its paragraph.
SENTENCE_END = frozenset("。！？…；:：\"''）)】》」』")
TOC_LEADER_RE = re.compile(r"[.…]{4,}")
TOC_ENTRY_RE = re.compile(r"^(项目|任务)\s*\d+")
TOC_PAGE_MAX = 4         # printed table of contents lives on the first pages
PAGE_NUMBER_FONTS = ("E-HD",)
COVER_TABLE_TOP = 390.0  # cover metadata table starts below the title artwork
COVER_TABLE_REPEAT_TOP = 530.0  # page 2 repeats that table below the 图书简介
ROW_TOLERANCE = 3.0      # lines within this many points share a cover table row
LABEL_COLUMN_MAX = 110.0  # a cover label starts left of this
CELL_GAP = 5.0           # horizontal gap that separates two cover cells
BOOK_LABEL_RE = re.compile(r"^(书名|书号|主编|定价|图书性质|图书特色)$")
BOOK_VALUE_RE = re.compile(r"^(书名|书号|主编|定价|图书性质|图书特色)[:：]?\s*(.*)$")

# ---------------------------------------------------------------- formatting --
# PDF text carries the spacing of a print layout, which looks wrong in Markdown.
SPACE_AFTER_OPEN_RE = re.compile(r"([（(【《“‘])\s+")
CJK = r"\u4e00-\u9fff"
CJK_SPACE_RE = re.compile(rf"(?<=[{CJK}])\s+(?=[{CJK}])")
ASCII_PUNCT_RE = re.compile(r"[,;:!?]")
ASCII_TO_CJK = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
DUPLICATE_PUNCT_RE = re.compile(r"([。，])\1+")
# "图1-4-1 ，" — a space before Chinese punctuation. A space before a sentence
# mark is always a print artefact; before a comma it is only safe after CJK.
SPACE_BEFORE_CJK_PUNCT_RE = re.compile(r"(?<=\S)\s+(?=[。！？])|(?<=[0-9A-Za-z\u4e00-\u9fff])\s+(?=[，；：、])")
# "怎么去呀？ 老师" — justification space after full-width ? or ! before Chinese.
SPACE_AFTER_CJK_END_RE = re.compile(rf"(?<=[？！])\s+(?=[{CJK}])")
# An empty ASCII answer stub between Chinese characters, e.g. "包括()。".
EMPTY_PAREN_RE = re.compile(rf"(?<=[{CJK}])\(\s*\)(?=[{CJK}。，])")
# A printed blank before a full stop prints as a bare space: "发送者与 。".
TRAILING_BLANK_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+[。.]")
# A printed fill-in-the-blank line has no text of its own, so it is rendered as
# an explicit blank; consecutive such lines collapse to a single blank.
BLANK_MARK = "____"
BLANK_MARK_RUN_RE = re.compile(r"_{2,}")
# An empty answer stub "( )" (possibly with trailing punctuation) ends a question.
ANSWER_STUB_RE = re.compile(r"[(（]\s*[)）][.。]?")
# A sentence introducing a fill-in-the-blank line ("请列举三项沟通内容:").
BLANK_LEAD_RE = re.compile(r"[:：]$")
# Answer-option line, e.g. "A.沟通主体".
OPTION_LINE_RE = re.compile(r"^([A-DＡ-Ｄ])[.、]\s*(.+)$")
# Question stem that expects options: "6.沟通过程的要素包括( )。" or "...有(  )"
QUESTION_RE = re.compile(r"[(（]\s*[)）]")
# Worksheet tables of 四、案例练习.
TABLE_DESIGNATOR_RE = re.compile(r"^第[一二三四五六七]个沟通过程")
TABLE_HEADERS = ("发送者", "信息", "媒介", "接收者", "如何避免沟通噪音")
# Cover bullet list of the book's selling points.
COVER_BULLET_RE = re.compile(r"^\d\.\S")


@dataclass
class Line:
    """One visual text line with the formatting we need for classification."""

    text: str
    x0: float
    y0: float
    y1: float
    size: float
    fonts: tuple[str, ...]
    colors: tuple[int, ...]
    page: int
    x1: float = 0.0
    starts_page: bool = False

    @property
    def has_cyan(self) -> bool:
        return COLOR_CYAN in self.colors

    @property
    def is_indented(self) -> bool:
        return self.x0 > INDENT_MIN

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def is_standalone(self) -> bool:
        return self.y1 < BODY_TOP or self.y0 > BODY_BOTTOM


@dataclass
class Figure:
    xref: int
    page: int
    bbox: tuple[float, float, float, float]
    name: str = ""
    rel_path: str = ""


@dataclass
class Objectives:
    """The 学习目标 box: a category label plus its bullet items."""

    groups: list[tuple[str, list[str]]] = field(default_factory=list)

    def as_pairs(self) -> list[tuple[str, list[str]]]:
        return self.groups


@dataclass
class Block:
    """A rendered element in reading order."""

    kind: str            # heading | paragraph | figure | options | objectives | table
    text: str = ""
    level: int = 0
    page: int = 0
    figure: Figure | None = None
    items: list[str] = field(default_factory=list)
    options: list[tuple[str, str]] = field(default_factory=list)
    rows: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class Converter:
    pdf_path: Path
    out_dir: Path
    images_dir: Path | None = None
    emit_provenance: bool = True
    blocks: list[Block] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    toc: list[tuple[int, str, int]] = field(default_factory=list)
    cover_fields: list[tuple[str, str]] = field(default_factory=list)
    cover_notes: list[str] = field(default_factory=list)
    cover_bullets: list[str] = field(default_factory=list)
    project: str = ""
    task: str = ""
    section: str = ""
    title: str = ""
    pending_paragraph: str = ""
    pending_block: Block | None = None
    warnings: list[str] = field(default_factory=list)

    # Section titles at the task level; numbered lists below them nest one deeper.
    TASK_SECTIONS: ClassVar[tuple[str, ...]] = (
        "学习目标",
        "任务描述",
        "知识学习",
        "实训演练",
        "课后练习",
    )

    # ---------------------------------------------------------------- text --
    @staticmethod
    def is_icon_glyph(ch: str) -> bool:
        """True for private-use code points: icon-font glyphs, not text."""
        code = ord(ch)
        return any(low <= code <= high for low, high in PUA_RANGES)

    @classmethod
    def clean_text(cls, raw: str) -> str:
        """Drop icon-font private-use glyphs and normalise whitespace."""
        text = "".join(ch for ch in raw if not cls.is_icon_glyph(ch))
        text = text.replace("\u3000", " ")
        text = re.sub(r"[^\S\n]+", " ", text)
        return text.strip()

    @staticmethod
    def tidy(text: str) -> str:
        """Remove print-layout spacing that looks wrong in Markdown.

        The PDF spaces text for justification, so runs arrive as
        "发送者与 。" or "沟通内容: , ,。". Chinese typesetting has no spaces
        there, and CJK does not need a space between characters. ASCII
        punctuation between Chinese characters is normalised to full width.
        """
        text = SPACE_AFTER_OPEN_RE.sub(r"\1", text)
        text = CJK_SPACE_RE.sub("", text)
        text = DUPLICATE_PUNCT_RE.sub(r"\1", text)
        text = ASCII_PUNCT_RE.sub(Converter._ascii_to_cjk, text)
        text = EMPTY_PAREN_RE.sub("（　）", text)
        # A blank printed before a full stop is a space in the PDF, so normalise
        # it before the generic spacing rules remove the space.
        text = TRAILING_BLANK_RE.sub(f" {BLANK_MARK}。", text)
        text = SPACE_BEFORE_CJK_PUNCT_RE.sub("", text)
        text = SPACE_AFTER_CJK_END_RE.sub("", text)
        # Collapse the blank rules emitted for fill-in-the-blank lines.
        text = BLANK_MARK_RUN_RE.sub(BLANK_MARK, text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    @staticmethod
    def _ascii_to_cjk(match: "re.Match[str]") -> str:
        """Full-width the ASCII punctuation that sits between Chinese characters."""
        text, start, end = match.string, match.start(), match.end()
        before = text[start - 1] if start else ""
        after = text[end] if end < len(text) else ""
        if Converter.is_cjk(before) or Converter.is_cjk(after):
            return ASCII_TO_CJK[match.group(0)]
        return match.group(0)

    @staticmethod
    def is_cjk(ch: str) -> bool:
        return bool(ch) and ("\u4e00" <= ch <= "\u9fff" or ch in "，。、；：！？（）【】《》“”")

    def read_lines(self) -> dict[int, list[Line]]:
        pages: dict[int, list[Line]] = {}
        with fitz.open(self.pdf_path) as doc:
            if not self.title:
                self.title = self.infer_title(doc)
            for page_index, page in enumerate(doc):
                page_number = page_index + 1
                lines: list[Line] = []
                for block in page.get_text("rawdict")["blocks"]:
                    if block.get("type") != 0:
                        continue
                    for raw_line in block["lines"]:
                        raw = "".join(
                            char["c"]
                            for span in raw_line["spans"]
                            for char in span["chars"]
                        )
                        text = self.clean_text(raw)
                        if not text:
                            continue
                        fonts = tuple(
                            span["font"].split("+")[-1] for span in raw_line["spans"]
                        )
                        if any(font in PAGE_NUMBER_FONTS for font in fonts) and len(text) <= 4:
                            continue  # page number, exploded into digits
                        sizes = [span["size"] for span in raw_line["spans"]]
                        line = Line(
                            text=text,
                            x0=raw_line["bbox"][0],
                            y0=raw_line["bbox"][1],
                            y1=raw_line["bbox"][3],
                            size=max(sizes),
                            fonts=fonts,
                            colors=tuple(span["color"] for span in raw_line["spans"]),
                            page=page_number,
                            x1=raw_line["bbox"][2],
                        )
                        if line.is_standalone:
                            continue  # running header / footer art
                        lines.append(line)
                lines.sort(key=lambda item: (round(item.y0, 1), item.x0))
                if lines:
                    lines[0].starts_page = True
                pages[page_number] = lines
        return pages

    def infer_title(self, doc: "fitz.Document") -> str:
        """Prefer the cover's 书名 field, then the PDF's own title, then filename.

        Note the publisher wordmark ("东北师范大学出版社") is set larger than the
        book title on the cover, so "largest text wins" would pick the wrong one.
        """
        for page_number in (1, 2):
            fields = self.read_cover_fields(
                self.read_page_lines(doc, page_number)
            )
            for label, value in fields:
                if label == "书名" and value:
                    return value
        meta_title = (doc.metadata or {}).get("title", "").strip()
        if meta_title and not meta_title.isascii():
            return meta_title
        # The distributor filename appends a promo suffix after a full-width
        # hyphen, so keep only the part that names the book.
        return re.split(r"[-\u2010-\u2015\uff0d]", self.pdf_path.stem)[0].strip()

    @staticmethod
    def read_page_lines(doc: "fitz.Document", page_number: int) -> list[Line]:
        """Minimal line list for the cover pages (used only for title/fields)."""
        lines: list[Line] = []
        for block in doc[page_number - 1].get_text("rawdict")["blocks"]:
            if block.get("type") != 0:
                continue
            for raw_line in block["lines"]:
                text = Converter.clean_text(
                    "".join(
                        char["c"]
                        for span in raw_line["spans"]
                        for char in span["chars"]
                    )
                )
                if text:
                    lines.append(
                        Line(
                            text=text,
                            x0=raw_line["bbox"][0],
                            y0=raw_line["bbox"][1],
                            y1=raw_line["bbox"][3],
                            size=max(span["size"] for span in raw_line["spans"]),
                            fonts=tuple(
                                span["font"].split("+")[-1] for span in raw_line["spans"]
                            ),
                            colors=tuple(
                                span["color"] for span in raw_line["spans"]
                            ),
                            page=page_number,
                            x1=raw_line["bbox"][2],
                        )
                    )
        lines.sort(key=lambda item: (round(item.y0, 1), item.x0))
        return lines

    # ----------------------------------------------------------- structure --
    def line_type(self, line: Line) -> str:
        """Classify a line as HEADING, ITEM, BODY or DROP.

        Headings are recognised from the book's typography (cyan accent, larger
        section fonts). Numbered list entries that are *not* headings are
        reported as ITEM so that they still start their own paragraph instead of
        running on after the previous sentence.
        """
        if len(line.text) > HEADING_MAX_CHARS:
            return BODY
        base = line.fonts[0].split("-")[0]
        size = line.size
        if base == "FZDBSK" or (line.has_cyan and (size >= 16 or base == "E-HZ" and size >= 16)):
            return H2  # 项目 banner
        if base == "FZXBSK" or (line.has_cyan and (size >= 14 or base == "E-HZ" and size >= 14)):
            return H3  # 任务 banner
        if base in SECTION_FONTS and (size >= 12 or line.has_cyan):
            # 一、二、… / 学习目标 / 知识学习 / 课后练习. The task sections are
            # 13.1pt black while the in-task ones are 11.2pt cyan.
            return H4
        if base in ("FZY3K", "E-YT1") and line.has_cyan and NUMBERED_ITEM_RE.match(line.text):
            return H5  # 1.沟通主体
        if (
            line.has_cyan
            and base in ("FZSSK", "E-FZ", "E-BZ")
            and (NUMBERED_ITEM_RE.match(line.text) or PAREN_ITEM_RE.match(line.text))
        ):
            return H5
        if SUB_ITEM_RE.match(line.text) or NUMBERED_ITEM_RE.match(line.text):
            return ITEM  # exercise numbering, blanks, sub-points
        if OPTION_RE.match(line.text):
            return ITEM  # answer option; must not run into the previous one
        return BODY

    # -------------------------------------------------------------- blocks --
    def build_blocks(self, pages: dict[int, list[Line]]) -> None:
        for page_number in sorted(pages):
            lines = pages[page_number]
            if not lines:
                continue
            if page_number == 1:
                self.emit_cover(page_number, lines)
                continue
            if page_number <= TOC_PAGE_MAX and self.is_toc_page(lines):
                continue  # printed table of contents; rebuilt from headings
            if page_number == 2:
                for note in self.collect_notes(lines):
                    self.blocks.append(Block(kind="paragraph", text=note, page=2))
                continue
            self.emit_page(page_number, lines)
        if self.pending_block is not None:
            # The document ends on a paragraph with no sentence-final mark.
            self.blocks.append(self.pending_block)
            self.pending_block, self.pending_paragraph = None, ""

    @staticmethod
    def is_toc_page(lines: list[Line]) -> bool:
        """Pages 3-4 are the printed contents: entries with dot leaders."""
        leaders = sum(1 for line in lines if TOC_LEADER_RE.search(line.text))
        return leaders >= 3

    def emit_cover(self, page_number: int, lines: list[Line]) -> None:
        """Cover page: read the structured fields, ignore the wordmark artwork."""
        for figure in self.collect_figures(page_number):
            self.figures.append(figure)
            self.blocks.append(
                Block(kind="figure", text=figure.name, page=page_number, figure=figure)
            )
        for label, value in self.read_cover_fields(lines):
            self.cover_fields.append((label, value))
        self.cover_bullets = [
            self.tidy(line.text)
            for line in lines
            if line.y0 >= COVER_TABLE_TOP and COVER_BULLET_RE.match(line.text)
        ]
        self.cover_notes = self.collect_notes(lines)
        for note in self.cover_notes:
            self.blocks.append(Block(kind="paragraph", text=note, page=page_number))

    @staticmethod
    def flatten(spaced: str) -> tuple[str, dict[int, int]]:
        """Return the text without spaces plus a flat-index -> spaced-index map."""
        flat_chars: list[str] = []
        index_map: dict[int, int] = {}
        for position, ch in enumerate(spaced):
            if ch == " ":
                continue
            index_map[len(flat_chars)] = position
            flat_chars.append(ch)
        index_map[len(flat_chars)] = len(spaced)
        return "".join(flat_chars), index_map

    @staticmethod
    def join_cells(cells: list[Line]) -> str:
        """Join a cover row's runs, keeping a space where the cells are far apart.

        A split run ("书" + "名：…") sits about 14pt from its value, whereas
        separately typeset names on one row sit further apart; preserving that
        gap is what makes the editor list recoverable ("孙青" + "钱永霞").
        """
        text = ""
        previous_x1: float | None = None
        for cell in sorted(cells, key=lambda item: item.x0):
            if previous_x1 is not None and cell.x0 - previous_x1 > CELL_GAP:
                text += " "
            text += cell.text
            previous_x1 = cell.x0 + cell.width
        return text

    @classmethod
    def read_cover_fields(cls, lines: list[Line]) -> list[tuple[str, str]]:
        """Rebuild the cover's metadata table row by row.

        The cover is drawn cell by cell: a label near the left margin ("书" and
        "名：…" come as two separate runs) and its value in a column to the
        right. Rows are found by y-proximity, then the label is the leftmost
        line and the value is everything else on that row.
        """
        body = sorted(
            (line for line in lines if line.y0 >= COVER_TABLE_TOP),
            key=lambda item: (item.y0, item.x0),
        )
        if not body:
            return []
        rows: list[list[Line]] = []
        for line in body:
            if rows and line.y0 - rows[-1][0].y0 <= ROW_TOLERANCE:
                rows[-1].append(line)
            else:
                rows.append([line])

        entries: list[tuple[str, str]] = []
        for row in rows:
            row.sort(key=lambda item: item.x0)
            if row[0].x0 > LABEL_COLUMN_MAX:
                continue                      # indented sub-item, not a field
            spaced = cls.join_cells(row)
            flat, index_map = cls.flatten(spaced)
            match = BOOK_VALUE_RE.match(flat)
            if match and match.group(2).strip():
                # "书" + "名：老年人沟通技能指导手册" — name and value split upstream;
                # take the value from the spaced join so name lists stay separated.
                end = index_map[len(match.group(1))]
                entries.append((match.group(1), spaced[end:].lstrip("：: ").strip()))
                continue
            if BOOK_LABEL_RE.match(flat):
                continue                      # label with no value on this row
            if len(row) > 1:
                sep = flat.find("：")
                if sep > 0 and BOOK_LABEL_RE.match(flat[:sep]):
                    end = index_map[sep]
                    entries.append((flat[:sep], spaced[end:].lstrip("：: ").strip()))

        # A split name can also split its value ("主"+"编：孙青" + "钱永霞" +
        # "郑朝晖"), so identical labels are merged in order.
        merged: list[tuple[str, str]] = []
        for label, value in entries:
            if merged and merged[-1][0] == label:
                merged[-1] = (label, merged[-1][1] + " " + value)
            else:
                merged.append((label, value))
        return [
            (label, "、".join(value.split()) if label == "主编" else value)
            for label, value in merged
        ]
    @staticmethod
    def collect_notes(lines: list[Line]) -> list[str]:
        """The 图书简介 prose of page 2, without the cover table or wordmark.

        Collecting starts only at the 图书简介 marker, so page 1 (which carries
        the same metadata table but no blurb) yields nothing. The table is
        repeated below the prose, so that band stops the collection.
        """
        notes: list[str] = []
        buffer = ""
        started = False
        for line in lines:
            if line.y0 >= COVER_TABLE_REPEAT_TOP:
                break                     # repeated cover metadata table
            if line.x0 > 200:
                continue                  # right-hand wordmark fragments
            if line.text.startswith("图书简介"):
                if buffer:
                    notes.append(buffer)
                buffer = line.text.replace("图书简介", "", 1).lstrip("：: ")
                started = True
                continue
            if not started:
                continue
            if len(line.text) <= 2 and not line.text[0].isascii():
                continue                  # left-hand wordmark fragments
            buffer += line.text
        if buffer:
            notes.append(buffer)
        return [re.sub(r"\s+", " ", note).strip() for note in notes if note.strip()]

    def emit_page(self, page_number: int, lines: list[Line]) -> None:
        pending = self.collect_figures(page_number)
        flow: list[Line | Figure] = []
        for line in lines:
            if line.text in ("目 录", "目  录") or TOC_LEADER_RE.fullmatch(line.text):
                continue
            if line.text == "续 表":
                continue  # "continued" marker for the two-part worksheet table
            self.drain_figures(pending, line.y0, flow)
            flow.append(line)
        self.drain_figures(pending, float("inf"), flow)
        self.emit_flow(page_number, flow)
        self.hoist_leading_figures(page_number)
    def drain_figures(self, pending: list[Figure], before_y: float, flow: list) -> None:
        """Move figures whose artwork ends above ``before_y`` into the flow."""
        while pending and pending[0].bbox[3] <= before_y:
            flow.append(pending.pop(0))

    def emit_flow(self, page_number: int, flow: list) -> None:
        """Turn a page's lines and figures into paragraphs, headings and images.

        A paragraph that runs to the foot of a page is carried over to the next
        page, because the PDF only indents the first line of a paragraph and each
        page is read separately. Paragraphs are staged first so the last one can
        be held back until the next page shows whether it continues.
        """
        carry, deferred = self.pending_paragraph, self.pending_block
        self.pending_paragraph, self.pending_block = "", None
        flow = self.regroup_objectives(flow)
        options, option_lines = self.collect_options(flow)
        table_rows, table_lines = self.collect_table_rows(flow)
        typed: list[tuple[Line, str]] = []
        for item in flow:
            if isinstance(item, Figure):
                typed.append((item, FIGURE_LINE))
            elif isinstance(item, Objectives):
                typed.append((item, OBJECTIVES))
            elif id(item) in table_lines:
                typed.append((item, TABLE_ROW))
            elif id(item) in option_lines:
                typed.append((item, OPTION_LINE))
            else:
                typed.append((item, self.line_type(item)))

        staged: list[Block] = [deferred] if deferred is not None else []
        paragraphs: list[int] = []      # indices in ``staged`` holding paragraphs
        for index, (item, kind) in enumerate(typed):
            if kind in (CONSUMED, OPTION_LINE):
                continue
            if kind == TABLE_ROW:
                if id(item) in table_rows:
                    staged.append(
                        Block(kind="table", text=item.text, page=page_number,
                              rows=table_rows[id(item)])
                    )
                continue
            if kind == FIGURE_LINE:
                self.figures.append(item)
                staged.append(
                    Block(kind="figure", text=item.name, page=page_number, figure=item)
                )
                continue
            if kind == OBJECTIVES:
                staged.append(
                    Block(kind="objectives", page=page_number, rows=item.as_pairs())
                )
                continue
            if kind in HEADING_KINDS:
                staged.append(self.make_heading(int(kind[1]), item))
                continue
            if id(item) in options:
                staged.append(
                    Block(kind="options", text=self.tidy(item.text),
                          page=page_number, options=options[id(item)])
                )
                continue
            if not self.starts_paragraph(typed, index, kind):
                continue
            text = BLANK_MARK if self.is_blank_rule(item) else item.text
            cursor = index + 1
            while cursor < len(typed) and not self.starts_paragraph(typed, cursor, typed[cursor][1]):
                other = typed[cursor][0]
                text = self.join_fragment(text, other)
                typed[cursor] = (other, CONSUMED)
                cursor += 1
            paragraphs.append(len(staged))
            staged.append(
                Block(kind="paragraph", text=self.tidy(re.sub(r"\s+", " ", text)),
                      page=page_number)
            )

        # A paragraph that runs to the foot of the page is held back: the next
        # page shows whether it continues (a heading or figure first means it was
        # already complete).
        tail = staged[paragraphs[-1]] if paragraphs else None
        if tail is not None and not self.is_complete(tail.text):
            staged.pop(paragraphs[-1])
        else:
            tail = None

        # The page continues a carried paragraph only when its first flow element
        # is body text; otherwise the carried paragraph stands on its own.
        if (
            deferred is not None
            and paragraphs
            and carry
            and not self.is_complete(carry)
            and self._starts_with_body(typed)
        ):
            # Capture the target before removing the held-back paragraph, which
            # itself sits at the head of the page.
            target = staged[paragraphs[0]]
            staged.remove(deferred)
            target.text = self.tidy(carry + target.text)

        self.blocks.extend(staged)
        if tail is not None:
            self.pending_paragraph = tail.text
            self.pending_block = tail

    @staticmethod
    def _starts_with_body(typed: list) -> bool:
        """Whether the page's first flow element is ordinary body text."""
        for _, kind in typed:
            if kind in (CONSUMED, OPTION_LINE):
                continue
            return kind == BODY
        return False

    @staticmethod
    def open_paragraph(typed: list) -> str:
        """Text of a paragraph left open at the foot of a page, if any.

        Returns the text of the *last* paragraph on the page when it does not end
        on a sentence mark, so the next page can continue it.
        """
        last_text = ""
        last_index = -1
        for index, (item, kind) in enumerate(typed):
            if kind in (CONSUMED, FIGURE_LINE, OPTION_LINE, OBJECTIVES, TABLE_ROW):
                continue
            if kind in HEADING_KINDS:
                continue
            text = item.text
            cursor = index + 1
            while cursor < len(typed) and typed[cursor][1] == CONSUMED:
                text += typed[cursor][0].text
                cursor += 1
            last_text, last_index = text, index
        if last_index < 0 or Converter.is_complete(last_text):
            return ""
        return last_text

    @classmethod
    def is_blank_rule(cls, line: Line) -> bool:
        """True for a printed blank rule: a line with no text of its own.

        Checked against the raw line because normalising the spacing away is what
        makes the rule invisible (``" 。"`` would collapse to ``"。"``). An answer
        stub (``"( )。"``) is not a blank rule: it closes the question and must
        stay attached to its stem.
        """
        stripped = line.text.strip()
        if not stripped or ANSWER_STUB_RE.fullmatch(stripped):
            return False
        return bool(PUNCT_ONLY_RE.fullmatch(stripped))

    @classmethod
    def join_fragment(cls, text: str, line: Line) -> str:
        """Append a continuation line, turning a blank rule into an explicit blank.

        The blank is padded so it stays a separate token and survives the later
        whitespace normalisation.
        """
        if cls.is_blank_rule(line):
            return text + " " + BLANK_MARK + " "
        return cls.join_text(text, line.text)

    def collect_table_rows(
        self, flow: list
    ) -> tuple[dict[int, list[tuple[str, list[str]]]], set[int]]:
        """Detect the 案例练习 worksheet tables of task 1.

        The book prints three blank forms, each a designator row
        ("第一个沟通过程(与班主任)") above the same five column headers. They are
        re-emitted as one table so the worksheet does not break the flow.
        """
        grouped: dict[int, list[tuple[str, list[str]]]] = {}
        consumed: set[int] = set()
        headers = list(TABLE_HEADERS)
        for index, item in enumerate(flow):
            if not isinstance(item, Line) or not TABLE_DESIGNATOR_RE.match(item.text):
                continue
            following = [
                flow[position]
                for position in range(index + 1, min(index + 1 + len(headers), len(flow)))
            ]
            if len(following) < len(headers):
                continue
            if any(
                not isinstance(line, Line) or line.text != expected
                for line, expected in zip(following, headers)
            ):
                continue
            grouped[id(item)] = [(item.text, headers)]
            consumed.add(id(item))
            consumed.update(id(line) for line in following)
        return grouped, consumed

    def collect_options(
        self, flow: list
    ) -> tuple[dict[int, list[tuple[str, str]]], set[int]]:
        """Group a question stem with the A/B/C/D options that follow it.

        Returns the stems mapped to their choices plus the ids of the option
        lines, so the caller can skip those when building the block list.
        """
        grouped: dict[int, list[tuple[str, str]]] = {}
        consumed: set[int] = set()
        index = 0
        while index < len(flow):
            item = flow[index]
            if not isinstance(item, Line) or not self.question_stem(item):
                index += 1
                continue
            cursor = index + 1
            choices: list[tuple[str, str]] = []
            while cursor < len(flow) and isinstance(flow[cursor], Line):
                match = OPTION_LINE_RE.match(flow[cursor].text)
                if not match:
                    break
                choices.append((match.group(1), self.tidy(match.group(2))))
                cursor += 1
            if len(choices) >= 2:
                grouped[id(item)] = choices
                consumed.update(id(flow[position]) for position in range(index + 1, cursor))
                index = cursor
            else:
                index += 1
        return grouped, consumed

    @classmethod
    def question_stem(cls, line: Line) -> bool:
        """True for the line that introduces a set of answer options.

        The printed stem is split at a line break, so the answer stub "( )。"
        commonly arrives as its own line. In that case the stub is not the stem —
        it belongs to the question above it, and the options must attach there.
        """
        text = line.text.strip()
        if ANSWER_STUB_RE.fullmatch(text):
            return False
        return bool(QUESTION_RE.search(text) or BLANK_LEAD_RE.search(cls.tidy(text)))


    @classmethod
    def is_objective_label(cls, line: Line) -> bool:
        """The 知识目标/能力目标/素质目标 column of the objectives box."""
        return (
            len(line.text) <= 4
            and line.x0 < OBJECTIVE_COLUMN_MAX
            and line.size <= OBJECTIVE_LABEL_SIZE
        )

    @staticmethod
    def line_centre(line: Line) -> float:
        return (line.y0 + line.y1) / 2.0

    @classmethod
    def regroup_objectives(cls, flow: list) -> list:
        """Replace the 学习目标 box with a structured Objectives element.

        The box is a two-column table, so PDF reading order interleaves the
        category label (知识/能力/素质目标) with a bullet that wraps onto a second
        line. Extracting it lets the renderer emit a tidy nested list.
        """
        result: list = []
        index = 0
        while index < len(flow):
            item = flow[index]
            if not isinstance(item, Line) or item.text != "学习目标":
                result.append(item)
                index += 1
                continue
            result.append(item)
            end = index + 1
            while end < len(flow) and isinstance(flow[end], Line):
                line = flow[end]
                if (
                    line.y0 - item.y0 > OBJECTIVES_BOX_HEIGHT
                    or line.size > OBJECTIVE_LABEL_SIZE
                    or line.is_standalone
                ):
                    break
                end += 1
            box = [line for line in flow[index + 1:end] if isinstance(line, Line)]
            labels = [line for line in box if cls.is_objective_label(line)]
            if len(labels) >= 2:
                # A bullet can share the label's own row, and the row pitch of the
                # table is close to the gap between items, so a purely geometric
                # split is ambiguous. Instead walk the bullets in order and open
                # the next group once a bullet sits clearly past the boundary
                # between two labels. This keeps every bullet in exactly one group
                # and preserves the reading order.
                centres = [cls.line_centre(label) for label in labels]
                bullets = [
                    line
                    for line in sorted(box, key=lambda entry: (entry.y0, entry.x0))
                    if not cls.is_objective_label(line)
                ]
                groups_: list[list[Line]] = [[] for _ in labels]
                owner = 0
                for position, line in enumerate(bullets):
                    if owner + 1 < len(labels):
                        boundary = (centres[owner] + centres[owner + 1]) / 2.0
                        if cls.line_centre(line) > boundary + OBJECTIVE_BOUNDARY_SLACK:
                            owner += 1
                    groups_[owner].append(line)
                groups: dict[int, list[Line]] = dict(enumerate(groups_))
                pairs: list[tuple[str, list[str]]] = []
                for position, label in enumerate(labels):
                    bullets = cls.merge_bullets(groups[position])
                    pairs.append((label.text, bullets))
                result.append(Objectives(groups=pairs))
            else:
                result.extend(box)
            index = end
        return result

    @classmethod
    def merge_bullets(cls, lines: list[Line]) -> list[str]:
        """Join the wrapped lines of each bullet into one item."""
        bullets: list[str] = []
        for line in sorted(lines, key=lambda entry: (entry.y0, entry.x0)):
            if bullets and line.x0 <= OBJECTIVE_COLUMN_MAX:
                bullets[-1] += line.text          # wrapped continuation
            else:
                bullets.append(line.text)
        return [cls.tidy(re.sub(r"\s+", " ", bullet)) for bullet in bullets]

    def starts_paragraph(self, typed: list, index: int, kind: str) -> bool:
        """Whether the line at ``index`` begins a new paragraph."""
        if kind in (CONSUMED, FIGURE_LINE, OPTION_LINE, OBJECTIVES, TABLE_ROW):
            return True
        if kind in (H2, H3, H4, H5, ITEM):
            return True
        item = typed[index][0]
        if self.is_blank_rule(item):
            return False      # blank-filled answer line: continues the premise
        if ANSWER_STUB_RE.fullmatch(item.text):
            return False      # "( )。" closes the question it follows
        if index == 0:
            return True
        previous, previous_kind = typed[index - 1]
        if previous_kind in HEADING_KINDS:
            return True
        if isinstance(previous, Line) and previous.text.rstrip().endswith(("，", ",")):
            # A line that breaks on a comma is mid-question, so what follows
            # (often a blank rule and then the sentence tail) belongs to it.
            return False
        line = typed[index][0]
        if not line.is_indented:
            # A continuation line: only a page turn can break the paragraph.
            return False
        if line.starts_page and not self.is_complete(previous.text):
            return False  # paragraph flowed onto this page
        return True

    @classmethod
    def join_text(cls, text: str, addition: str) -> str:
        if not text:
            return addition
        if text[-1].isascii() and addition[0].isascii() and not text[-1].isspace():
            return text + " " + addition
        return text + addition

    def hoist_leading_figures(self, page_number: int) -> None:
        """Keep a page's heading above its banner artwork."""
        first_heading = next(
            (
                index
                for index, block in enumerate(self.blocks)
                if block.kind == "heading" and block.page == page_number
            ),
            None,
        )
        if first_heading is None:
            return
        leading: list[Block] = []
        while (
            first_heading > 0
            and self.blocks[first_heading - 1].kind == "figure"
            and self.blocks[first_heading - 1].page == page_number
        ):
            leading.insert(0, self.blocks.pop(first_heading - 1))
            first_heading -= 1
        for offset, block in enumerate(leading):
            self.blocks.insert(first_heading + 1 + offset, block)

    @staticmethod
    def joins(previous: str, current: str) -> bool:
        """Whether ``current`` continues ``previous`` without a space."""
        if not previous:
            return False
        if previous[-1].isascii() and current[0].isascii():
            # keep a space between Latin words ("SOLER 模式" style spacing)
            return previous[-1].isspace()
        return True

    @staticmethod
    def is_complete(text: str) -> bool:
        """Whether a paragraph already ends on a sentence-final mark.

        Used at page boundaries: a page ending mid-sentence ("…编码方式")
        continues onto the next page even when that page starts indented,
        because Chinese typesetting only indents the first line of a paragraph.
        """
        stripped = text.rstrip()
        return bool(stripped) and stripped[-1] in SENTENCE_END

    def record_heading(self, level: int, line: Line) -> None:
        self.blocks.append(self.make_heading(level, line))

    def make_heading(self, level: int, line: Line) -> Block:
        """Build a heading block and update the running context and contents."""
        text = line.text
        if level == 2:
            self.project, self.task, self.section = text, "", ""
        elif level == 3:
            self.task, self.section = text, ""
        elif level == 4:
            if text not in self.TASK_SECTIONS:
                # 一、填空题 belongs to 课后练习, not to the task itself.
                level = 5
            else:
                self.section = text
        if level <= 5:
            self.toc.append((level, text, line.page))
        return Block(kind="heading", text=text, level=level, page=line.page)

    # ------------------------------------------------------------ figures --
    @staticmethod
    def is_decoration(figure: Figure, page_rect: "fitz.Rect") -> bool:
        """True for artwork that carries no content (rules, banners, leaves).

        Calibrated against a WPS-produced textbook: heading banners are
        ~100x21pt, heading underlines ~166x6pt, corner leaves ~30x24pt,
        margin sidebars ~44x306pt and footers span the page width.
        """
        x0, y0, x1, y1 = figure.bbox
        width, height = x1 - x0, y1 - y0
        if y1 <= BODY_TOP or y0 >= BODY_BOTTOM:
            return True                       # header / footer artwork
        if width < 60 and height > 150:
            return True                       # full-height margin sidebar
        if height <= 26 and width <= 170:
            return True                       # banner, underline rule, small icon
        if width < 60 and height < 60:
            return True                       # corner leaf ornament
        if width > page_rect.width * 0.9 and height < 60:
            return True                       # full-width strip
        return False

    def collect_figures(self, page_number: int) -> list[Figure]:
        """Return the content figures of a page in top-to-bottom order."""
        figures: list[Figure] = []
        with fitz.open(self.pdf_path) as doc:
            page = doc[page_number - 1]
            page_rect = page.rect
            seen: set[int] = set()
            for image in page.get_images(full=True):
                xref = image[0]
                for rect in page.get_image_rects(xref):
                    figure = Figure(
                        xref=xref,
                        page=page_number,
                        bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                    )
                    if self.is_decoration(figure, page_rect) or xref in seen:
                        continue
                    seen.add(xref)
                    figure.name = self.figure_name(page_number, xref)
                    figure.rel_path = self.save_figure(doc, xref, figure.name)
                    figures.append(figure)
        figures.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
        return figures

    def figure_name(self, page_number: int, xref: int) -> str:
        if page_number <= 2:
            return "fig-cover"
        stem = re.sub(r"[^\w\u4e00-\u9fff]+", "-", self.pdf_path.stem)[:40].strip("-")
        return f"{stem}-p{page_number:03d}-x{xref:04d}"

    def save_figure(self, doc: "fitz.Document", xref: int, name: str) -> str:
        """Extract the image; return the path used inside the Markdown file."""
        if self.images_dir is None:
            return ""
        pixmap = fitz.Pixmap(doc, xref)
        if pixmap.n - pixmap.alpha > 3:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        extension = ".png"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        target = self.images_dir / f"{name}{extension}"
        pixmap.save(str(target))
        return f"{self.images_dir.name}/{target.name}"

    # ------------------------------------------------------------- output --
    def render(self) -> str:
        out: list[str] = []
        title = self.title or self.pdf_path.stem
        out.extend(self.render_front_matter(title))
        out.extend(self.render_table_of_contents())
        out.extend(self.render_body())
        markdown = "\n".join(out)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip() + "\n"

    def render_front_matter(self, title: str) -> list[str]:
        out: list[str] = []
        fields = dict(self.cover_fields)
        out.append("---")
        out.append(f"title: {title}")
        out.append(f"source_pdf: {self.pdf_path.name}")
        out.append(f"pages: {self.page_count()}")
        for key, label in (
            ("书号", "isbn"),
            ("主编", "authors"),
            ("定价", "price"),
            ("图书性质", "category"),
        ):
            if fields.get(key):
                out.append(f"{label}: {fields[key]}")
        out.append("---")
        out.append("")
        out.append(f"# {title}")
        out.append("")

        for note in self.cover_notes:
            out.append(f"> {self.tidy(note)}")
            out.append("")
        if self.cover_bullets:
            out.append("> **图书特色**")
            out.append(">")
            for bullet in self.cover_bullets:
                out.append(f"> - {bullet}")
            out.append("")
        out.append("---")
        out.append("")
        return out

    def render_table_of_contents(self) -> list[str]:
        if not self.toc:
            return []
        out = ["## 目录", ""]
        for level, text, page in self.toc:
            indent = "  " * max(0, level - 2)
            out.append(f"{indent}- {text} <sub>p{page}</sub>")
        out.append("")
        out.append("---")
        out.append("")
        return out

    def render_body(self) -> list[str]:
        out: list[str] = []
        for block in self.blocks:
            if block.kind == "heading":
                out.append("")
                out.append("#" * block.level + " " + self.tidy(block.text))
                out.append("")
            elif block.kind == "figure":
                if block.figure and block.figure.rel_path:
                    out.append(f"![{block.figure.name}]({block.figure.rel_path})")
                    out.append("")
            elif block.kind == "objectives":
                for label, bullets in block.rows:
                    out.append(f"**{label}**")
                    out.append("")
                    for bullet in bullets:
                        out.append(f"- {bullet}")
                    out.append("")
            elif block.kind == "options":
                out.append(self.tidy(block.text))
                out.append("")
                for letter, text in block.options:
                    out.append(f"- **{letter}.** {text}")
                out.append("")
            elif block.kind == "table":
                headers = block.rows[0][1] if block.rows else []
                out.append(block.text)
                out.append("")
                out.append("| " + " | ".join(headers) + " |")
                out.append("| " + " | ".join("---" for _ in headers) + " |")
                out.append("| " + " | ".join("" for _ in headers) + " |")
                out.append("")
            elif block.kind == "paragraph":
                out.append(block.text)
                out.append("")
        return out

    def page_count(self) -> int:
        with fitz.open(self.pdf_path) as doc:
            return doc.page_count

    def convert(self) -> Path:
        pages = self.read_lines()
        self.build_blocks(pages)
        markdown = self.render()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.out_dir / f"{self.pdf_path.stem}.md"
        target.write_text(markdown, encoding="utf-8")
        return target


def iter_pdfs(source: Path) -> list[Path]:
    if source.is_dir():
        return sorted(source.glob("*.pdf"))
    return [source]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="PDF file or directory of PDFs")
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="output directory (default: <source dir>/md)",
    )
    parser.add_argument("--no-images", action="store_true", help="skip figure export")
    args = parser.parse_args(argv)

    if not args.source.exists():
        parser.error(f"not found: {args.source}")

    pdfs = iter_pdfs(args.source)
    if not pdfs:
        print(f"no PDF files under {args.source}", file=sys.stderr)
        return 1

    for pdf in pdfs:
        out_dir = args.out_dir or pdf.parent / "md"
        images_dir = None if args.no_images else out_dir / f"{pdf.stem}.assets"
        converter = Converter(pdf_path=pdf, out_dir=out_dir, images_dir=images_dir)
        target = converter.convert()
        rel = target.relative_to(Path.cwd()) if target.is_relative_to(Path.cwd()) else target
        print(
            f"{pdf.name}: {converter.page_count()} pages -> {rel} "
            f"({len(converter.figures)} figures, {len(converter.toc)} headings)"
        )
        for warning in converter.warnings:
            print(f"  warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
