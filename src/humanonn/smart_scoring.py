from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from humanonn.llm_clients import ModelRouter
from humanonn.models import AuditReport, AuditSnapshot, SignalFinding
from humanonn.scoring import score_findings
from humanonn.signals import SIGNALS


PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"

ARCHETYPES = {
    "vibe_coded": (
        "A startup landing page with pill buttons, aurora gradients, glass cards, centered headings, "
        "generic CTA copy, repeated feature/pricing/CTA rhythm, and polished-looking but template-heavy defaults."
    ),
    "touched_up_vibe": (
        "A vibe-coded site that has some custom polish added, but still keeps structural defaults like "
        "templated spacing rhythm, generic cards, repeated layouts, and weak interaction design."
    ),
    "human_built": (
        "A human-built site with varied layout rhythm, deliberate typography pairing, strong interaction states, "
        "custom content hierarchy, and fewer template fingerprints."
    ),
}


def run_smart_scoring(
    snapshot: AuditSnapshot,
    base_report: AuditReport,
    router: ModelRouter,
) -> AuditReport:
    artifact_root = Path(snapshot.raw.get("artifact_root", "")) if snapshot.raw.get("artifact_root") else None
    manifest = _load_manifest(snapshot.raw.get("manifest_path"))
    evidence_pack = _build_evidence_pack(snapshot, base_report.findings, manifest)

    ambiguity_result, ambiguity_candidate, ambiguity_attempts = _run_ambiguity(router, evidence_pack)
    vision_result, vision_candidate, vision_attempts = _run_vision(router, snapshot, evidence_pack)
    embedding_result, embedding_candidate, embedding_attempts = _run_embeddings(router, evidence_pack)
    aggregator_result, aggregator_candidate, aggregator_attempts = _run_aggregator(
        router,
        evidence_pack,
        ambiguity_result,
        vision_result,
        embedding_result,
    )

    merged_findings = _merge_findings(base_report.findings, aggregator_result.get("signal_overrides", []), ambiguity_result.get("signal_reviews", []))
    llm_adjustment = float(aggregator_result.get("score_adjustment", 0))
    score = score_findings(merged_findings, score_mode="smart_llm", llm_adjustment=llm_adjustment)

    report = AuditReport(
        url=base_report.url,
        title=base_report.title,
        score=score,
        findings=merged_findings,
        screenshot_path=base_report.screenshot_path,
        agent_notes=list(base_report.agent_notes),
        smart_summary=aggregator_result.get("summary"),
        dynamic_findings=aggregator_result.get("dynamic_findings", []),
        llm_evidence={
            "ambiguity": {
                "candidate": _candidate_payload(ambiguity_candidate),
                "attempts": ambiguity_attempts,
                "result": ambiguity_result,
            },
            "vision": {
                "candidate": _candidate_payload(vision_candidate),
                "attempts": vision_attempts,
                "result": vision_result,
            },
            "embeddings": {
                "candidate": _candidate_payload(embedding_candidate),
                "attempts": embedding_attempts,
                "result": embedding_result,
            },
            "aggregator": {
                "candidate": _candidate_payload(aggregator_candidate),
                "attempts": aggregator_attempts,
                "result": aggregator_result,
            },
        },
    )

    if artifact_root:
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "llm_evidence.json").write_text(
            json.dumps(report.llm_evidence, indent=2),
            encoding="utf-8",
        )
        (artifact_root / "smart_summary.json").write_text(
            json.dumps(
                {
                    "summary": report.smart_summary,
                    "dynamic_findings": report.dynamic_findings,
                    "score": {
                        "vibe_score": report.score.vibe_score,
                        "humanness_score": report.score.humanness_score,
                        "llm_adjustment": report.score.llm_adjustment,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    report.agent_notes.append(
        f"{aggregator_candidate.bug_tag} produced smart scoring with adjustment {report.score.llm_adjustment:+.2f}."
    )
    return report


def _run_ambiguity(router: ModelRouter, evidence_pack: dict[str, Any]) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    prompt = (PROMPT_ROOT / "fast_ambiguity.md").read_text(encoding="utf-8")
    payload = {
        "site": evidence_pack["site"],
        "artifact_summary": evidence_pack["artifact_summary"],
        "requested_signals": evidence_pack["ambiguous_signals"],
    }
    result, candidate, attempts = router.call_json("fast_ambiguity", prompt, payload, temperature=0.05)
    return result, candidate, attempts


def _run_vision(
    router: ModelRouter,
    snapshot: AuditSnapshot,
    evidence_pack: dict[str, Any],
) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    prompt = (PROMPT_ROOT / "vision_summary.md").read_text(encoding="utf-8")
    image_paths = [snapshot.raw.get("main_image_path") or snapshot.screenshot_path]
    if snapshot.raw.get("manifest_path"):
        manifest = _load_manifest(snapshot.raw.get("manifest_path"))
        for section in manifest.get("sections", [])[:2]:
            if section.get("section_image_path"):
                image_paths.append(section["section_image_path"])
    payload = {
        "site": evidence_pack["site"],
        "artifact_summary": evidence_pack["artifact_summary"],
    }
    result, candidate, attempts = router.call_json("vision", prompt, payload, image_paths=[path for path in image_paths if path], temperature=0.1)
    return result, candidate, attempts


def _run_embeddings(
    router: ModelRouter,
    evidence_pack: dict[str, Any],
) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    signature = evidence_pack["site_signature"]
    texts = [signature, *ARCHETYPES.values()]
    vectors, candidate, attempts = router.embed_texts(texts)
    source = vectors[0]
    labels = list(ARCHETYPES.keys())
    similarities = []
    for label, vector in zip(labels, vectors[1:]):
        similarities.append({"label": label, "score": round(_cosine_similarity(source, vector), 4)})
    similarities.sort(key=lambda item: item["score"], reverse=True)
    result = {"site_signature": signature, "similarities": similarities}
    return result, candidate, attempts


def _run_aggregator(
    router: ModelRouter,
    evidence_pack: dict[str, Any],
    ambiguity_result: dict[str, Any],
    vision_result: dict[str, Any],
    embedding_result: dict[str, Any],
) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    prompt = (PROMPT_ROOT / "smart_score.md").read_text(encoding="utf-8")
    payload = {
        "site": evidence_pack["site"],
        "deterministic_findings": evidence_pack["base_findings"],
        "artifact_summary": evidence_pack["artifact_summary"],
        "ambiguity_result": ambiguity_result,
        "vision_result": vision_result,
        "embedding_result": embedding_result,
    }
    result, candidate, attempts = router.call_json("json_classification", prompt, payload, temperature=0.1)
    return result, candidate, attempts


def _build_evidence_pack(
    snapshot: AuditSnapshot,
    findings: list[SignalFinding],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    flagged = [finding for finding in findings if finding.flagged]
    base_findings = [
        {
            "id": finding.id,
            "name": finding.name,
            "tier": finding.tier,
            "flagged": finding.flagged,
            "confidence": finding.confidence,
            "reason": finding.reason,
            "fix": finding.fix,
            "evidence": finding.evidence,
        }
        for finding in findings
    ]
    sections = manifest.get("sections", [])
    artifact_summary = {
        "section_count": len(sections),
        "sections": [],
        "unverified_components": 0,
        "verified_components": 0,
    }
    for section in sections[:8]:
        components = section.get("components", [])
        verified = sum(1 for item in components if item.get("status") == "verified")
        unverified = sum(1 for item in components if item.get("status") == "unverified")
        artifact_summary["verified_components"] += verified
        artifact_summary["unverified_components"] += unverified
        artifact_summary["sections"].append(
            {
                "label": section.get("label"),
                "component_count": len(components),
                "verified_components": verified,
                "unverified_components": unverified,
                "sample_components": [
                    {
                        "kind": item.get("kind"),
                        "label": item.get("label"),
                        "hover_changed": item.get("hover_changed"),
                        "active_changed": item.get("active_changed"),
                        "status": item.get("status"),
                    }
                    for item in components[:8]
                ],
            }
        )
    site = {
        "url": snapshot.url,
        "title": snapshot.title,
        "fonts": snapshot.fonts[:8],
        "body": {
            "backgroundColor": snapshot.body.get("backgroundColor"),
            "backgroundImage": snapshot.body.get("backgroundImage"),
            "heroBackgroundImage": snapshot.body.get("heroBackgroundImage"),
        },
        "counts": {
            "buttons": len(snapshot.buttons),
            "inputs": len(snapshot.inputs),
            "links": len(snapshot.links),
            "headings": len(snapshot.headings),
            "sections": len(snapshot.sections),
            "images": len(snapshot.images),
        },
        "sample_buttons": [item.get("text", "") for item in snapshot.buttons[:8]],
        "sample_headings": [item.get("text", "") for item in snapshot.headings[:8]],
        "flagged_count": len(flagged),
    }
    ambiguous_signals = [
        {
            "id": signal.id,
            "name": signal.name,
            "tier": signal.tier,
            "fix": signal.fix,
        }
        for signal in SIGNALS
        if signal.kind == "ambiguous"
    ]
    site_signature = _site_signature(site, base_findings, artifact_summary)
    return {
        "site": site,
        "base_findings": base_findings,
        "artifact_summary": artifact_summary,
        "ambiguous_signals": ambiguous_signals,
        "site_signature": site_signature,
    }


def _merge_findings(
    base_findings: list[SignalFinding],
    aggregator_overrides: list[dict[str, Any]],
    ambiguity_reviews: list[dict[str, Any]],
) -> list[SignalFinding]:
    merged = {finding.id: finding for finding in base_findings}
    for payload in [*ambiguity_reviews, *aggregator_overrides]:
        signal_id = payload.get("id")
        if not signal_id or signal_id not in merged:
            continue
        current = merged[signal_id]
        merged[signal_id] = SignalFinding(
            id=current.id,
            name=current.name,
            tier=current.tier,
            weight=current.weight,
            flagged=bool(payload.get("flagged", current.flagged)),
            confidence=float(payload.get("confidence", current.confidence if current.flagged else 0.0)),
            reason=str(payload.get("reason", current.reason)),
            fix=str(payload.get("fix", current.fix)),
            evidence={**current.evidence, "llm_override": True},
        )
    return list(merged.values())


def _load_manifest(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _site_signature(site: dict[str, Any], base_findings: list[dict[str, Any]], artifact_summary: dict[str, Any]) -> str:
    flagged = [item["name"] for item in base_findings if item["flagged"]][:12]
    section_labels = [item["label"] for item in artifact_summary.get("sections", [])[:6] if item.get("label")]
    return (
        f"Title: {site['title']}. "
        f"Fonts: {', '.join(site['fonts'])}. "
        f"Buttons: {site['counts']['buttons']}, Links: {site['counts']['links']}, Sections: {site['counts']['sections']}. "
        f"Sample buttons: {', '.join(site['sample_buttons'])}. "
        f"Sample headings: {', '.join(site['sample_headings'])}. "
        f"Section labels: {', '.join(section_labels)}. "
        f"Deterministic flags: {', '.join(flagged)}. "
        f"Verified components: {artifact_summary.get('verified_components', 0)}. "
        f"Unverified components: {artifact_summary.get('unverified_components', 0)}."
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    return {
        "provider": candidate.provider,
        "model": candidate.model,
        "bug_tag": candidate.bug_tag,
    }
