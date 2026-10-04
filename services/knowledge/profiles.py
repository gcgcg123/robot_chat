from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

# Bump when the cleaning/chunking rules change: it is part of the import idempotency key,
# so a rule change re-imports documents instead of silently keeping stale chunks.
#   v2: case narratives that close with a question are no longer mistaken for exercises, and
#       text before the first heading is reported instead of vanishing.
#   v3: a chunk that starts mid-page inherits the last page anchor seen, instead of reporting
#   v4: every chunk also stores sentence-window embeddings so retrieval can score its best window.
#       page=None (which made page-based calibration read correct hits as misses).
CLEANER_VERSION = 4

DEFAULT_PROFILES_DIR = Path("data") / "knowledge" / "profiles"


@dataclass(frozen=True)
class KnowledgeProfile:
    """Per-document cleaning rules.

    The rules live with the document, not with the code: the first corpus is a vocational
    textbook whose structure (目录 / 学习目标 / 任务描述 / 填空·选择·判断) says nothing about
    the next book. A document without a profile is refused by the importer rather than
    indexed with rules that do not fit it.
    """

    doc: str
    path_glob: str = ""
    keep_sections: tuple[str, ...] = ()
    drop_sections: tuple[str, ...] = ()
    scenario_sections: tuple[str, ...] = ()
    reference_sections: tuple[str, ...] = ()
    drop_pure_exercises: bool = True
    min_chars: int = 30
    max_chars: int = 600
    min_heading_level: int = 3
    floor_overrides: dict[str, float] = field(default_factory=dict)
    source_label: str = ""
    # Kind for a section that no kind rule matches. Hand-written scenario answers have no
    # chapter structure to match against, so they declare "everything here is scenario".
    default_kind: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeProfile":
        shape = data.get("content_shape") or {}
        kinds = data.get("kind_rules") or {}
        return cls(
            doc=str(data["doc"]),
            path_glob=str(data.get("path_glob", "")),
            keep_sections=tuple(data.get("keep_sections") or ()),
            drop_sections=tuple(data.get("drop_sections") or ()),
            scenario_sections=tuple(kinds.get("scenario") or ()),
            reference_sections=tuple(kinds.get("reference") or ()),
            drop_pure_exercises=bool(shape.get("drop_pure_exercises", True)),
            min_chars=int(shape.get("min_chars", 30)),
            max_chars=int(shape.get("max_chars", 600)),
            min_heading_level=int(shape.get("min_heading_level", 3)),
            floor_overrides=dict(data.get("floor_overrides") or {}),
            source_label=str(data.get("source_label") or data["doc"]),
            default_kind=str(data.get("default_kind") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "doc": self.doc,
            "path_glob": self.path_glob,
            "keep_sections": list(self.keep_sections),
            "drop_sections": list(self.drop_sections),
            "kind_rules": {
                "scenario": list(self.scenario_sections),
                "reference": list(self.reference_sections),
            },
            "content_shape": {
                "drop_pure_exercises": self.drop_pure_exercises,
                "min_chars": self.min_chars,
                "max_chars": self.max_chars,
                "min_heading_level": self.min_heading_level,
            },
            "floor_overrides": dict(self.floor_overrides),
            "source_label": self.source_label,
            "default_kind": self.default_kind,
        }


def load_profiles(directory: str | Path = DEFAULT_PROFILES_DIR) -> list[KnowledgeProfile]:
    root = Path(directory)
    if not root.is_dir():
        return []
    profiles: list[KnowledgeProfile] = []
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles.append(KnowledgeProfile.from_dict(data))
    return profiles


def profile_for(document_path: str | Path, profiles: list[KnowledgeProfile]) -> KnowledgeProfile | None:
    """Match a document to its profile by glob, then by bare filename containment.

    Slugs and filenames drift (the corpus filenames carry a publisher suffix), so the
    glob is tried first and a substring match on ``doc`` is the forgiving fallback.
    """

    text = str(document_path).replace("\\", "/")
    name = Path(document_path).name
    for profile in profiles:
        if profile.path_glob:
            if Path(text).match(profile.path_glob) or re.fullmatch(profile.path_glob, name):
                return profile
    for profile in profiles:
        if profile.doc and profile.doc in name:
            return profile
    return None


def is_pure_exercise(text: str) -> str | None:
    """Why ``text`` is a task for the reader rather than an answer, if it is.

    Measured on this manual's 案例练习 sections: several are instructions to fill in a
    blank table or to answer a question ("反思与班主任和养老院负责人的沟通过程，将沟通要素
    整理在下表中" followed by an empty table). Indexing those injects questions as if they
    were reference material, so they are dropped by shape, not by heading.
    """

    stripped = text.strip()
    if not stripped:
        return "empty"

    blanks = stripped.count("（ ）") + stripped.count("( )") + len(re.findall(r"_{3,}", stripped))
    if blanks >= 3:
        return "fill_in_blanks"

    empty_cells = len(re.findall(r"\|\s*(?=\|)", stripped))
    if empty_cells >= 3:
        return "empty_table_to_fill"

    advice = sum(
        1
        for marker in ("要", "可以", "应该", "需要", "方法", "步骤", "例如", "比如", "首先", "其次", "建议")
        if marker in stripped
    )
    questions = stripped.count("？") + stripped.count("?")
    asks_reader = bool(re.match(r"^(请|试|如何|为什么|什么|辨析|辨认|陈述|整理|填写|回答)", stripped))
    # Only the question part counts. A case narrative that closes with "你会怎么做？" is scenario
    # material, while a block of nothing but questions is an exercise. Measured on the
    # starvation task's 情景导入: about 150 characters of case plus one closing question.
    # The split is per *sentence*: splitting on the question mark alone would attribute the
    # whole narrative before it to the question and drop real case material.
    sentences = [part for part in re.split(r"(?<=[。！？；?!;])", stripped) if part.strip()]
    question_chars = sum(len(part) for part in sentences if part.strip().endswith(("？", "?")))
    if advice == 0 and questions and (questions >= 2 or asks_reader) and len(stripped) - question_chars < 80:
        return "questions_only"

    # A short imperative paragraph is a classroom task ("为奇奇和怪怪拟定沟通计划书"),
    # whereas the same heading in another chapter holds a real scenario with advice
    # ("参照以下步骤…完成劝说张奶奶进行手术的任务", 1801 chars). Length plus the absence of
    # advice is what separates them.
    if advice == 0 and len(stripped) < 250 and re.match(r"^(为|参照|按照|根据|结合|在小组|分组|请|试|拟定)", stripped):
        return "task_instructions"

    # A case description that ends by asking the reader to answer something is a question
    # with context attached, not reference material.
    tail = stripped[-60:]
    if advice == 0 and re.search(r"(请|试|如何|为什么|哪些)[^。！？]{0,40}[。？]", tail):
        return "question_tail"
    return None
