from __future__ import annotations

import re

# A chunk is a window of ~334 characters, so its embedding averages several topics together --
# the same dilution the memory flywheel measured on whole-message embeddings (age memory scored
# 0.7172 against the question alone but 0.5898 against a multi-topic message). Splitting a chunk
# into sentence-sized windows and scoring it by its *best* window keeps one topic per vector.
WINDOW_CHARS = 90
MAX_WINDOWS = 8
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")


def split_windows(text: str, *, size: int = WINDOW_CHARS, limit: int = MAX_WINDOWS) -> list[str]:
    """Sentence-aligned windows of at most ``size`` characters, longest-first truncated.

    Windows never cut mid-sentence unless a single sentence is longer than ``size``, in which
    case it is kept whole: a half sentence embeds to something neither side means.
    """

    sentences = [part.strip() for part in _SENTENCE_END.split(text or "") if part.strip()]
    if not sentences:
        return []
    windows: list[str] = []
    buffer = ""
    for sentence in sentences:
        if buffer and len(buffer) + len(sentence) > size:
            windows.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
    if buffer:
        windows.append(buffer)
    return windows[:limit]
