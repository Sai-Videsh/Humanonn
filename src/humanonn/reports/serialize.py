from __future__ import annotations

from dataclasses import asdict
from typing import Any

from humanonn.models import AuditReport


def _audit_summary_lines(report: AuditReport) -> list[str]:
    score_block = report.score
    source_code = report.source_code if isinstance(report.source_code, dict) else {}
    report_url = report.url
    report_title = report.title or "(untitled)"
    category = score_block.category or "Human Built"
    vibe = score_block.vibe_score
    rendered_vibe = score_block.rendered_vibe_score
    source_code_score = score_block.source_code_score
    humanness = score_block.humanness_score
    base_score = score_block.base_score
    cluster_bonus = score_block.cluster_bonus
    score_mode = score_block.score_mode or "—"

    lines = [
        "Humanonn Audit",
        "==============",
        f"URL: {report_url}",
        f"Title: {report_title}",
        f"Category: {category}",
        f"Merged Vibe Score: {vibe}/100",
        f"Source Code Score: {source_code_score}/100",
        f"Humanness Score: {humanness}/100",
        f"Base Score: {base_score}",
        f"Cluster Bonus: {cluster_bonus}",
        f"Score Mode: {score_mode}",
    ]
    if score_mode == "source_only":
        lines.insert(6, "Rendered Vibe Score: —/100")
        lines.insert(7, "Source-only mode: live site scraping is disabled; score is driven by source-code findings.")
    elif rendered_vibe is not None or source_code_score:
        lines.insert(6, f"Rendered Vibe Score: {rendered_vibe if rendered_vibe is not None else vibe}/100")

    if source_code:
        repo_url = source_code.get("repo_url", "—")
        files_scanned = source_code.get("files_scanned", 0)
        lines.extend(
            [
                f"Repo: {repo_url}",
                f"Files Scanned: {files_scanned}",
                f"Source Code Score: +{source_code.get('source_code_score', 0)}",
            ]
        )
    return lines


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
        return [*_audit_summary_lines(report), *list(report.scan_code_log)]
    source_code = report.source_code if isinstance(report.source_code, dict) else {}
    logs = source_code.get("scan_log") if isinstance(source_code.get("scan_log"), list) else []
    return [*_audit_summary_lines(report), *[str(line) for line in logs]]


def report_to_dict(report: AuditReport) -> dict[str, Any]:
    # For source-only scans, avoid including live-only fields like screenshot and zeroed scan_metadata
    scan_mode = report.scan_metadata.get("scan_mode") if isinstance(report.scan_metadata, dict) else None
    is_source_only = scan_mode == "source_only"

    out: dict[str, Any] = {
        "url": report.url,
        "title": report.title,
        "score": asdict(report.score),
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

    if not is_source_only:
        out["screenshot_path"] = report.screenshot_path
        # include only scan_metadata entries that are truthy/non-zero
        if report.scan_metadata:
            filtered_meta = {k: v for k, v in report.scan_metadata.items() if v not in (None, "", 0, [], {})}
            out["scan_metadata"] = filtered_meta

    return out


def split_report_logs(report: AuditReport) -> tuple[list[str], list[str]]:
    return _derive_live_log(report), _derive_code_log(report)
