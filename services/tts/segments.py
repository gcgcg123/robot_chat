from __future__ import annotations

import re


def split_speech(text: str) -> list[str]:
    """Return readable sentence chunks shared by captions and TTS."""
    plain = re.sub(r"```.*?```", "", text or "", flags=re.S)
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", plain)
    plain = re.sub(r"[*_`#~]", "", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return []
    parts = [p.strip() for p in re.findall(r"[^。！？!?]+[。！？!?]?", plain) if p.strip()]
    return parts
