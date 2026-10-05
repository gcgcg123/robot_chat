"""Sentence segmentation shared by captions and speech synthesis.

Two readers, two shapes of the same problem:

* the *batch* splitter (:func:`split_speech`) takes a finished reply and cuts it
  into speakable pieces;
* the *streaming* splitter (:class:`SentenceStreamer`) takes model deltas and
  hands out a sentence the moment it is complete, so synthesising sentence 1
  overlaps the model still writing sentence 2.

Both cap the length of a piece.  Without a cap a model that forgets punctuation
produces one enormous "sentence", which becomes one enormous edge-tts request,
hits the provider timeout, and leaves the device silent halfway through a reply.
"""
from __future__ import annotations

import re

from services.emoji import strip_leading_emoji

#: Hard terminators.  Everything up to and including one of these is a complete
#: utterance and may be synthesised on its own.
TERMINATORS = "。！？!?；;…\n"

#: Softer break points, consulted only when a run grows past ``max_chars``
#: without a hard terminator.  Cutting at a comma reads far better than cutting
#: at an arbitrary character.
SOFT_BREAKS = "，,、：:）)】」》」 　"

#: The prompt asks the model to append a fenced ```json … ``` trailer carrying
#: emotion / risk / memory metadata.  A streaming reader sees it *after* the
#: last real sentence, so it has to be recognised and withheld from the speaker.
FENCE = "```"

#: Bare (unfenced) metadata is the prompt's fallback shape; seeing one of its
#: keys is the only reliable signal that the block has started.
METADATA_KEYS = ('"emotion"', '"risk_evidence"', '"remember"', '"risk"')

#: Characters that appear in JSON but essentially never at the tail of a spoken
#: sentence -- used to decide whether a trailing fragment is already metadata.
_JSON_ISH = set('{}":,')

_MIN_CHARS_FLOOR = 8


def strip_markup(text: str) -> str:
    """Remove markdown decoration that must never be read aloud."""
    plain = re.sub(r"```.*?```", "", text or "", flags=re.S)
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", plain)
    plain = re.sub(r"[*_`#~]", "", plain)
    return plain


def _split_long(part: str, max_chars: int) -> list[str]:
    """Break a run with no hard terminator into pieces of at most ``max_chars``."""
    part = part.strip()
    if not part:
        return []
    if max_chars <= 0 or len(part) <= max_chars:
        return [part]
    pieces: list[str] = []
    rest = part
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max((window.rfind(char) for char in SOFT_BREAKS), default=-1)
        # A delimiter too close to the start would make a useless two-word
        # fragment; fall back to a hard cut instead.
        if cut < max(_MIN_CHARS_FLOOR, max_chars // 3):
            cut = max_chars - 1
        pieces.append(rest[: cut + 1].strip())
        rest = rest[cut + 1 :]
    if rest.strip():
        pieces.append(rest.strip())
    return [piece for piece in pieces if piece]


def split_speech(text: str, *, max_chars: int = 48) -> list[str]:
    """Return readable sentence chunks shared by captions and TTS."""
    plain = strip_leading_emoji(re.sub(r"\s+", " ", strip_markup(text)).strip())
    if not plain:
        return []
    parts: list[str] = []
    for raw in re.findall(rf"[^{re.escape(TERMINATORS)}]+[{re.escape(TERMINATORS)}]?", plain):
        parts.extend(_split_long(raw, max_chars))
    return parts


class SentenceStreamer:
    """Turn a token stream into speakable sentences, one at a time.

    ``feed`` is called with each model delta and returns whatever became
    speakable; ``flush`` returns the tail when the model stops.  Both are
    cheap and safe to call from the thread that drives the model, which is why
    the streamer itself is deliberately free of any I/O.
    """

    def __init__(self, *, max_chars: int = 48, min_chars: int = 6) -> None:
        self.max_chars = max(12, int(max_chars or 48))
        self.min_chars = max(1, int(min_chars or 1))
        self._pending = ""
        self._suppressed = False
        self._emitted = 0
        self._head_cleaned = False

    # ------------------------------------------------------------------ probes

    @property
    def emitted(self) -> int:
        """How many sentences have been handed out so far."""
        return self._emitted

    @property
    def suppressed(self) -> bool:
        """True once the trailing metadata block has been recognised."""
        return self._suppressed

    # ------------------------------------------------------------------ feeding

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        if self._suppressed:
            return []
        self._pending += delta
        self._note_metadata()
        return self._drain(final=self._suppressed)

    def flush(self) -> list[str]:
        """Emit the tail; call once the model has finished the turn."""
        if self._suppressed:
            self._pending = ""
            return []
        return self._drain(final=True)

    def reset(self) -> None:
        self._pending = ""
        self._suppressed = False
        self._emitted = 0
        self._head_cleaned = False

    # ------------------------------------------------------------------ internals

    def _note_metadata(self) -> None:
        """Truncate at the first metadata marker and refuse to speak past it."""
        marker_at = -1
        for marker in (FENCE, *METADATA_KEYS):
            found = self._pending.find(marker)
            if found >= 0 and (marker_at < 0 or found < marker_at):
                marker_at = found
        if marker_at < 0:
            return
        head = self._pending[:marker_at]
        # Keep the last *complete* sentence; a half-written one sitting directly
        # before the block ("…不错{\"emo") would otherwise be read out as "{".
        tail_start = max((head.rfind(char) for char in TERMINATORS), default=-1) + 1
        if any(char in _JSON_ISH for char in head[tail_start:]):
            head = head[:tail_start]
        self._pending = head
        self._suppressed = True

    def _cut_index(self) -> int | None:
        """Index just past the first piece worth speaking, or ``None`` to wait.

        The minimum-length rule exists to stop a reply being chopped into
        two-word fragments, but it must not delay the *opening* sentence: time
        to first audio is the latency the user actually feels, so the very first
        piece is released at the first terminator whatever its length.
        """
        floor = 2 if self._emitted == 0 else self.min_chars
        for index, char in enumerate(self._pending):
            if char in TERMINATORS and index + 1 >= floor:
                return index + 1
        if len(self._pending) >= self.max_chars:
            window = self._pending[: self.max_chars]
            cut = max((window.rfind(char) for char in SOFT_BREAKS), default=-1)
            if cut < max(_MIN_CHARS_FLOOR, self.max_chars // 3):
                cut = self.max_chars - 1
            return cut + 1
        return None

    def _drain(self, *, final: bool) -> list[str]:
        if self._pending and not self._head_cleaned:
            # The prompt makes the model prefix its reply with one emoji so the
            # LCD can pick an expression; that emoji must not be synthesised.
            # Done here rather than per-delta because a delta boundary can fall
            # anywhere -- by the time a sentence is cut, the emoji has arrived.
            self._pending = strip_leading_emoji(self._pending)
            self._head_cleaned = True
        picked: list[str] = []
        while True:
            cut = self._cut_index()
            if cut is None or cut <= 0:
                break
            piece = self._pending[:cut].strip()
            self._pending = self._pending[cut:]
            if piece:
                picked.append(piece)
        if final:
            tail = self._pending.strip()
            self._pending = ""
            picked.extend(_split_long(tail, self.max_chars))
        self._emitted += len(picked)
        return picked
