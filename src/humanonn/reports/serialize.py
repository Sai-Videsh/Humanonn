from __future__ import annotations

from dataclasses import asdict
from typing import Any

from humanonn.models import AuditReport


def report_to_dict(report: AuditReport) -> dict[str, Any]:
    return {
        "url": report.url,
        "title": report.title,
        "score": asdict(report.score),
        "screenshot_path": report.screenshot_path,
        "scan_metadata": report.scan_metadata,
        "agent_notes": report.agent_notes,
        "smart_summary": report.smart_summary,
        "archetype_label": report.archetype_label,
        "dynamic_findings": report.dynamic_findings,
        "llm_evidence": report.llm_evidence,
        "findings": [asdict(finding) for finding in report.findings],
    }
