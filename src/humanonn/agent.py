from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from groq import Groq

from humanonn.config import Settings
from humanonn.models import AuditReport
from humanonn.llm_clients import ModelRouter
from humanonn.runtime import terminal_log
from humanonn.smart_scoring import run_smart_scoring
from humanonn.tools.registry import ToolRegistry
from humanonn.tools.schemas import TOOL_DEFINITIONS


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "agent_system.md"


class HumanonnAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = ToolRegistry(settings)
        self.router = ModelRouter(settings)

    def scan(self, url: str, use_llm: bool = True) -> AuditReport:
        if use_llm and self.settings.llm_enabled:
            try:
                return self._scan_with_primary_orchestrator(url)
            except Exception as exc:
                report = self._scan_deterministic(url)
                candidate = self.settings.primary_candidate("main_orchestrator")
                report.agent_notes.append(f"{candidate.bug_tag} failed; used deterministic fallback: {exc}")
                return report
        report = self._scan_deterministic(url)
        if use_llm and not self.settings.llm_enabled:
            report.agent_notes.append("Main orchestrator API key is not configured; used deterministic scanner.")
        return report

    def _scan_deterministic(self, url: str) -> AuditReport:
        self.registry.execute("crawl_page", {"url": url})
        return self.registry.generate_report()

    def _scan_with_primary_orchestrator(self, url: str) -> AuditReport:
        candidate = self.settings.primary_candidate("main_orchestrator")
        if candidate.provider != "groq":
            raise RuntimeError(f"{candidate.provider} orchestrator client is not implemented yet.")
        client = Groq(api_key=self.settings.api_key_for("groq"))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": f"Audit this deployed website: {url}"},
        ]

        for _ in range(8):
            response = client.chat.completions.create(
                model=candidate.model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.1,
            )
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                if self.registry.snapshot is not None:
                    report = self._finalize_smart_report()
                    report.agent_notes.append(f"{candidate.bug_tag} completed without another tool call.")
                    return report
                if self.registry.report is not None:
                    self.registry.report.agent_notes.append(f"{candidate.bug_tag} completed without another tool call.")
                    return self.registry.report
                return self.registry.generate_report()

            messages.append(message)
            for tool_call in tool_calls:
                args = json.loads(tool_call.function.arguments or "{}")
                result = self.registry.execute(tool_call.function.name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, default=str),
                    }
                )
                if tool_call.function.name == "generate_report":
                    return self._finalize_smart_report()

        report = self._finalize_smart_report()
        report.agent_notes.append(f"{candidate.bug_tag} reached max agent iterations; finalized current findings.")
        return report

    def _finalize_smart_report(self) -> AuditReport:
        if self.registry.snapshot is None:
            raise RuntimeError("LLM scoring requires a completed crawl snapshot.")
        terminal_log("Building deterministic baseline before smart scoring", self.settings.terminal_logs)
        base_report = self.registry.generate_report()
        terminal_log("Running smart LLM scoring pipeline", self.settings.terminal_logs)
        return run_smart_scoring(self.registry.snapshot, base_report, self.router)
