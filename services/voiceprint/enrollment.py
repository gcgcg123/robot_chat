"""Voiceprint enrollment: the shared half of a flow that now has two entry points.

The browser wizard uploads WAVs recorded by a PC microphone; a device sends Opus-decoded PCM16
straight from the microphone it will later be *verified* with. Both must write byte-identical rows
and templates, because the entire point of the device path is that the template matches that
microphone -- measured 2026-10-06, templates enrolled through a browser scored only 0.38-0.44
against the same person speaking into the ESP32 (threshold 0.55), while the browser's own samples
sat at 0.53-0.79. A second, divergent implementation of this logic would quietly reintroduce that.

Callers own the connection and the transaction; nothing here opens a database or commits except
where a single logical step needs it (`build_templates`).
"""
from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any, Iterable

from services.audio.quality import check_quality
from services.voiceprint.storage import open_sealed, seal

REQUIRED_SAMPLES = 3
STEPS = (1, 2, 3)


def steps_for(required: int | None = None) -> tuple[int, ...]:
    """The step numbers an enrollment of ``required`` samples uses.

    The browser wizard asks for :data:`REQUIRED_SAMPLES` (its UI has that many steps); the device
    path asks for more, because the board's microphone at desk distance produces looser templates
    and every extra recording is another chance for one of them to sit near where the person
    actually speaks.  Both paths still write the same rows -- only the count differs.
    """

    try:
        count = int(required) if required else REQUIRED_SAMPLES
    except (TypeError, ValueError):
        count = REQUIRED_SAMPLES
    return tuple(range(1, max(1, count) + 1))


def _cosine(a: Iterable[float], b: Iterable[float]) -> float:
    left, right = list(a), list(b)
    if not left or len(left) != len(right):
        return -1.0
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right)) or 1.0
    return sum(x * y for x, y in zip(left, right)) / denominator


def sample_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    """Cosine similarity between two embeddings, so the caller can compare samples directly."""

    return _cosine(a, b)


def saved_steps(conn: Any, user_id: str, model_version: str) -> set[int]:
    return {
        int(row[0])
        for row in conn.execute(
            "SELECT step FROM voiceprint_samples WHERE user_id=? AND model_version=?",
            (user_id, model_version),
        )
    }


def next_free_step(conn: Any, user_id: str, model_version: str, required: int | None = None) -> int | None:
    existing = saved_steps(conn, user_id, model_version)
    return next((step for step in steps_for(required) if step not in existing), None)


def embed_sample(provider: Any, audio: Any) -> list[float]:
    """Quality-gate and embed one sample; ``ValueError`` carries the rejection reason."""

    quality = check_quality(audio, enrollment=True)
    if not quality["accepted"]:
        raise ValueError(str(quality["reason"]))
    return provider.embed(audio.pcm16), quality


def conflicting_sample(
    conn: Any,
    *,
    user_id: str,
    embedding: Iterable[float],
    model_version: str,
    min_similarity: float,
) -> dict | None:
    """The already-stored sample that this recording does *not* look like, if any.

    Enrollment records whatever reaches the microphone, and "whoever is in the room" is not a
    property of the audio -- a second voice recorded as sample 3 lands in the template set and then
    matches that person at verification time.  The device path gates on this; the browser path does
    not (its chain of custody is a single person at one keyboard, and the measured same-speaker floor
    there is 0.53, so the check would mostly be a chance to reject good samples).
    """

    if min_similarity <= 0:
        return None
    stored = [
        (int(row["step"]), open_sealed(row["embedding_json"]))
        for row in conn.execute(
            "SELECT step, embedding_json FROM voiceprint_samples WHERE user_id=? AND model_version=? ORDER BY step",
            (user_id, model_version),
        )
    ]
    best: tuple[float, int] | None = None
    for step, vector in stored:
        if not vector:
            continue
        score = _cosine(embedding, vector)
        if best is None or score > best[0]:
            best = (score, step)
    if best is None:
        return None
    if best[0] >= min_similarity:
        return None
    return {"step": best[1], "score": round(best[0], 4), "min_similarity": float(min_similarity)}


def save_sample(
    conn: Any,
    *,
    user_id: str,
    step: int,
    embedding: Iterable[float],
    quality: dict,
    model_version: str,
    now: float | None = None,
) -> int:
    """Upsert one sample and return how many the user now has for this model version."""

    moment = time.time() if now is None else now
    conn.execute(
        "INSERT INTO voiceprint_samples(sample_id,user_id,step,embedding_json,quality_json,model_version,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,step) DO UPDATE SET sample_id=excluded.sample_id, "
        "embedding_json=excluded.embedding_json, quality_json=excluded.quality_json, "
        "model_version=excluded.model_version, updated_at=excluded.updated_at",
        (
            str(uuid.uuid4()),
            user_id,
            int(step),
            seal(list(embedding)),
            json.dumps(quality, ensure_ascii=False),
            model_version,
            moment,
            moment,
        ),
    )
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM voiceprint_samples WHERE user_id=? AND model_version=?",
            (user_id, model_version),
        ).fetchone()[0]
    )


def take_sample(
    conn: Any,
    *,
    user_id: str,
    provider: Any,
    audio: Any,
    step: int | None = None,
    now: float | None = None,
    required: int | None = None,
    min_similarity: float = 0.0,
) -> dict:
    """The whole single-sample step: gate, embed, store at the next free step.

    Returns ``{"step", "sample_count", "quality"}``; raises ``ValueError`` when the audio is
    rejected (``too_short``/``no_speech``/``clipping``/``speaker_mismatch``/``invalid_sample_step``)
    so a device can ask the user to say it again *without* consuming a step.
    """

    model_version = str(getattr(provider, "model_version", "") or "")
    allowed = steps_for(required)
    existing = saved_steps(conn, user_id, model_version)
    target = step if step is not None else next((s for s in allowed if s not in existing), None)
    if target is None or (target not in allowed):
        raise ValueError("invalid_sample_step")
    if target not in existing and target != next((s for s in allowed if s not in existing), None):
        raise ValueError("invalid_sample_step")
    embedding, quality = embed_sample(provider, audio)
    conflict = conflicting_sample(
        conn,
        user_id=user_id,
        embedding=embedding,
        model_version=model_version,
        min_similarity=min_similarity,
    )
    if conflict is not None:
        # Deliberately after the quality gate and before the write: nothing is stored, so the step
        # stays free and the operator can simply say the sentence again.
        raise ValueError("speaker_mismatch")
    count = save_sample(
        conn,
        user_id=user_id,
        step=target,
        embedding=embedding,
        quality=quality,
        model_version=model_version,
        now=now,
    )
    return {"step": target, "sample_count": count, "quality": quality, "model_version": model_version}


def build_templates(
    conn: Any,
    *,
    user_id: str,
    provider: Any,
    language: str | None = None,
    now: float | None = None,
    required: int | None = None,
    keep_previous: bool = False,
) -> dict:
    """Turn the stored samples into active templates: one per sample.

    One template per recording, not a single averaged vector, because matching takes the best score
    over templates -- keeping each recording preserves the variation between them. (The old code in
    ``app.py`` computed an average and then never stored it; this function drops that dead
    computation rather than quietly adding a fourth row that the browser flow never had.)

    ``keep_previous`` leaves the user's existing templates active instead of retiring them, which is
    what makes one person's browser enrollment and device enrollment coexist. It is *off* by default
    and deliberately so: matching takes the maximum over a user's template set, so every extra
    template is another chance for a wrong voice to clear the threshold, and the measured benefit of
    the cross-channel set is negative on this hardware (browser-enrolled templates scored 0.38-0.44
    against the same person on the board, below any usable threshold -- 2026-10-06).
    """

    model_version = str(getattr(provider, "model_version", "") or "")
    rows = list(
        conn.execute(
            "SELECT step, embedding_json FROM voiceprint_samples WHERE user_id=? AND model_version=? ORDER BY step",
            (user_id, model_version),
        )
    )
    needed = len(steps_for(required))
    if len(rows) < needed:
        raise ValueError("insufficient_samples")
    vectors = [open_sealed(row["embedding_json"]) for row in rows]
    if any(not vector for vector in vectors):
        raise ValueError("empty_embedding")

    moment = time.time() if now is None else now
    template_ids = [str(uuid.uuid4()) for _ in vectors]
    if not keep_previous:
        conn.execute("UPDATE voiceprint_templates SET active=0 WHERE user_id=?", (user_id,))
    if language:
        conn.execute("UPDATE users SET enrollment_language=? WHERE user_id=?", (language, user_id))
    for template_id, vector in zip(template_ids, vectors):
        conn.execute(
            "INSERT INTO voiceprint_templates(template_id,user_id,embedding_json,model_version,active,created_at) VALUES(?,?,?,?,?,?)",
            (template_id, user_id, seal(vector), model_version, 1, moment),
        )
    conn.commit()
    return {
        "user_id": user_id,
        "template_id": template_ids[0],
        "template_ids": template_ids,
        "model_version": model_version,
        "samples": len(rows),
        "kept_previous": bool(keep_previous),
    }
