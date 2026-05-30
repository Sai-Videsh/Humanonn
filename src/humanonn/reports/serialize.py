from __future__ import annotations

from dataclasses import asdict
from typing import Any

from humanonn.models import AuditReport


def _derive_live_log(report: AuditReport) -> list[str]:
    if report.scan_live_log:
        return list(report.scan_live_log)
    logs = []
    for note in report.agent_notes:
        if note.startswith("Source code "):
            continue
        if note.startswith("Boosted DOM confidence"):
            continue
        if note.startswith("Added normalized source code score"):
            continue
        logs.append(note)
    return logs


def _derive_code_log(report: AuditReport) -> list[str]:
    if report.scan_code_log:
        return list(report.scan_code_log)
    source_code = report.source_code if isinstance(report.source_code, dict) else {}
    logs = source_code.get("scan_log") if isinstance(source_code.get("scan_log"), list) else []
    return [str(line) for line in logs]


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
        "source_code": report.source_code,
        "scan_live_log": _derive_live_log(report),
        "scan_code_log": _derive_code_log(report),
        "findings": [asdict(finding) for finding in report.findings],
    }


def split_report_logs(report: AuditReport) -> tuple[list[str], list[str]]:
    return _derive_live_log(report), _derive_code_log(report)
