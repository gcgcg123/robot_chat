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


def classify_scores(best: float, second: float | None, threshold: float, min_margin: float) -> str:
    if best < threshold:
        return "unknown"
    if second is not None and best - second < min_margin:
        return "ambiguous"
    return "accepted"

def identify(
    embedding: list[float],
    templates: list[dict],
    threshold: float | None = None,
    min_margin: float | None = None,
) -> IdentityResult:
    """Compare a normalized embedding to active templates.

    The PC baseline uses cosine similarity; callers can replace the provider
    and keep this decision contract unchanged.
    """
    threshold = threshold if threshold is not None else float(os.getenv("VOICEPRINT_THRESHOLD", "0.55"))
    min_margin = min_margin if min_margin is not None else float(os.getenv("VOICEPRINT_MIN_MARGIN", "0.05"))

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
    ranked = sorted(best_by_user.values(), key=lambda x: x[0], reverse=True)
    if not ranked: return IdentityResult("unknown", None, -1.0, None, None, "pc-baseline-v1")
    best, item = ranked[0]; second = ranked[1][0] if len(ranked) > 1 else None
    decision = classify_scores(best, second, threshold, min_margin)
    return IdentityResult(decision, item.get("user_id") if decision == "accepted" else None, best, second, item.get("template_id"), "pc-baseline-v1")
