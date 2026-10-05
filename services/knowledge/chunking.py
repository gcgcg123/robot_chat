from __future__ import annotations

from dataclasses import dataclass
import re

from services.knowledge.markdown import (
    SectionNode,
    assign_pages,
    paragraph_blocks,
    parse_sections,
    parse_toc,
)
from services.knowledge.profiles import KnowledgeProfile, is_pure_exercise

# The two relevance tiers stored per chunk. 'scenario' answers a situation ("老年人不肯吃饭
# 时怎么说"); 'reference' is exposition that only answers conceptual questions ("什么是沟通").
# They are admitted at different floors, see services/knowledge/retriever.py.
KIND_SCENARIO = "scenario"
KIND_REFERENCE = "reference"

_SENTENCE_END = re.compile(r"(?<=[。！？；.!?;])")


@dataclass(frozen=True)
class ChunkDraft:
    section_path: str
    page: int | None
    kind: str
    text: str


@dataclass(frozen=True)
class DroppedSection:
    section_path: str
    reason: str
    chars: int


def _matches(title: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern and pattern in title for pattern in patterns)


def classify(node: SectionNode, text: str, profile: KnowledgeProfile) -> tuple[bool, str, str]:
    """(keep, reason, kind) for one section.

    Order matters: ``drop_sections`` wins over ``keep_sections``, so ``课后练习`` still
    vanishes even though its children would each be considered; and an inherited
    ``scenario`` rule beats a parent's ``reference`` rule, which is how the two
    elderly-care subsections inside a knowledge chapter become scenario content.
    """

    title = node.title
    if _matches(title, profile.drop_sections):
        return False, "drop_section", ""
    if profile.keep_sections and not (
        _matches(title, profile.keep_sections)
        or any(_matches(parent, profile.keep_sections) for parent in node.parents)
    ):
        return False, "not_in_keep_sections", ""

    inherits_scenario = any(_matches(parent, profile.scenario_sections) for parent in node.parents)
    if _matches(title, profile.scenario_sections) or inherits_scenario:
        kind = KIND_SCENARIO
    elif _matches(title, profile.reference_sections) or any(
        _matches(parent, profile.reference_sections) for parent in node.parents
    ):
        kind = KIND_REFERENCE
    elif profile.default_kind in (KIND_SCENARIO, KIND_REFERENCE):
        # Hand-written scenario files have no chapter vocabulary to match; their profile
        # declares the kind once instead.
        kind = profile.default_kind
    else:
        return False, "no_kind_rule", ""

    if profile.drop_pure_exercises:
        reason = is_pure_exercise(text)
        if reason:
            return False, reason, kind
    return True, "kept", kind


def _split_long(page: int | None, text: str, max_chars: int) -> list[tuple[int | None, str]]:
    """Split one oversized paragraph on sentence ends, never inside a sentence."""

    if len(text) <= max_chars:
        return [(page, text)]
    parts: list[tuple[int | None, str]] = []
    buffer = ""
    for sentence in (piece for piece in _SENTENCE_END.split(text) if piece):
        if buffer and len(buffer) + len(sentence) > max_chars:
            parts.append((page, buffer))
            buffer = sentence
        else:
            buffer += sentence
    if buffer:
        parts.append((page, buffer))
    return parts


def chunk_document(text: str, profile: KnowledgeProfile) -> tuple[list[ChunkDraft], list[DroppedSection]]:
    """Turn one document into the chunks the index will hold.

    Sections are the unit: the corpus keeps one paragraph per line and carries
    ``<sub>pN</sub>`` page anchors, so a heading-delimited chunk can be cited to the page
    instead of guessing where a fixed 600-character window would land.
    """

    lines = text.splitlines()
    nodes = parse_sections(lines)
    if nodes and nodes[0].start > 0 and any(line.strip() for line in lines[:nodes[0].start]):
        # Text before the first heading belongs to no section and would vanish silently. Give it
        # a synthetic "前言" section so a profile can keep or drop it explicitly: OCR output
        # often starts mid-chapter and that leading fragment is noise. Its level matches the
        # profile's floor so the level filter cannot skip it without a report.
        nodes.insert(
            0,
            SectionNode(
                level=profile.min_heading_level,
                title="前言",
                start=-1,
                end=nodes[0].start,
                parents=(),
            ),
        )
    section_pages = assign_pages(nodes, parse_toc(lines))
    chunks: list[ChunkDraft] = []
    dropped: list[DroppedSection] = []
    # The last page anchor seen anywhere in the document. A section that starts mid-page (after
    # the anchor was already consumed by the previous section) has no anchor of its own, and
    # leaving its page as None made page-based calibration read a correct hit as a miss:
    # "爷爷得了老年痴呆" ranked its answer at #2 and #5, but those chunks had page=None.
    last_page: int | None = None

    # Every section is considered, not just the leaves: a section that carries its own text
    # *and* has subsections (the manual's 知识学习 intros, or the opening paragraph of a
    # hand-written answer) would otherwise be dropped silently. A parent whose lines are only
    # child headings has no text and falls out on min_chars.
    for index, node in enumerate(nodes):
        if node.level < profile.min_heading_level:
            continue
        blocks = paragraph_blocks(lines, node)
        body = " ".join(part for _page, part in blocks)
        if not body:
            # A heading whose lines are only other headings (the manual's 项目/任务 levels)
            # carries nothing to index; it is not a "dropped section", just structure.
            continue
        keep, reason, kind = classify(node, body, profile)
        if not keep:
            dropped.append(DroppedSection(node.section_path, reason, len(body)))
            continue
        if len(body) < profile.min_chars:
            dropped.append(DroppedSection(node.section_path, "too_short", len(body)))
            continue

        # A section starts on the page its contents entry names; a page anchor found inline
        # in the body (not the case for this corpus) would be more precise and wins.
        section_page = section_pages[index] if section_pages[index] is not None else last_page
        page: int | None = section_page
        buffer = ""
        for block_page, part in blocks:
            if block_page is not None:
                last_page = block_page
            current_page = block_page if block_page is not None else (last_page or section_page)
            if buffer and (current_page != page or len(buffer) + len(part) > profile.max_chars):
                chunks.append(ChunkDraft(node.section_path, page, kind, buffer))
                buffer = ""
                page = current_page
            if not buffer:
                page = current_page
            buffer = f"{buffer} {part}".strip()
            if len(buffer) > profile.max_chars:
                for split_page, piece in _split_long(page, buffer, profile.max_chars):
                    chunks.append(ChunkDraft(node.section_path, split_page, kind, piece))
                buffer = ""
                page = current_page
        if buffer:
            chunks.append(ChunkDraft(node.section_path, page, kind, buffer))

    return chunks, dropped


def citation(chunk: ChunkDraft, label: str) -> str:
    """Citation text; ``pipeline.py`` already turns ``chunk.source_id`` into ``citations``."""

    page = f" p{chunk.page}" if chunk.page else ""
    leaf_title = chunk.section_path.split(" › ")[-1]
    return f"{label}{page} › {leaf_title}"
