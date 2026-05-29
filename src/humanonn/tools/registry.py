from __future__ import annotations

from dataclasses import asdict
from typing import Any

from humanonn.config import Settings
from humanonn.models import AuditReport, AuditSnapshot
from humanonn.reports.serialize import report_to_dict
from humanonn.runtime import terminal_log
from humanonn.rules import evaluate_rules
from humanonn.scoring import score_findings

from .browser import analyze_copy, analyze_layout, check_accessibility, crawl_page, inspect_elements, to_jsonable


class ToolRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.snapshot: AuditSnapshot | None = None
        self.report: AuditReport | None = None

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "crawl_page":
            self.snapshot = crawl_page(arguments["url"], self.settings)
            return self._snapshot_summary(self.snapshot)
        if self.snapshot is None:
            raise RuntimeError("crawl_page must run before other tools.")
        if name == "inspect_elements":
            return to_jsonable(inspect_elements(self.snapshot, arguments.get("selectors", [])))
        if name == "analyze_layout":
            return to_jsonable(analyze_layout(self.snapshot))
        if name == "check_accessibility":
            return to_jsonable(check_accessibility(self.snapshot))
        if name == "analyze_copy":
            return to_jsonable(analyze_copy(self.snapshot))
        if name == "generate_report":
            return report_to_dict(self.generate_report())
        raise ValueError(f"Unknown tool: {name}")

    def generate_report(self) -> AuditReport:
        if self.snapshot is None:
            raise RuntimeError("Cannot generate report before crawl_page.")
        terminal_log("Evaluating rule-based signal set", self.settings.terminal_logs)
        findings = evaluate_rules(self.snapshot)
        score = score_findings(findings)
        flagged = sum(1 for finding in findings if finding.flagged)
        terminal_log(
            f"Rule evaluation complete: {flagged}/{len(findings)} signals flagged, vibe score {score.vibe_score}",
            self.settings.terminal_logs,
        )
        self.report = AuditReport(
            url=self.snapshot.url,
            title=self.snapshot.title,
            score=score,
            findings=findings,
            screenshot_path=self.snapshot.screenshot_path,
            scan_metadata=self.snapshot.raw.get("scan_metadata", {}),
        )
        return self.report

    def _snapshot_summary(self, snapshot: AuditSnapshot) -> dict[str, Any]:
        return to_jsonable(
            {
                "url": snapshot.url,
                "title": snapshot.title,
                "screenshot_path": snapshot.screenshot_path,
                "fonts": snapshot.fonts,
                "colors": snapshot.colors.get("all", [])[:16],
                "counts": {
                    "buttons": len(snapshot.buttons),
                    "inputs": len(snapshot.inputs),
                    "links": len(snapshot.links),
                    "headings": len(snapshot.headings),
                    "sections": len(snapshot.sections),
                    "images": len(snapshot.images),
                },
                "body": snapshot.body,
                "sample_buttons": snapshot.buttons[:8],
                "sample_headings": snapshot.headings[:8],
            }
        )
