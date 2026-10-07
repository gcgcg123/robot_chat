"""Rolling summary: what the user and the robot said before the verbatim window starts.

The verbatim window (`services/dialogue/history.py`) is bounded on purpose -- replaying an hour of
talk on every turn would cost more than it is worth and drown the actual question. But a hard
window means turn 30 cannot refer to turn 12, so everything older than the window is folded into a
few sentences here, once, and carried in the system prompt from then on.

Two properties matter more than the exact wording of the prompt:

* **No gaps.** The summary covers the oldest ``covered_count`` turns; the verbatim window covers as
  many of the remaining ones as needed. A turn is therefore never in neither place -- which is what
  a naive "summarise every 6th turn" would produce.
* **The turn never depends on the summary call succeeding.** Summarising is another LLM request: if
  it times out or the endpoint rejects it, the turn continues with the previous summary and the
  backlog simply stays in the verbatim window until the next attempt.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from services.dialogue.history import MAX_TURNS, load_recent_turns, recent_turn_limit

# Refresh only once this many turns have retired past the window. Summarising every turn would add
# a second LLM request to every single turn for no benefit -- five sixths of the time the input to
# the summariser would be identical apart from one turn.
SUMMARY_EVERY = 6
SUMMARY_MAX_CHARS = 400
# How many retired turns one summarisation request may read. Normal operation never reaches this
# (a refresh fires as soon as 6 turns have retired), but a backlog can grow large when the summary
# is switched off for a while and then switched back on -- or when an existing database is first
# upgraded to include this feature. Catching up in bounded slices is slower but keeps the request
# from being handed an unbounded transcript; the oldest turns go first, so nothing is skipped.
SUMMARY_SLICE_MAX = 30
DEFAULT_SUMMARY = ""

PROMPT = (
    "你是對話摘要器。請把「先前的摘要」與「新增的對話」合併成一段更新後的摘要。\n"
    "要保留：使用者的處境與近況、正在進行的話題、他提過要做的事或約定、情緒基調、還沒解決的問題。\n"
    "要求：3～5 句繁體中文，{limit} 字以內；只寫對話裡出現過的資訊，不要推測，不要逐句複述，"
    "不要提到這段指示本身。只輸出摘要本文。\n\n"
    "先前的摘要：\n{previous}\n\n新增的對話：\n{transcript}"
)


@dataclass
class ShortTermContext:
    """What the pipeline needs to know about the conversation so far."""

    history: list[dict[str, str]] = field(default_factory=list)
    summary: str = DEFAULT_SUMMARY
    covered: int = 0
    total: int = 0
    refreshed: bool = False
    note: str = ""


def summary_enabled(env: dict | None = None) -> bool:
    source = os.environ if env is None else env
    raw = str(source.get("IOT_DIALOGUE_SUMMARY", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def summary_every(env: dict | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get("IOT_DIALOGUE_SUMMARY_EVERY", "")).strip()
    try:
        value = int(raw) if raw else SUMMARY_EVERY
    except ValueError:
        value = SUMMARY_EVERY
    return max(1, min(value, 50))


def summary_max_chars(env: dict | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get("IOT_DIALOGUE_SUMMARY_MAX_CHARS", "")).strip()
    try:
        value = int(raw) if raw else SUMMARY_MAX_CHARS
    except ValueError:
        value = SUMMARY_MAX_CHARS
    return max(80, min(value, 2000))


def load_summary(conn: Any, user_id: str) -> tuple[str, int]:
    """``(summary, covered_count)``; ``("", 0)`` when this user has none."""

    if not user_id:
        return "", 0
    row = conn.execute(
        "SELECT summary, covered_count FROM conversation_summaries WHERE user_id=?", (user_id,)
    ).fetchone()
    if row is None:
        return "", 0
    return str(row["summary"] or ""), int(row["covered_count"] or 0)


def count_turns(conn: Any, user_id: str) -> int:
    if not user_id:
        return 0
    return int(
        conn.execute("SELECT COUNT(*) FROM conversations WHERE user_id=?", (user_id,)).fetchone()[0]
    )


def turns_between(conn: Any, user_id: str, start: int, stop: int) -> list[dict[str, str]]:
    """The user's turns ``[start, stop)`` in conversation order, oldest first.

    The ordering matches ``load_recent_turns`` exactly (``created_at, rowid``) so an index into one
    means the same turn in the other.
    """

    if stop <= start:
        return []
    rows = conn.execute(
        "SELECT input_text, response_text FROM conversations WHERE user_id=? "
        "ORDER BY created_at, rowid LIMIT ? OFFSET ?",
        (user_id, stop - start, start),
    ).fetchall()
    turns: list[dict[str, str]] = []
    for row in rows:
        text = " ".join(str(row["input_text"] or "").split())
        reply = " ".join(str(row["response_text"] or "").split())
        if text and reply:
            turns.append({"input": text, "reply": reply})
    return turns


def render_transcript(turns: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for turn in turns:
        lines.append(f"使用者：{turn['input']}")
        lines.append(f"機器人：{turn['reply']}")
    return "\n".join(lines)


def build_summary(llm: Any, previous: str, turns: list[dict[str, str]], *, limit: int | None = None) -> str:
    """One summarisation request; raises on transport failure (the caller decides what to do)."""

    prompt = PROMPT.format(
        limit=limit or summary_max_chars(),
        previous=previous or "（無）",
        transcript=render_transcript(turns),
    )
    response = llm.reply([{"role": "user", "content": prompt}], "summary") if llm is not None else {}
    if str(response.get("status") or "") != "ok":
        raise RuntimeError(f"summary_llm_{response.get('status') or 'unavailable'}")
    text = " ".join(str(response.get("text") or "").split())
    if not text:
        raise RuntimeError("summary_empty")
    cap = limit or summary_max_chars()
    return text if len(text) <= cap else text[:cap] + "…"


def save_summary(conn: Any, user_id: str, summary: str, covered: int, *, now: float | None = None) -> None:
    conn.execute(
        "INSERT INTO conversation_summaries(user_id,summary,covered_count,updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary,"
        " covered_count=excluded.covered_count, updated_at=excluded.updated_at",
        (user_id, summary, int(covered), time.time() if now is None else now),
    )
    conn.commit()


def short_term_context(
    conn: Any,
    user_id: str | None,
    *,
    llm: Any = None,
    env: dict | None = None,
    identity: Any = None,
) -> ShortTermContext:
    """Everything the prompt needs about this conversation, refreshing the summary if due.

    Returns the verbatim window **and** the summary, with the guarantee that no turn falls between
    them: while a refresh is not yet due, the backlog stays in the verbatim window.

    ``identity`` -- when given and the turn is *not* verified, the personal conversation is withheld.
    The history is the user's own words, so replaying it for a voice nobody recognised is the same
    disclosure the long-term memory gate exists to prevent.  Measured 2026-10-06: with
    ``IOT_MEMORY_REQUIRE_IDENTITY=1`` the robot still answered 「你叫劉清琪」 to another person in
    the room, because the owner's name was sitting in the last few turns of the *short-term* window
    -- the one path that flag did not cover.
    """

    if identity is not None and str(getattr(identity, "decision", "accepted")) != "accepted":
        return ShortTermContext(note="identity_required")

    window = recent_turn_limit(env)
    if not user_id or window == 0:
        # A window of 0 is "no short-term memory at all", and the summary must not sneak it back in.
        return ShortTermContext()

    total = count_turns(conn, user_id)
    summary, covered = load_summary(conn, user_id)
    covered = max(0, min(covered, total))
    backlog = max(0, total - window - covered)  # older than the window and not yet summarised
    context = ShortTermContext(summary=summary, covered=covered, total=total)

    if summary_enabled(env) and backlog >= summary_every(env) and llm is not None:
        # The oldest retired turns first, never more than one slice, so a large backlog catches up
        # over several turns instead of building a prompt the endpoint would reject.
        stop = min(total - window, covered + SUMMARY_SLICE_MAX)
        retired = turns_between(conn, user_id, covered, stop)
        try:
            context.summary = build_summary(llm, summary, retired, limit=summary_max_chars(env))
            context.covered = stop
            context.refreshed = True
            save_summary(conn, user_id, context.summary, context.covered)
            backlog = max(0, total - window - stop)
        except Exception as exc:  # noqa: BLE001 - the turn must not fail because of a summary
            context.covered = covered
            context.note = f"summary_not_refreshed:{type(exc).__name__}"
    elif summary_enabled(env) and llm is None:
        # No LLM configured (or testing): never attempt, and say why in the diagnostics.
        context.note = "summary_no_llm"

    context.history = load_recent_turns(conn, user_id, limit=min(window + backlog, MAX_TURNS))
    return context
