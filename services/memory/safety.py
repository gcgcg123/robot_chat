"""A long-term memory for a safety disclosure, derived from the user's own words.

Why this is not left to the model's ``remember`` block: the flywheel stores what the model
*proposes*, and the instruction list it is given (name, home, family, pets, preferences, skills,
goals...) does not mention disclosures. Measured on 2026-10-03, a turn saying "我要自殺" was
answered kindly and then forgotten: no ``memory_chunks`` row, so a later turn could not recall it.
Here the memory comes from the local risk detector instead, so remembering a threat does not depend
on the model volunteering one.

The opposite case is handled too. "我不想死" contains "想死", and "他叫我不會自殺" contains
"自殺"; storing either as a disclosure would put words in the user's mouth -- and this memory is
auto-approved, so nothing reviews it before it is recalled. A phrase preceded by a negation marker
is therefore not stored (``_negated``). The *risk event* is still raised, because the detector is
the conservative half of that path and a human reviews those; the asymmetry is deliberate.

Two keys, so a self-harm disclosure and a threat to someone else do not overwrite each other, and
so a later disclosure of the same kind updates the same row instead of piling up: the memory holds
the latest state, while ``risk_events`` keeps the full history.
"""
from __future__ import annotations

from typing import Any

from services.analysis.risk import SELF_HARM, VIOLENCE
from services.memory.candidates import normalize_candidate, normalize_text
from services.memory.flywheel import MemorySettings

SAFETY_SLOT_KEY = "risk:safety"
VIOLENCE_SLOT_KEY = "risk:violence"
# Top of the scale: a disclosure should outrank preferences when the flywheel ranks memories, and
# a hit (or the passage of ~31 days, at the 14-day half-life) is what keeps it out of `cold`.
SAFETY_IMPORTANCE = 0.95

SELF_HARM_PROBE = "使用者有沒有說過想不開、想結束生命或傷害自己？"
VIOLENCE_PROBE = "使用者有沒有說過想傷害別人？"

_NEGATIONS = ("不", "沒", "没", "別", "别", "無", "无", "甭")


def _negated(message: str, start: int) -> bool:
    """Whether the phrase at ``start`` is denied by the one or two characters before it."""

    return any(marker in message[max(0, start - 2):start] for marker in _NEGATIONS)


def first_disclosure(message: str, vocabulary: tuple[str, ...]) -> str:
    """The phrase from ``vocabulary`` that the user states first, ignoring denied ones.

    Earliest in the *message*, not in the vocabulary: "我不想活了，我想跳樓" says 不想活 first, so
    that is what gets quoted. Picking by vocabulary order quoted whichever term happened to be
    listed earlier in this file.
    """

    lowered = message.lower()
    best: tuple[int, str] = (len(lowered) + 1, "")
    for phrase in vocabulary:
        start = lowered.find(phrase)
        while start != -1:
            if not _negated(lowered, start) and start < best[0]:
                best = (start, phrase)
                break
            start = lowered.find(phrase, start + 1)
    return best[1]


def safety_memories(risk: Any, user_message: str, *, settings: MemorySettings | None = None) -> list[dict[str, Any]]:
    """Memories to store for this turn's disclosure; empty when there is none.

    Only ``urgent`` counts. ``attention`` is ordinary distress (絕望, 撐不下去) and the model's own
    proposals are a better judge of whether it is worth keeping -- flooding the store with every bad
    day would make the safety rows harder to find, not easier.
    """

    level = str((risk or {}).get("risk_level") or "none")
    if level != "urgent":
        return []

    message = normalize_text(user_message)
    plans = (
        (SELF_HARM, SAFETY_SLOT_KEY, SELF_HARM_PROBE, "想結束生命或傷害自己"),
        (VIOLENCE, VIOLENCE_SLOT_KEY, VIOLENCE_PROBE, "想傷害別人"),
    )
    memories: list[dict[str, Any]] = []
    for vocabulary, key, probe, description in plans:
        phrase = first_disclosure(message, vocabulary)
        if not phrase:
            continue
        candidate = normalize_candidate(
            {
                "key": key,
                # The quote is the memory: it is what the user said, and it keeps the statement
                # honest when someone reviews it later.
                "text": f"使用者曾說出{description}的話：「{phrase}」。",
                "probe": probe,
                "quote": phrase,
                "importance": SAFETY_IMPORTANCE,
            },
            message,
            settings=settings,
            source_id="safety",
        )
        if candidate is not None:
            memories.append(candidate)
    return memories
