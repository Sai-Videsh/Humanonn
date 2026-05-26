from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from humanonn.agent import HumanonnAgent
from humanonn.config import load_settings
from humanonn.reports import format_console_report, report_to_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="humanonn", description="Audit a deployed website for vibe-coded design patterns.")
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="Scan a deployed URL.")
    scan.add_argument("url", help="Deployed URL to audit.")
    scan.add_argument("--json", dest="json_path", help="Write the full report JSON to this path.")
    scan.add_argument("--all", action="store_true", help="Show clear and flagged findings in console output.")
    scan.add_argument("--no-llm", action="store_true", help="Skip Groq orchestration and use deterministic rules only.")
    scan.add_argument("--quiet", action="store_true", help="Disable scan progress logs in the terminal.")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command != "scan":
        parser.print_help()
        return

    settings = load_settings()
    if args.quiet:
        settings = replace(settings, terminal_logs=False)
    agent = HumanonnAgent(settings)
    try:
        report = agent.scan(args.url, use_llm=not args.no_llm)
    except RuntimeError as exc:
        parser.exit(1, f"Humanonn scan failed: {exc}\n")

    print(format_console_report(report, show_all=args.all))
    if args.json_path:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report_to_dict(report), indent=2), encoding="utf-8")
        print(f"\nJSON report written to {path}")


if __name__ == "__main__":
    main()
