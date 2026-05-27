from __future__ import annotations

from .models import ScoreSummary, SignalFinding


MAX_RAW_SCORE = 347.5


def clamp(minimum: int, maximum: int, value: int) -> int:
    return max(minimum, min(maximum, value))


def normalise_raw_score(raw_score: float) -> int:
    return clamp(0, 100, round((raw_score / MAX_RAW_SCORE) * 100))


def get_category(vibe_score: int) -> str:
    if vibe_score >= 65:
        return "Fully Vibe Coded"
    if vibe_score >= 40:
        return "Slight Dev Effort"
    if vibe_score >= 16:
        return "Decent Dev Effort"
    return "Human Built"


def score_findings(
    findings: list[SignalFinding],
    score_mode: str = "deterministic",
    llm_adjustment: float = 0.0,
) -> ScoreSummary:
    base_score = sum(f.weight * f.confidence for f in findings if f.flagged)
    tier_counts = {tier: 0 for tier in (1, 2, 3, 4)}
    for finding in findings:
        if finding.flagged:
            tier_counts[finding.tier] += 1

    cluster_bonus = 0
    if tier_counts[1] >= 3:
        cluster_bonus += 15
    if tier_counts[1] >= 1 and tier_counts[4] >= 1:
        cluster_bonus += 10

    raw_score = base_score + cluster_bonus
    normalised_score = normalise_raw_score(raw_score)
    vibe_score = clamp(0, 100, round(normalised_score + llm_adjustment))
    return ScoreSummary(
        vibe_score=vibe_score,
        humanness_score=100 - vibe_score,
        base_score=round(base_score, 2),
        cluster_bonus=cluster_bonus,
        tier_counts=tier_counts,
        score_mode=score_mode,
        llm_adjustment=round(llm_adjustment, 2),
    )
