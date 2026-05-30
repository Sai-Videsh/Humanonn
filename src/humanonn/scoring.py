from __future__ import annotations

from .models import ScoreSummary, SignalFinding
from .signals import SIGNALS


MAX_RAW_SCORE = sum(signal.weight for signal in SIGNALS)


def clamp(minimum: int, maximum: int, value: int) -> int:
    return max(minimum, min(maximum, value))


def normalise_raw_score(raw_score: float) -> int:
    return clamp(0, 100, round((raw_score / MAX_RAW_SCORE) * 100))


def get_category(
    final_score: int,
    live_tier_counts: dict[int, int] | None = None,
    source_tier_counts: dict[int, int] | None = None,
) -> str:
    live_tier_counts = live_tier_counts or {}
    source_tier_counts = source_tier_counts or {}
    live_pressure = _tier_pressure(live_tier_counts)
    source_pressure = _tier_pressure(source_tier_counts)

    live_strong = live_pressure >= 12
    live_moderate = 7 <= live_pressure <= 11
    live_weak = 3 <= live_pressure <= 6 # this var not used yet
    source_strong = source_pressure >= 12
    source_moderate = 7 <= source_pressure <= 11
    source_weak = 3 <= source_pressure <= 6

    if 70 <= final_score <= 100 or (62 <= final_score <= 69 and source_strong and live_strong):
        return "Fully Vibe Coded"
    if 55 <= final_score <= 69 or (48 <= final_score <= 54 and source_moderate and live_moderate):
        return "Mostly Vibe Coded"
    if 35 <= final_score <= 54 or (30 <= final_score <= 34 and (live_moderate or source_moderate)):
        return "Vibe Coded With Human Polish"
    if 18 <= final_score <= 34 or (13 <= final_score <= 17 and source_weak):
        return "Slight Dev Effort"
    return "Human Built"


def _tier_pressure(tier_counts: dict[int, int]) -> int:
    return (
        4 * int(tier_counts.get(1, 0) or 0)
        + 3 * int(tier_counts.get(2, 0) or 0)
        + 2 * int(tier_counts.get(3, 0) or 0)
        + 1 * int(tier_counts.get(4, 0) or 0)
    )


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
    vibe_score = _apply_calibrated_verdict_bounds(vibe_score, findings)
    return ScoreSummary(
        vibe_score=vibe_score,
        humanness_score=100 - vibe_score,
        category=get_category(vibe_score, tier_counts, None),
        base_score=round(base_score, 2),
        cluster_bonus=cluster_bonus,
        tier_counts=tier_counts,
        score_mode=score_mode,
        llm_adjustment=round(llm_adjustment, 2),
    )


def _apply_calibrated_verdict_bounds(vibe_score: int, findings: list[SignalFinding]) -> int:
    flagged = [finding for finding in findings if finding.flagged]
    tier1_origin = [
        finding
        for finding in flagged
        if finding.tier == 1 and finding.bucket == "origin" and finding.id != "vibe_builder_domain"
    ]
    high_conf_tier1_origin = [finding for finding in tier1_origin if finding.confidence >= 0.8]
    code_dom_agreements = [
        finding
        for finding in flagged
        if finding.bucket == "origin" and finding.evidence.get("source_code_agreement")
    ]
    has_builder_domain = any(finding.id == "vibe_builder_domain" for finding in flagged)

    floor = 0
    if len(high_conf_tier1_origin) >= 3:
        floor = max(floor, 50)
    if len(high_conf_tier1_origin) >= 4:
        floor = max(floor, 65)
    if len(code_dom_agreements) >= 2:
        floor = max(floor, 60)
    if len(code_dom_agreements) >= 3:
        floor = max(floor, 70)
    if has_builder_domain:
        floor = max(floor, 45)
    if has_builder_domain and len(high_conf_tier1_origin) >= 2:
        floor = max(floor, 60)

    bounded_score = max(vibe_score, floor)
    if not tier1_origin and not has_builder_domain:
        bounded_score = min(bounded_score, 39)
    return clamp(0, 100, bounded_score)
