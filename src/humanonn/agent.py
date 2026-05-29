from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from groq import Groq

from humanonn.config import Settings
from humanonn.model_routing import ModelCandidate, route_for
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

    def scan(self, url: str) -> AuditReport:
        started_at = time.perf_counter()
        terminal_log(f"Site scan timer started for {url}", self.settings.terminal_logs)
        if self.settings.llm_enabled:
            try:
                report = self._scan_with_fallback_orchestrator(url)
            except Exception as exc:
                report = self._scan_deterministic(url)
                report.agent_notes.append(f"Main orchestrator fallback chain failed; used deterministic fallback: {exc}")
        else:
            report = self._scan_deterministic(url)
            if self.settings.force_no_llm:
                report.agent_notes.append("LLM path disabled by --no-llm/HUMANONN_NO_LLM; used deterministic scanner.")
            else:
                report.agent_notes.append("Main orchestrator API key is not configured; used deterministic scanner.")
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        elapsed_seconds = elapsed_ms / 1000
        report.agent_notes.append(f"Site scan completed in {elapsed_seconds:.2f}s ({elapsed_ms} ms).")
        terminal_log(f"Site scan completed in {elapsed_seconds:.2f}s ({elapsed_ms} ms) for {url}", self.settings.terminal_logs)
        return report

    def _scan_deterministic(self, url: str) -> AuditReport:
        self.registry = ToolRegistry(self.settings)
        self.registry.execute("crawl_page", {"url": url})
        return self.registry.generate_report()

    def _scan_with_fallback_orchestrator(self, url: str) -> AuditReport:
        failures: list[str] = []
        configured = self.settings.primary_candidate("main_orchestrator")
        candidate_chain = self._main_orchestrator_candidates(configured)
        for candidate in candidate_chain:
            if not self.settings.api_keys_for(candidate.provider):
                failures.append(f"{candidate.bug_tag}: missing_api_key")
                continue
            try:
                self.registry = ToolRegistry(self.settings)
                if candidate.provider == "groq":
                    report = self._run_groq_orchestrator(url, candidate)
                elif candidate.provider == "gemini":
                    report = self._run_gemini_orchestrator(url, candidate)
                else:
                    failures.append(f"{candidate.bug_tag}: provider_not_supported")
                    continue
                if failures:
                    report.agent_notes.append(f"{candidate.bug_tag} succeeded after orchestrator fallback: {' | '.join(failures)}")
                return report
            except Exception as exc:
                failures.append(f"{candidate.bug_tag}: {str(exc).splitlines()[0]}")
        raise RuntimeError("; ".join(failures) or "no main orchestrator candidates available")

    def _run_groq_orchestrator(self, url: str, candidate: ModelCandidate) -> AuditReport:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": f"Audit this deployed website: {url}"},
        ]
        last_error: Exception | None = None

        for api_key in self.settings.api_keys_for("groq"):
            try:
                client = Groq(api_key=api_key)
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
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise RuntimeError("Missing Groq API key.")
        raise last_error

    def _run_gemini_orchestrator(self, url: str, candidate: ModelCandidate) -> AuditReport:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        contents: list[dict[str, Any]] = [
            {
                "role": "user",
                "parts": [{"text": f"Audit this deployed website: {url}"}],
            }
        ]

        for _ in range(8):
            response = self._call_gemini_orchestrator(candidate, system_prompt, contents)
            parts = response["candidates"][0]["content"].get("parts", [])
            function_calls = [part["functionCall"] for part in parts if "functionCall" in part]
            text_parts = [part.get("text", "") for part in parts if part.get("text")]

            if not function_calls:
                if self.registry.snapshot is not None:
                    report = self._finalize_smart_report()
                    note = f"{candidate.bug_tag} completed without another tool call."
                    if text_parts:
                        note = f"{note} {' '.join(text_parts).strip()}"
                    report.agent_notes.append(note.strip())
                    return report
                if self.registry.report is not None:
                    self.registry.report.agent_notes.append(f"{candidate.bug_tag} completed without another tool call.")
                    return self.registry.report
                return self.registry.generate_report()

            model_parts: list[dict[str, Any]] = []
            response_parts: list[dict[str, Any]] = []
            for function_call in function_calls:
                tool_name = function_call["name"]
                args = function_call.get("args", {})
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                result = self.registry.execute(tool_name, args)
                model_parts.append({"functionCall": {"name": tool_name, "args": args}})
                response_parts.append(
                    {
                        "functionResponse": {
                            "name": tool_name,
                            "response": {"result": result},
                        }
                    }
                )
                if tool_name == "generate_report":
                    return self._finalize_smart_report()

            contents.append({"role": "model", "parts": model_parts})
            contents.append({"role": "user", "parts": response_parts})

        report = self._finalize_smart_report()
        report.agent_notes.append(f"{candidate.bug_tag} reached max agent iterations; finalized current findings.")
        return report

    def _call_gemini_orchestrator(
        self,
        candidate: ModelCandidate,
        system_prompt: str,
        contents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        tools = [
            {
                "functionDeclarations": [
                    {
                        "name": tool["function"]["name"],
                        "description": tool["function"].get("description", ""),
                        "parameters": tool["function"].get("parameters", {"type": "object", "properties": {}}),
                    }
                    for tool in TOOL_DEFINITIONS
                ]
            }
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "tools": tools,
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"temperature": 0.1},
        }
        for api_key in self.settings.api_keys_for("gemini"):
            try:
                request = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{candidate.model}:generateContent",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.settings.navigation_timeout_ms / 1000) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise RuntimeError("Missing Gemini API key.")
        raise last_error

    def _main_orchestrator_candidates(self, configured: ModelCandidate) -> list[ModelCandidate]:
        fallback_chain = route_for("main_orchestrator")
        ordered: list[ModelCandidate] = [configured]
        for candidate in fallback_chain:
            if candidate.provider == configured.provider and candidate.model == configured.model:
                continue
            ordered.append(candidate)
        return ordered

    def _finalize_smart_report(self) -> AuditReport:
        if self.registry.snapshot is None:
            raise RuntimeError("LLM scoring requires a completed crawl snapshot.")
        self.registry.snapshot.raw["llm_adjustment_gate_enabled"] = self.settings.llm_adjustment_gate_enabled
        self.registry.snapshot.raw["llm_adjustment_multiplier_enabled"] = self.settings.llm_adjustment_multiplier_enabled
        self.registry.snapshot.raw["llm_adjustment_evidence_floor"] = self.settings.llm_adjustment_evidence_floor
        self.registry.snapshot.raw["llm_adjustment_single_source_cap"] = self.settings.llm_adjustment_single_source_cap
        self.registry.snapshot.raw["llm_adjustment_headroom_enabled"] = self.settings.llm_adjustment_headroom_enabled
        self.registry.snapshot.raw["smart_summary_enabled"] = self.settings.smart_summary_enabled
        self.registry.snapshot.raw["dynamic_findings_enabled"] = self.settings.dynamic_findings_enabled
        terminal_log("Building deterministic baseline before smart scoring", self.settings.terminal_logs)
        base_report = self.registry.generate_report()
        force_vision_override = bool(self.registry.snapshot.raw.get("needs_vision_override")) or any(
            finding.id == "dynamic_injected_styles" and finding.flagged for finding in base_report.findings
        )
        if force_vision_override:
            self.registry.snapshot.raw["needs_vision_override"] = True
            terminal_log("Vision override enabled from crawl signals; forcing smart scoring vision pass", self.settings.terminal_logs)
        terminal_log("Running smart LLM scoring pipeline", self.settings.terminal_logs)
        return run_smart_scoring(self.registry.snapshot, base_report, self.router)
