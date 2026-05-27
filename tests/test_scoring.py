from humanonn.models import SignalFinding
from humanonn.scoring import get_category, normalise_raw_score, score_findings
from humanonn.signals import signal_weight


def make_finding(tier: int, bucket: str = "origin", flagged: bool = True, confidence: float = 1.0) -> SignalFinding:
    return SignalFinding(
        id=f"{bucket}-t{tier}",
        name="test",
        tier=tier,  # type: ignore[arg-type]
        bucket=bucket,  # type: ignore[arg-type]
        weight=signal_weight(tier, bucket),  # type: ignore[arg-type]
        flagged=flagged,
        confidence=confidence if flagged else 0.0,
        reason="test",
        fix="test",
    )


def test_cluster_bonus_for_tier1_and_tier4() -> None:
    findings = [
        make_finding(1),
        make_finding(1),
        make_finding(1),
        make_finding(4, bucket="polish"),
        make_finding(4, bucket="polish"),
        make_finding(4, bucket="polish"),
        make_finding(4, bucket="polish"),
    ]
    score = score_findings(findings)

    assert score.base_score == 53.0
    assert score.cluster_bonus == 25
    assert score.vibe_score == 21
    assert score.humanness_score == 79


def test_normalisation_and_llm_adjustment_are_applied_after_raw_scoring() -> None:
    findings = [make_finding(1), make_finding(1), make_finding(2), make_finding(3, confidence=0.5)]
    score = score_findings(findings, score_mode="smart_llm", llm_adjustment=7)

    assert score.base_score == 41.25
    assert score.cluster_bonus == 0
    assert normalise_raw_score(score.base_score + score.cluster_bonus) == 11
    assert score.vibe_score == 18
    assert score.score_mode == "smart_llm"
    assert score.llm_adjustment == 7


def test_category_thresholds() -> None:
    assert get_category(80) == "Fully Vibe Coded"
    assert get_category(50) == "Slight Dev Effort"
    assert get_category(20) == "Decent Dev Effort"
    assert get_category(10) == "Human Built"
