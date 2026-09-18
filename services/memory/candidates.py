"""Turn the model's memory proposals into validated, storable rows.

The model returns proposals inside the JSON block it already emits every turn::

    {"emotion": "...", "risk": "...",
     "remember": [{"key": "name", "text": "使用者叫絕絕子",
                   "probe": "我叫什麼名字？", "quote": "我叫絕絕子",
                   "importance": 0.9}]}

Two rules matter here:

* **``probe`` is the matching key.** Matching a user question against the memory
  *statement* is unreliable for short Chinese text (measured: best-vs-second
  margin collapsed to 0.001); matching it against a canonical question is not
  (margin 0.215). So every memory carries the question it answers.
* **``approved`` is decided by a verbatim quote**, not by trusting the model.
  ``RAG_DESIGN`` requires that LLM-proposed memories are not automatically
  approved facts.  A memory only becomes immediately usable when the model quotes
  the user's own words and that quote really appears in the message; anything the
  model inferred stays ``approved=0`` for review.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from services.memory.flywheel import MemorySettings

MIN_TEXT = 2
MAX_TEXT = 280
MAX_PROBE = 120
MAX_QUOTE = 200
MAX_KEY = 40

# Slot keys are stable identifiers so re-stating a fact updates it in place
# instead of piling up near-duplicates.
SLOT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_:.\-]{0,39}$")
_SLOT_KEY = SLOT_KEY_PATTERN


def valid_slot_key(value: Any) -> bool:
    """Whether a caller-supplied slot key is usable as a stable identifier."""

    return bool(SLOT_KEY_PATTERN.match(normalize_text(value).lower()))


def normalize_text(value: Any) -> str:
    """Collapse whitespace so comparison and storage are stable."""

    return " ".join(str(value if value is not None else "").split()).strip()


def quote_is_verbatim(quote: Any, user_message: str) -> bool:
    """Whether ``quote`` really appears in what the user said this turn."""

    candidate = normalize_text(quote)
    if not candidate or len(candidate) > MAX_QUOTE:
        return False
    return candidate in normalize_text(user_message)


def normalize_candidate(
    raw: Any,
    user_message: str,
    *,
    settings: MemorySettings | None = None,
    source_id: str = "conversation",
) -> dict[str, Any] | None:
    """Validate one proposal; return ``None`` when it is not storable."""

    config = settings or MemorySettings()
    if not isinstance(raw, dict):
        return None

    text = normalize_text(raw.get("text"))
    if len(text) < MIN_TEXT or len(text) > MAX_TEXT:
        return None

    probe = normalize_text(raw.get("probe"))[:MAX_PROBE] or text

    key = normalize_text(raw.get("key")).lower()
    if key and not _SLOT_KEY.match(key):
        key = ""

    try:
        importance = float(raw.get("importance", config.default_importance))
    except (TypeError, ValueError):
        importance = config.default_importance
    importance = min(1.0, max(0.05, importance))

    return {
        "text": text,
        "probe": probe,
        "slot_key": key or None,
        "importance": importance,
        "approved": quote_is_verbatim(raw.get("quote"), user_message),
        "quote": normalize_text(raw.get("quote"))[:MAX_QUOTE],
        "source_id": normalize_text(raw.get("source_id"))[:128] or source_id,
        "source_kind": "auto",
    }


def normalize_candidates(
    raw: Iterable[Any] | None,
    user_message: str,
    *,
    settings: MemorySettings | None = None,
    source_id: str = "conversation",
) -> list[dict[str, Any]]:
    """Validate a batch, dropping duplicates and capping the per-turn budget."""

    config = settings or MemorySettings()
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in raw:
        candidate = normalize_candidate(item, user_message, settings=config, source_id=source_id)
        if candidate is None:
            continue
        fingerprint = candidate["slot_key"] or candidate["text"]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(candidate)
        if len(result) >= config.max_candidates_per_turn:
            break
    return result
