from __future__ import annotations

from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class IdentityResult:
    decision: str
    user_id: str | None
    best_score: float
    second_score: float | None
    template_id: str | None
    provider: str
    #: Who the runner-up was, not just how close they were.  A rejected match that was *almost*
    #: another enrolled user's template is a different situation from one that matched nobody, and
    #: only the id tells those apart after the fact (measured 2026-10-06 on real hardware: an
    #: unverified utterance scored 0.28 against the best template -- belonging to a *different*
    #: enrolled user than the device's binding, which no score-only log could show).
    second_user_id: str | None = None


def classify_scores(best: float, second: float | None, threshold: float, min_margin: float) -> str:
    if best < threshold:
        return "unknown"
    if second is not None and best - second < min_margin:
        return "ambiguous"
    return "accepted"

def effective_threshold(env: dict | None = None) -> float:
    """The acceptance threshold actually in force, so callers can report it instead of guessing."""

    source = os.environ if env is None else env
    try:
        return float(str(source.get("VOICEPRINT_THRESHOLD", "")).strip() or 0.55)
    except ValueError:
        return 0.55


def effective_min_margin(env: dict | None = None) -> float:
    source = os.environ if env is None else env
    try:
        return float(str(source.get("VOICEPRINT_MIN_MARGIN", "")).strip() or 0.05)
    except ValueError:
        return 0.05


def identify(
    embedding: list[float],
    templates: list[dict],
    threshold: float | None = None,
    min_margin: float | None = None,
    provider: str = "ecapa-voxceleb-v1",
) -> IdentityResult:
    """Compare a normalized embedding to active templates.

    The PC baseline uses cosine similarity; callers can replace the provider
    and keep this decision contract unchanged.
    """
    threshold = threshold if threshold is not None else effective_threshold()
    min_margin = min_margin if min_margin is not None else effective_min_margin()

    def score(candidate):
        values = candidate.get("embedding", candidate.get("vector", []))
        if not values or len(values) != len(embedding): return -1.0
        den = math.sqrt(sum(x*x for x in embedding) * sum(x*x for x in values)) or 1.0
        return sum(a*b for a,b in zip(embedding, values)) / den
    best_by_user = {}
    for template in templates:
        if not template.get("active", True):
            continue
        candidate_score = score(template)
        user_id = template.get("user_id")
        if not user_id:
            continue
        previous = best_by_user.get(user_id)
        if previous is None or candidate_score > previous[0]:
            best_by_user[user_id] = (candidate_score, template)
    ranked = sorted(best_by_user.items(), key=lambda item: item[1][0], reverse=True)
    if not ranked: return IdentityResult("unknown", None, -1.0, None, None, provider)
    best_user, (best, item) = ranked[0]
    second_user, second_item = ranked[1] if len(ranked) > 1 else (None, None)
    second = second_item[0] if second_item is not None else None
    decision = classify_scores(best, second, threshold, min_margin)
    return IdentityResult(
        decision,
        item.get("user_id") if decision == "accepted" else None,
        best,
        second,
        item.get("template_id"),
        provider,
        second_user if second_item is not None else None,
    )
