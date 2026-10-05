from __future__ import annotations

from dataclasses import dataclass
import re

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
PAGE_MARKER = re.compile(r"<sub>\s*p(\d+)\s*</sub>", re.IGNORECASE)


@dataclass(frozen=True)
class SectionNode:
    """One heading plus the lines it owns until the next heading of any level.

    ``parents`` holds the ancestor titles, outermost first, so a chunk can be cited as
    "手册 p7 › 二、沟通的因素" instead of a bare page number.
    """

    level: int
    title: str
    start: int
    end: int
    parents: tuple[str, ...]

    @property
    def section_path(self) -> str:
        return " › ".join((*self.parents, self.title))


def parse_sections(lines: list[str]) -> list[SectionNode]:
    """Build the heading tree as a flat, ordered list.

    The corpus produced by ``scripts/pdf_to_md.py`` keeps one paragraph per line, so line
    indices are enough to delimit a section -- no block model is needed.
    """

    flat: list[tuple[int, str, int]] = []
    for index, raw in enumerate(lines):
        match = HEADING.match(raw.rstrip())
        if match:
            flat.append((len(match.group(1)), match.group(2).strip(), index))

    nodes: list[SectionNode] = []
    stack: list[tuple[int, str]] = []
    for position, (level, title, start) in enumerate(flat):
        while stack and stack[-1][0] >= level:
            stack.pop()
        end = flat[position + 1][2] if position + 1 < len(flat) else len(lines)
        nodes.append(SectionNode(level, title, start, end, tuple(t for _l, t in stack)))
        stack.append((level, title))
    return nodes


def page_marker(text: str) -> int | None:
    match = PAGE_MARKER.search(text)
    return int(match.group(1)) if match else None


def strip_page_markers(text: str) -> str:
    return PAGE_MARKER.sub(" ", text).strip()


def paragraph_blocks(lines: list[str], node: SectionNode) -> list[tuple[int | None, str]]:
    """(page, text) for every non-empty, non-heading line inside ``node``.

    A page anchor is only honoured when it sits *inside* these lines. Scanning backwards for
    "the page in effect" would be wrong for this corpus: its 55 anchors all live in the
    table of contents, so every body paragraph would inherit the last one (p31).
    """

    blocks: list[tuple[int | None, str]] = []
    page: int | None = None
    for raw in lines[node.start + 1:node.end]:
        if HEADING.match(raw.rstrip()):
            continue
        found = page_marker(raw)
        if found is not None:
            page = found
        text = strip_page_markers(raw)
        if text:
            blocks.append((page, text))
    return blocks


def own_text(lines: list[str], node: SectionNode) -> str:
    return " ".join(text for _page, text in paragraph_blocks(lines, node))


TOC_ENTRY = re.compile(r"^\s*[-*]\s+(?P<title>.+?)\s*<sub>\s*p(?P<page>\d+)\s*</sub>\s*$", re.IGNORECASE)


def front_matter(lines: list[str]) -> dict[str, str]:
    """The ``key: value`` block between the leading ``---`` fences, if present."""

    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for raw in lines[1:]:
        if raw.strip() == "---":
            break
        if ":" in raw:
            key, _, value = raw.partition(":")
            values[key.strip()] = value.strip()
    return values


def parse_toc(lines: list[str]) -> list[tuple[str, int]]:
    """(title, page) for every table-of-contents entry, in document order.

    The corpus carries its page numbers on the *contents* entries, not inline in the body:
    all 55 anchors of this manual sit between lines 24 and 78, inside the 目录 list. A body
    section therefore inherits the page its TOC entry names -- the page the section *starts*
    on, which is what a citation needs.
    """

    entries: list[tuple[str, int]] = []
    for raw in lines:
        match = TOC_ENTRY.match(raw.rstrip())
        if match:
            entries.append((match.group("title").strip(), int(match.group("page"))))
    return entries


def assign_pages(nodes: list[SectionNode], toc: list[tuple[str, int]]) -> list[int | None]:
    """Start page per section, matching body headings to TOC entries in order.

    Titles repeat across tasks ("一、填空题" four times), so the n-th body occurrence is
    matched with the n-th TOC occurrence of the same title. A heading with no entry of its
    own inherits its parent's page rather than guessing.
    """

    available: dict[str, list[int]] = {}
    for title, page in toc:
        available.setdefault(title, []).append(page)

    used: dict[str, int] = {}
    pages: list[int | None] = []
    stack: list[tuple[int, int | None]] = []
    for node in nodes:
        while stack and stack[-1][0] >= node.level:
            stack.pop()
        parent_page = stack[-1][1] if stack else None
        candidates = available.get(node.title) or []
        position = used.get(node.title, 0)
        if position < len(candidates):
            page = candidates[position]
            used[node.title] = position + 1
        else:
            page = parent_page
        pages.append(page)
        stack.append((node.level, page))
    return pages
