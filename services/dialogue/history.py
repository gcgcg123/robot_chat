"""Short-term continuity: the last few exchanges of this conversation.

Why this exists. Measured on 2026-10-05, a user asked "你刚刚说了什么，我没有听清，你重新说一遍。"
and the robot answered "不好意思，我這邊其實還沒開口呢" -- because the prompt only ever carried the
current turn. Long-term memory is a *different* mechanism: the flywheel surfaces facts (name,
preferences, a safety disclosure) by relevance to the current question, so it can answer "我叫什麼
名字" but it was never going to know what was said thirty seconds ago.

Scope is the **user**, not the session. The companion follows the person: a new websocket session, a
device reboot or a different simulator should not make the robot forget what was said a minute ago.

Nothing is stored here that is not already in `conversations`; this module only reads it back.
"""
from __future__ import annotations

import os
from typing import Any

# Ten exchanges is roughly the last five minutes of an elderly user's conversation (~1,200 tokens of
# history at the 240-character clamp). It was 4, which turned out too short in use: the sixth turn
# back was already gone, so a topic could not survive a short detour. Everything older is carried by
# the rolling summary (services/dialogue/summary.py) rather than by growing this window further.
RECENT_TURNS = 10
MAX_TURNS = 40
# One turn cannot exceed the request limit (4000 chars), and a pasted essay in the history would
# push the actual question out of the model's attention. Truncate mid-turn instead of dropping it:
# the beginning of a long turn is what the next turn is usually referring to.
MAX_TURN_CHARS = 240
ELLIPSIS = "…"


def recent_turn_limit(env: dict | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get("IOT_DIALOGUE_HISTORY_TURNS", "")).strip()
    try:
        value = int(raw) if raw else RECENT_TURNS
    except ValueError:
        value = RECENT_TURNS
    return max(0, min(value, MAX_TURNS))


def _clip(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= MAX_TURN_CHARS else text[:MAX_TURN_CHARS] + ELLIPSIS


def load_recent_turns(conn: Any, user_id: str | None, *, limit: int | None = None) -> list[dict[str, str]]:
    """The most recent ``limit`` exchanges as ``[{"input": ..., "reply": ...}]``, oldest first.

    The current turn is not in here: the caller saves a conversation only after answering it.
    """

    if not user_id:
        return []
    wanted = recent_turn_limit() if limit is None else max(0, min(int(limit), MAX_TURNS))
    if wanted == 0:
        return []
    rows = conn.execute(
        "SELECT input_text, response_text FROM conversations WHERE user_id=? "
        # Tie-break on rowid: two turns inside the same second must keep their real order, and
        # `created_at DESC` alone leaves that to chance.
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (user_id, wanted),
    ).fetchall()
    turns: list[dict[str, str]] = []
    for row in reversed(rows):
        text = _clip(row["input_text"])
        reply = _clip(row["response_text"])
        if text and reply:
            turns.append({"input": text, "reply": reply})
    return turns
