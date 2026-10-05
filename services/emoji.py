"""The one emoji that drives the device's face.

Upstream does not classify the user's mood to pick an expression.  It asks the
model to prefix its *own* reply with exactly one emoji, then scans the first
streamed chunk for a character it recognises and forwards the mapped emotion
name to the firmware (``core/utils/textUtils.py::get_emotion`` in
``xiao-zhi-esp32-server``)::

    {"type": "llm", "text": "😊", "emotion": "happy", "session_id": "..."}

The table below is upstream's ``EMOJI_MAP`` verbatim -- it has to be, because it
is the intersection of two things that live outside this service: the emoji the
model is allowed to use, and the names the firmware's ``SetEmotion`` can render
(``main/display/emoji_collection.cc``).

Leaf module on purpose: the prompt builder, the TTS sentence splitter and the
device gateway all need it, and none of them may import each other.
"""
from __future__ import annotations

#: Emoji -> firmware emotion name.  Upstream ``EMOJI_MAP``, order preserved
#: because the same order is what the prompt shows the model as a whitelist.
EMOJI_TO_EMOTION: dict[str, str] = {
    "😶": "neutral",
    "🙂": "happy",
    "😆": "laughing",
    "😂": "funny",
    "😔": "sad",
    "😠": "angry",
    "😭": "crying",
    "😍": "loving",
    "😳": "embarrassed",
    "😲": "surprised",
    "😱": "shocked",
    "🤔": "thinking",
    "😉": "winking",
    "😎": "cool",
    "😌": "relaxed",
    "🤤": "delicious",
    "😘": "kissy",
    "😏": "confident",
    "😴": "sleepy",
    "😜": "silly",
    "🙄": "confused",
}

#: The whitelist as one string, ready to drop into the system prompt.
EMOJI_WHITELIST = "".join(EMOJI_TO_EMOTION)

#: Upstream's ``EMOJI_RANGES``.  Used to strip a leading expression even when the
#: model ignored the whitelist -- an unknown emoji must not reach the synthesiser.
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
    (0xFE0F, 0xFE0F),
)

#: Zero-width joiner: glues the pieces of a composite emoji together, so it has
#: to be swallowed with them rather than left behind on its own.
_ZWJ = "\u200d"


def is_emoji(char: str) -> bool:
    if not char:
        return False
    point = ord(char)
    return any(low <= point <= high for low, high in _EMOJI_RANGES)


def emotion_from_emoji(text: str) -> tuple[str, str] | None:
    """Return ``(emoji, emotion_name)`` for the first *known* emoji in ``text``.

    ``None`` means the model did not lead with one of ours -- a kaomoji, an
    unknown emoji or no emoji at all.  The caller decides what to fall back to;
    silently pretending it said ``🙂`` is how every reply ends up looking happy.
    """
    for char in text or "":
        emotion = EMOJI_TO_EMOTION.get(char)
        if emotion is not None:
            return char, emotion
    return None


def strip_leading_emoji(text: str) -> str:
    """Drop the leading expression so the speaker never reads it aloud.

    Only the front is touched, and only once: an emoji further into the reply is
    the model being chatty, and that one is stripped by the TTS provider anyway.
    Recognised emoji *and* unrecognised ones are both removed -- agreeing with
    the whitelist is what decides the face, not whether a character gets spoken.
    """
    stripped = (text or "").lstrip()
    if not stripped or not is_emoji(stripped[0]):
        return text
    index = 0
    while index < len(stripped) and (is_emoji(stripped[index]) or stripped[index] == _ZWJ):
        index += 1
    return stripped[index:].lstrip()
