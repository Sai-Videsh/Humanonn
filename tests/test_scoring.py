from humanonn.models import SignalFinding
from humanonn.scoring import score_findings


def make_finding(tier: int, flagged: bool = True) -> SignalFinding:
    return SignalFinding(
        id=f"t{tier}",
        name="test",
        tier=tier,  # type: ignore[arg-type]
        weight={1: 4.0, 2: 6.0, 3: 6.0, 4: 8.0}[tier],
        flagged=flagged,
        confidence=1.0 if flagged else 0.0,
        reason="test",
        fix="test",
    )


def test_cluster_bonus_for_tier1_and_tier4() -> None:
    findings = [make_finding(1), make_finding(1), make_finding(1), make_finding(4), make_finding(4), make_finding(4), make_finding(4)]
    score = score_findings(findings)

    assert score.cluster_bonus == 45
    assert score.vibe_score == 89
    assert score.humanness_score == 11

