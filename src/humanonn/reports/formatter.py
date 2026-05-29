from __future__ import annotations

from humanonn.models import AuditReport


def format_console_report(report: AuditReport, show_all: bool = False) -> str:
    flagged = [finding for finding in report.findings if finding.flagged]
    visible_findings = report.findings if show_all else flagged
    lines = [
        "",
        "Humanonn Audit",
        "==============",
        f"URL: {report.url}",
        f"Title: {report.title or '(untitled)'}",
        f"Vibe Score: {report.score.vibe_score}/100",
        f"Humanness Score: {report.score.humanness_score}/100",
        f"Base Score: {report.score.base_score}",
        f"Cluster Bonus: {report.score.cluster_bonus}",
        f"Score Mode: {report.score.score_mode}",
        f"Flagged Signals: {len(flagged)}/{len(report.findings)}",
        f"Screenshot: {report.screenshot_path or '(not captured)'}",
    ]
    if report.scan_metadata:
        lines.extend(
            [
                f"Verified Components: {report.scan_metadata.get('verified_components', 0)}",
                f"Style-Verified Components: {report.scan_metadata.get('style_verified_components', 0)}",
                f"Unverified Components: {report.scan_metadata.get('unverified_components', 0)}",
                f"Interaction Coverage: {report.scan_metadata.get('interaction_coverage_ratio', 0)}",
            ]
        )
    lines.extend(
        [
            "",
            "Tier Counts:",
            *[f"  Tier {tier}: {count}" for tier, count in report.score.tier_counts.items()],
            "",
            "Findings:",
        ]
    )
    if not visible_findings:
        lines.append("  No rule-based signals were flagged.")
    for finding in visible_findings:
        status = "FLAGGED" if finding.flagged else "clear"
        lines.extend(
            [
                f"  [{status}] T{finding.tier} {finding.name} ({finding.id})",
                f"    Reason: {finding.reason}",
                f"    Fix: {finding.fix}",
            ]
        )
    if report.agent_notes:
        lines.extend(["", "Agent Notes:", *[f"  - {note}" for note in report.agent_notes]])
    if report.smart_summary:
        lines.extend(["", "Smart Summary:", f"  {report.smart_summary}"])
    if report.dynamic_findings:
        lines.extend(["", "Dynamic Findings:"])
        for item in report.dynamic_findings[:8]:
            lines.append(f"  - {item.get('label', 'Finding')}: {item.get('reason', '')}")
    return "\n".join(lines)
