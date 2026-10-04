from __future__ import annotations


def render_context(chunks) -> str:
    if not chunks:
        return "（沒有可用的核准記憶）"
    return "\n".join(f"[不可信參考 {c.source_id}] {c.text}" for c in chunks)


def render_excerpts(chunks) -> str:
    """Reference excerpts **without** their source label.

    The label is not lost -- callers return it separately as ``citations``, and the dashboard
    shows it there -- but leaving it in the prompt is what made a spoken answer say
    "（《溝通手冊》p18）" out loud: measured in a live turn on 2026-10-03, the model copied the
    bracket verbatim into its reply. A companion robot reading page numbers aloud is not what a
    spoken answer is for, so the prompt carries content only.
    """

    if not chunks:
        return "（沒有可用的節錄）"
    return "\n".join(f"- {c.text}" for c in chunks)
