from __future__ import annotations

from .models import ScoreSummary, SignalFinding


def score_findings(findings: list[SignalFinding]) -> ScoreSummary:
    base_score = sum(f.weight * f.confidence for f in findings if f.flagged)
    tier_counts = {tier: 0 for tier in (1, 2, 3, 4)}
    for finding in findings:
        if finding.flagged:
            tier_counts[finding.tier] += 1

    cluster_bonus = 0
    if tier_counts[1] >= 3:
        cluster_bonus += 15
    if tier_counts[4] >= 4:
        cluster_bonus += 20
    if tier_counts[1] > 0 and tier_counts[4] > 0:
        cluster_bonus += 10

    vibe_score = min(100, round(base_score + cluster_bonus))
    return ScoreSummary(
        vibe_score=vibe_score,
        humanness_score=100 - vibe_score,
        base_score=round(base_score, 2),
        cluster_bonus=cluster_bonus,
        tier_counts=tier_counts,
    )

