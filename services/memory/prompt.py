from __future__ import annotations


def render_context(chunks) -> str:
    if not chunks:
        return "（沒有可用的核准記憶）"
    return "\n".join(f"[不可信參考 {c.source_id}] {c.text}" for c in chunks)
