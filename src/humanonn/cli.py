from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from humanonn.agent import HumanonnAgent
from humanonn.config import load_settings
from humanonn.reports import format_console_report, report_to_dict, split_report_logs
from humanonn.runtime import tee_output
from humanonn.source_code import apply_source_code_score, build_source_only_report
from humanonn.tools.browser import _artifact_root


_SOURCE_SCAN_START = "Starting source-code scan"
_REPORT_START = "Humanonn Audit"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="humanonn", description="Audit a deployed website for vibe-coded design patterns.")
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scan a deployed URL.")
    scan.add_argument("url", nargs="?", default="", help="Deployed URL to audit.")
    scan.add_argument("--json", dest="json_path", help="Write the full report JSON to this path.")
    scan.add_argument("--all", action="store_true", help="Show clear and flagged findings in console output.")
    scan.add_argument("--quiet", action="store_true", help="Disable scan progress logs in the terminal.")
    scan.add_argument("--no-llm", action="store_true", help="Disable all model calls and run deterministic scanning only.")
    scan.add_argument("--source-only", action="store_true", help="Skip live crawling and run source-code scoring only.")
    scan.add_argument(
        "--repo-url",
        dest="repo_url",
        help="Public GitHub repo URL for source-code scoring (frontend Next.js/React/Tailwind repos only; scans frontend files).",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command != "scan":
        parser.print_help()
        return

    settings = load_settings()
    if args.no_llm:
        settings = replace(settings, force_no_llm=True)
    if args.quiet:
        settings = replace(settings, terminal_logs=False)
    log_path = _resolve_log_path(args.url, settings, args.json_path)
    with tee_output(log_path):
        try:
            source_only_mode = args.source_only or not args.url.strip()
            if source_only_mode:
                report = build_source_only_report(args.url or "source-only", args.repo_url, settings=settings)
                report.scan_metadata.setdefault("scan_mode", "source_only")
            elif settings.live_site_scraping_enabled:
                agent = HumanonnAgent(settings)
                report = agent.scan(args.url)
                report = apply_source_code_score(report, args.repo_url, settings=settings)
                report.scan_metadata.setdefault("scan_mode", "combined" if args.repo_url else "live_only")
            else:
                report = build_source_only_report(args.url, args.repo_url, settings=settings)
                report.scan_metadata.setdefault("scan_mode", "source_only")
        except RuntimeError as exc:
            parser.exit(1, f"Humanonn scan failed: {exc}\n")

        print(format_console_report(report, show_all=args.all))
        if args.json_path:
            report.scan_live_log = _extract_live_log_lines(log_path, report.scan_metadata.get("scan_mode") if isinstance(report.scan_metadata, dict) else None)
            path = Path(args.json_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            report_dict = report_to_dict(report)
            path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
            live_log, code_log = split_report_logs(report)
            _write_text_log(path.with_name("scan_live.log"), live_log)
            _write_text_log(path.with_name("scan_code.log"), code_log)
            print(f"\nJSON report written to {path}")


def _resolve_log_path(url: str, settings, json_path: str | None) -> Path:
    if json_path:
        return Path(json_path).with_name("scan.log")
    return _artifact_root(url, settings) / "scan.log"


def _write_text_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(str(line) for line in lines)
    if body:
        body += "\n"
    path.write_text(body, encoding="utf-8")


def _extract_live_log_lines(log_path: Path, scan_mode: str | None) -> list[str]:
    if scan_mode == "source_only":
        return []

    if not log_path.exists():
        return []

    live_lines: list[str] = []
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        content = stripped.removeprefix("[humanonn] ").strip()
        if content.startswith(_REPORT_START):
            break
        if content.startswith(_SOURCE_SCAN_START):
            break
        if stripped.startswith("[humanonn]"):
            live_lines.append(stripped)
    return live_lines


if __name__ == "__main__":
    main()
