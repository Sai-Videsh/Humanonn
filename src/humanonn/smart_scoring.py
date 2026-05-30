from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from humanonn.llm_clients import ModelRouter
from humanonn.models import AuditReport, AuditSnapshot, SignalFinding
from humanonn.scoring import score_findings
from humanonn.signals import SIGNAL_BY_ID


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

VISION_OVERRIDE_SIGNAL_IDS = {
    "all_centered_headings",
    "bento_grid",
    "canvas_rendered_ui",
    "canvas_webgl_hero_background",
    "dynamic_injected_styles",
    "glassmorphism",
    "gradient_text",
    "mesh_gradient",
    "pill_buttons",
    "purple_accent",
    "stock_image_pattern",
}

VISION_PATTERN_ALIASES: dict[str, str] = {
    "all headings visually centered": "all_centered_headings",
    "bento grid": "bento_grid",
    "bento grid layout": "bento_grid",
    "floating badge": "beta_badge",
    "floating badge above h1": "beta_badge",
    "glassmorphism": "glassmorphism",
    "glassmorphism cards": "glassmorphism",
    "frosted glass": "glassmorphism",
    "purple accent": "purple_accent",
    "violet accent": "purple_accent",
    "mesh gradient": "mesh_gradient",
    "aurora blob": "mesh_gradient",
    "pill buttons": "pill_buttons",
}

VISION_SIGNAL_ID_ALIASES: dict[str, str] = {
    "all headings visually centered": "all_centered_headings",
    "floating_badge": "beta_badge",
    "floating_badge_above_h1": "beta_badge",
}

SMART_SCORING_GATE_MIN = 25
SMART_SCORING_GATE_MAX = 65
AMBIGUITY_REVIEW_CONFIDENCE_MIN = 0.3
AMBIGUITY_REVIEW_CONFIDENCE_MAX = 0.8
HYBRID_SIGNAL_MIN_CONFIDENCE = 0.55
HYBRID_TRIGGER_SIGNAL_IDS = {
    "pill_buttons",
    "mesh_gradient",
    "glassmorphism",
    "gradient_text",
    "bento_grid",
    "all_centered_headings",
    "generic_ctas",
    "dynamic_injected_styles",
    "canvas_webgl_hero_background",
    "canvas_rendered_ui",
    "stock_image_pattern",
    "inter_only",
}


def run_smart_scoring(
    snapshot: AuditSnapshot,
    base_report: AuditReport,
    router: ModelRouter,
) -> AuditReport:
    smart_scoring_gate_enabled = bool(snapshot.raw.get("smart_scoring_gate_enabled", False))
    vision_override = bool(snapshot.raw.get("needs_vision_override"))
    manifest = _load_manifest(snapshot.raw.get("manifest_path"))
    evidence_pack = _build_evidence_pack(snapshot, base_report.findings, manifest)
    hybrid_signal_trigger, hybrid_trigger_signal_ids = _has_hybrid_signal_trigger(base_report.findings, evidence_pack)
    within_smart_gate = smart_scoring_gate_enabled and SMART_SCORING_GATE_MIN <= base_report.score.vibe_score <= SMART_SCORING_GATE_MAX
    should_run_smart_scoring = vision_override or within_smart_gate or hybrid_signal_trigger
    smart_notes = list(base_report.agent_notes)
    smart_summary_enabled = bool(snapshot.raw.get("smart_summary_enabled", True))
    dynamic_findings_enabled = bool(snapshot.raw.get("dynamic_findings_enabled", True))

    if smart_scoring_gate_enabled and hybrid_signal_trigger and not within_smart_gate and not vision_override:
        smart_notes.append(
            "Forced smart scoring from hybrid trigger signals outside score gate: "
            f"{', '.join(hybrid_trigger_signal_ids[:8])}."
        )

    if not should_run_smart_scoring:
        report = replace(base_report)
        report.agent_notes = [
            *smart_notes,
            (
                "Skipped smart scoring because deterministic score was outside the "
                f"{SMART_SCORING_GATE_MIN}-{SMART_SCORING_GATE_MAX} gate, no vision override was requested, "
                "and no hybrid signal triggers were found."
            ),
        ]
        return report

    artifact_root = Path(snapshot.raw.get("artifact_root", "")) if snapshot.raw.get("artifact_root") else None

    if smart_scoring_gate_enabled and not evidence_pack["ambiguous_signals"] and not vision_override and not hybrid_signal_trigger:
        report = replace(base_report)
        report.agent_notes = [
            *smart_notes,
            (
                "Skipped smart scoring because no ambiguous signals fell in the "
                f"{AMBIGUITY_REVIEW_CONFIDENCE_MIN:.1f}-{AMBIGUITY_REVIEW_CONFIDENCE_MAX:.1f} confidence review band."
            ),
        ]
        return report

    ambiguity_result, ambiguity_candidate, ambiguity_attempts = _run_ambiguity(router, evidence_pack)
    try:
        vision_result, vision_candidate, vision_attempts = _run_vision(router, snapshot, evidence_pack)
    except Exception as exc:
        vision_result = {"signal_confirmations": [], "additional_patterns": [], "score_hint": {"direction": "neutral", "magnitude": 0}}
        vision_candidate = None
        vision_attempts = [{"bug_tag": "VISION_FALLBACK_SKIPPED", "status": "failed", "reason": str(exc).splitlines()[0]}]
        smart_notes.append(f"Skipped vision scoring after provider failures: {str(exc).splitlines()[0]}")
    vision_overrides = _vision_signal_overrides(vision_result)
    embedding_result, embedding_candidate, embedding_attempts = _run_embeddings(router, evidence_pack)
    aggregator_result, aggregator_candidate, aggregator_attempts = _run_aggregator(
        router,
        evidence_pack,
        ambiguity_result,
        vision_result,
        embedding_result,
        include_summary=smart_summary_enabled,
        include_dynamic_findings=dynamic_findings_enabled,
    )

    # Surface archetype label from embedding similarities (if available)
    archetype_label = None
    try:
        sims = embedding_result.get("similarities", []) if isinstance(embedding_result, dict) else []
        if sims:
            archetype_label = sims[0].get("label")
    except Exception:
        archetype_label = None

    merged_findings = _merge_findings(
        base_report.findings,
        [*aggregator_result.get("signal_overrides", []), *ambiguity_result.get("signal_reviews", []), *vision_overrides],
    )
    raw_llm_adjustment = float(aggregator_result.get("score_adjustment", 0))
    llm_adjustment = raw_llm_adjustment
    llm_adjustment_gate_enabled = bool(snapshot.raw.get("llm_adjustment_gate_enabled", False))
    llm_adjustment_multiplier_enabled = bool(snapshot.raw.get("llm_adjustment_multiplier_enabled", True))
    llm_adjustment_evidence_floor = float(snapshot.raw.get("llm_adjustment_evidence_floor", 0.35))
    llm_adjustment_single_source_cap = float(snapshot.raw.get("llm_adjustment_single_source_cap", 5.0))
    llm_adjustment_headroom_enabled = bool(snapshot.raw.get("llm_adjustment_headroom_enabled", True))

    # Re-validate post-merge: compute deterministic score without LLM adjustment
    # If the merged deterministic score is now confidently outside the borderline
    # window, zero out the LLM adjustment because overrides already resolved ambiguity.
    zeroed_llm_note = None
    post_merge_deterministic = score_findings(merged_findings, score_mode="smart_llm", llm_adjustment=0.0)
    if llm_adjustment_multiplier_enabled and llm_adjustment != 0:
        llm_adjustment, scaling_note = _scale_llm_adjustment(
            raw_llm_adjustment=raw_llm_adjustment,
            post_merge_vibe_score=post_merge_deterministic.vibe_score,
            ambiguity_result=ambiguity_result,
            vision_result=vision_result,
            embedding_result=embedding_result,
            aggregator_result=aggregator_result,
            archetype_label=archetype_label,
            evidence_floor=llm_adjustment_evidence_floor,
            single_source_cap=llm_adjustment_single_source_cap,
            headroom_enabled=llm_adjustment_headroom_enabled,
        )
        smart_notes.append(scaling_note)

    strong_positive_llm_evidence = (
        llm_adjustment > 0
        and archetype_label == "vibe_coded"
        and aggregator_result.get("archetype_label") == "vibe_coded"
        and (vision_result.get("score_hint", {}) or {}).get("direction") == "up"
    )
    if llm_adjustment_gate_enabled:
        if smart_scoring_gate_enabled and not SMART_SCORING_GATE_MIN <= post_merge_deterministic.vibe_score <= SMART_SCORING_GATE_MAX and not strong_positive_llm_evidence:
            zeroed_llm_note = (
                (
                    "Zeroed LLM adjustment because post-merge deterministic score "
                    f"{post_merge_deterministic.vibe_score} is outside the "
                    f"{SMART_SCORING_GATE_MIN}-{SMART_SCORING_GATE_MAX} smart-scoring window."
                )
            )
            llm_adjustment = 0.0
    elif llm_adjustment != 0:
        report_note = f"Applied LLM adjustment {llm_adjustment:+.2f} with HUMANONN_LLM_ADJUSTMENT_GATE=false."
        smart_notes.append(report_note)

    score = score_findings(merged_findings, score_mode="smart_llm", llm_adjustment=llm_adjustment)

    if not smart_summary_enabled:
        smart_notes.append("Skipped smart summary generation (HUMANONN_SMART_SUMMARY=false).")
    if not dynamic_findings_enabled:
        smart_notes.append("Skipped dynamic findings generation (HUMANONN_DYNAMIC_FINDINGS=false).")

    report = AuditReport(
        url=base_report.url,
        title=base_report.title,
        score=score,
        findings=merged_findings,
        screenshot_path=base_report.screenshot_path,
        scan_metadata=base_report.scan_metadata,
        agent_notes=smart_notes,
        smart_summary=aggregator_result.get("summary") if smart_summary_enabled else None,
        archetype_label=archetype_label,
        dynamic_findings=aggregator_result.get("dynamic_findings", []) if dynamic_findings_enabled else [],
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
                "signal_overrides": vision_overrides,
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
                    "archetype_label": report.archetype_label,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if zeroed_llm_note:
            report.agent_notes.append(zeroed_llm_note)

        report.agent_notes.append(
            f"{aggregator_candidate.bug_tag} produced smart scoring with adjustment {report.score.llm_adjustment:+.2f}."
        )
    return report


def _run_ambiguity(router: ModelRouter, evidence_pack: dict[str, Any]) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    prompt = (PROMPT_ROOT / "ats_review.md").read_text(encoding="utf-8")
    all_ambiguous = evidence_pack.get("ambiguous_signals", [])
    selected, selection_meta = _select_ambiguous_for_ats(all_ambiguous, router.settings)
    payload = {
        "scan_domain": "live",
        "site": evidence_pack["site"],
        "artifact_summary": evidence_pack["artifact_summary"],
        "requested_signals": selected,
        "selection_meta": selection_meta,
    }
    result, candidate, attempts = router.call_json("ats_review", prompt, payload, temperature=0.05)
    return result, candidate, attempts


def _select_ambiguous_for_ats(signals: list[dict[str, Any]], settings) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select a subset of ambiguous signals to send to ATS using soft confidence-band sampling.

    Returns (selected_signals, meta) where meta contains counts and reasons.
    """
    if not signals:
        return [], {"selected": 0, "available": 0, "reasons": {}}

    conf_low = float(getattr(settings, "smart_conf_low", 0.35))
    conf_high = float(getattr(settings, "smart_conf_high", 0.65))
    center_low = float(getattr(settings, "smart_center_low", 0.45))
    center_high = float(getattr(settings, "smart_center_high", 0.55))
    top_k = int(getattr(settings, "smart_top_k", 10))
    max_per_repo = int(getattr(settings, "smart_max_per_repo", 20))
    sample_outside = float(getattr(settings, "smart_sample_outside_pct", 0.05))

    selected: list[dict[str, Any]] = []
    reasons: dict[str, int] = {"center": 0, "within_band_sampled": 0, "outside_sampled": 0, "skipped": 0}

    import random

    candidates: list[tuple[float, dict[str, Any]]] = []
    for s in signals:
        conf = float(s.get("confidence", 0.0))
        # priority buckets
        if center_low <= conf <= center_high:
            priority = 2
        elif conf_low <= conf <= conf_high:
            priority = 1
        else:
            priority = 0

        # sampling
        include = False
        if priority == 2:
            include = True
            reasons["center"] += 1
        elif priority == 1:
            # soft sampling for near-center items
            if random.random() < 0.5:
                include = True
                reasons["within_band_sampled"] += 1
            else:
                reasons["skipped"] += 1
        else:
            if random.random() < sample_outside:
                include = True
                reasons["outside_sampled"] += 1
            else:
                reasons["skipped"] += 1

        if include:
            # score for ranking (higher near center and with higher rule weight)
            proximity = 1.0 - min(1.0, abs(conf - 0.5) * 2.0)
            weight = float(s.get("weight", 1.0))
            score = proximity * weight
            candidates.append((score, s))

    # sort and apply top-k and per-repo caps
    candidates.sort(key=lambda t: t[0], reverse=True)
    trimmed = [item for _, item in candidates][: min(top_k, max_per_repo)]

    meta = {"selected": len(trimmed), "available": len(signals), "reasons": reasons}
    return trimmed, meta


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
    labels = list(ARCHETYPES.keys())
    embedding_payload, candidate, attempts = router.embed_texts(texts, labels=labels)
    if embedding_payload.get("mode") == "vectors":
        vectors = embedding_payload["vectors"]
        source = vectors[0]
        similarities = []
        for label, vector in zip(labels, vectors[1:]):
            similarities.append({"label": label, "score": round(_cosine_similarity(source, vector), 4)})
        similarities.sort(key=lambda item: item["score"], reverse=True)
        result = {"site_signature": signature, "mode": "vectors", "similarities": similarities}
    else:
        result = {
            "site_signature": signature,
            "mode": embedding_payload.get("mode", "similarities"),
            "similarities": embedding_payload.get("similarities", []),
        }
    return result, candidate, attempts


def _run_aggregator(
    router: ModelRouter,
    evidence_pack: dict[str, Any],
    ambiguity_result: dict[str, Any],
    vision_result: dict[str, Any],
    embedding_result: dict[str, Any],
    include_summary: bool,
    include_dynamic_findings: bool,
) -> tuple[dict[str, Any], Any, list[dict[str, str]]]:
    prompt = _build_aggregator_prompt(
        include_summary=include_summary,
        include_dynamic_findings=include_dynamic_findings,
    )
    payload = {
        "site": evidence_pack["site"],
        "deterministic_findings": evidence_pack["scoring_findings"],
        "uncertain_findings": evidence_pack["ambiguous_signals"],
        "artifact_summary": evidence_pack["artifact_summary"],
        "ambiguity_result": ambiguity_result,
        "vision_result": vision_result,
        "embedding_result": embedding_result,
    }
    result, candidate, attempts = router.call_json("json_classification", prompt, payload, temperature=0.1)
    return result, candidate, attempts


def _build_aggregator_prompt(include_summary: bool, include_dynamic_findings: bool) -> str:
    if not include_summary and not include_dynamic_findings:
        return (PROMPT_ROOT / "smart_score_compact.md").read_text(encoding="utf-8")

    prompt = (PROMPT_ROOT / "smart_score.md").read_text(encoding="utf-8")
    constraints: list[str] = []
    if not include_summary:
        constraints.append(
            "Summary output is disabled. Return \"summary\": null and do not generate summary prose."
        )
    if not include_dynamic_findings:
        constraints.append(
            "Dynamic findings output is disabled. Return \"dynamic_findings\": [] and do not generate dynamic finding entries."
        )
    if not constraints:
        return prompt
    return prompt + "\n\nAdditional output constraints:\n- " + "\n- ".join(constraints)


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
            "bucket": finding.bucket,
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
        verified = sum(1 for item in components if item.get("status") in {"verified", "style_verified"})
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
            "id": finding.id,
            "name": finding.name,
            "tier": finding.tier,
            "bucket": finding.bucket,
            "weight": SIGNAL_BY_ID[finding.id].weight,
            "confidence": finding.confidence,
            "flagged": finding.flagged,
            "reason": finding.reason,
            "fix": finding.fix,
        }
        for finding in findings
        if (
            SIGNAL_BY_ID[finding.id].kind == "ambiguous"
            and AMBIGUITY_REVIEW_CONFIDENCE_MIN <= finding.confidence <= AMBIGUITY_REVIEW_CONFIDENCE_MAX
        )
    ]
    scoring_findings = [
        {
            "id": finding["id"],
            "name": finding["name"],
            "tier": finding["tier"],
            "bucket": finding["bucket"],
            "weight": SIGNAL_BY_ID[finding["id"]].weight,
            "confidence": finding["confidence"],
            "flagged": finding["flagged"],
            "reason": finding["reason"],
            "fix": finding["fix"],
            "evidence": finding["evidence"],
        }
        for finding in base_findings
        if finding["flagged"]
        or finding["id"] in VISION_OVERRIDE_SIGNAL_IDS
        or (
            SIGNAL_BY_ID[finding["id"]].kind == "ambiguous"
            and AMBIGUITY_REVIEW_CONFIDENCE_MIN <= finding["confidence"] <= AMBIGUITY_REVIEW_CONFIDENCE_MAX
        )
    ]
    site_signature = _site_signature(site, base_findings, artifact_summary)
    return {
        "site": site,
        "base_findings": base_findings,
        "scoring_findings": scoring_findings,
        "artifact_summary": artifact_summary,
        "ambiguous_signals": ambiguous_signals,
        "site_signature": site_signature,
    }


def _has_hybrid_signal_trigger(
    findings: list[SignalFinding],
    evidence_pack: dict[str, Any],
) -> tuple[bool, list[str]]:
    trigger_ids: list[str] = []

    ambiguous_signals = evidence_pack.get("ambiguous_signals", []) if isinstance(evidence_pack, dict) else []
    if len(ambiguous_signals) >= 3:
        trigger_ids.append("ambiguous_cluster")

    for finding in findings:
        if not finding.flagged:
            continue
        if finding.id not in HYBRID_TRIGGER_SIGNAL_IDS:
            continue
        if float(finding.confidence) < HYBRID_SIGNAL_MIN_CONFIDENCE:
            continue
        trigger_ids.append(finding.id)

    deduped = sorted(set(trigger_ids))
    return bool(deduped), deduped


def _merge_findings(
    base_findings: list[SignalFinding],
    override_payloads: list[dict[str, Any]],
) -> list[SignalFinding]:
    merged = {finding.id: finding for finding in base_findings}
    for payload in override_payloads:
        signal_id = payload.get("id")
        if not signal_id or signal_id not in merged:
            continue
        current = merged[signal_id]
        merged[signal_id] = SignalFinding(
            id=current.id,
            name=current.name,
            tier=current.tier,
            bucket=current.bucket,
            weight=current.weight,
            flagged=bool(payload.get("flagged", current.flagged)),
            confidence=float(payload.get("confidence", current.confidence if current.flagged else 0.0)),
            reason=str(payload.get("reason", current.reason)),
            fix=str(payload.get("fix", current.fix)),
            evidence={**current.evidence, "override_source": payload.get("source", "llm")},
        )
    return list(merged.values())


def _vision_signal_overrides(vision_result: dict[str, Any]) -> list[dict[str, Any]]:
    confirmations = vision_result.get("signal_confirmations", []) if isinstance(vision_result, dict) else []
    additional_patterns = vision_result.get("additional_patterns", []) if isinstance(vision_result, dict) else []
    overrides: list[dict[str, Any]] = []
    for item in confirmations:
        signal_id = str(item.get("signal_id", "")).strip()
        signal_id = VISION_SIGNAL_ID_ALIASES.get(signal_id, signal_id)
        if signal_id not in VISION_OVERRIDE_SIGNAL_IDS and signal_id not in SIGNAL_BY_ID:
            continue
        verdict = str(item.get("verdict", "")).lower()
        if verdict not in {"confirmed", "denied", "uncertain"}:
            continue
        signal = SIGNAL_BY_ID.get(signal_id)
        if not signal:
            continue
        flagged = verdict == "confirmed"
        if verdict == "uncertain":
            confidence = 0.5
        else:
            confidence = 1.0 if flagged else 0.0
        overrides.append(
            {
                "id": signal_id,
                "bucket": signal.bucket,
                "flagged": flagged,
                "confidence": confidence,
                "source": "vision",
                "reason": item.get("visual_evidence") or item.get("reason") or "Vision confirmation override.",
                "fix": signal.fix,
            }
        )
    for item in additional_patterns:
        label = str(item.get("label", "")).strip().lower()
        signal_id = str(item.get("signal_id", "")).strip()
        if not signal_id:
            signal_id = VISION_PATTERN_ALIASES.get(label, "")
        if signal_id == "gradient_text":
            # Keep hero-headline gradient text tied to explicit confirmation only.
            continue
        if signal_id not in SIGNAL_BY_ID:
            continue
        signal = SIGNAL_BY_ID[signal_id]
        severity = str(item.get("severity", "medium")).lower()
        confidence = 0.85 if severity == "high" else 0.65 if severity == "medium" else 0.5
        overrides.append(
            {
                "id": signal_id,
                "bucket": signal.bucket,
                "flagged": True,
                "confidence": confidence,
                "source": "vision_additional_pattern",
                "reason": item.get("reason") or f"Vision described '{item.get('label', signal.name)}' as an additional pattern.",
                "fix": signal.fix,
            }
        )
    return overrides


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


def _scale_llm_adjustment(
    raw_llm_adjustment: float,
    post_merge_vibe_score: int,
    ambiguity_result: dict[str, Any],
    vision_result: dict[str, Any],
    embedding_result: dict[str, Any],
    aggregator_result: dict[str, Any],
    archetype_label: str | None,
    evidence_floor: float,
    single_source_cap: float,
    headroom_enabled: bool,
) -> tuple[float, str]:
    source_count = _count_adjustment_sources(
        raw_llm_adjustment=raw_llm_adjustment,
        ambiguity_result=ambiguity_result,
        vision_result=vision_result,
        embedding_result=embedding_result,
        aggregator_result=aggregator_result,
        archetype_label=archetype_label,
    )
    capped = _clip(raw_llm_adjustment, -15.0, 15.0)
    if source_count <= 1:
        cap = max(0.0, single_source_cap)
        capped = _clip(capped, -cap, cap)

    evidence_multiplier = 1.0 if source_count >= 3 else 0.8 if source_count == 2 else 0.6 if source_count == 1 else 0.35
    effective_evidence_floor = _clip(evidence_floor, 0.0, 1.0)
    evidence_multiplier = _clip(max(effective_evidence_floor, evidence_multiplier), 0.0, 1.0)

    scaled = capped * evidence_multiplier
    headroom_multiplier = 1.0
    if headroom_enabled and scaled != 0:
        if scaled > 0:
            headroom_multiplier = _clip((100.0 - float(post_merge_vibe_score)) / 100.0, 0.1, 1.0)
        else:
            headroom_multiplier = _clip(float(post_merge_vibe_score) / 100.0, 0.1, 1.0)
        scaled *= headroom_multiplier

    final_adjustment = _clip(scaled, -15.0, 15.0)
    note = (
        "Scaled LLM adjustment "
        f"{raw_llm_adjustment:+.2f} -> {final_adjustment:+.2f} "
        f"(sources={source_count}, evidence_multiplier={evidence_multiplier:.2f}, headroom_multiplier={headroom_multiplier:.2f})."
    )
    return final_adjustment, note


def _count_adjustment_sources(
    raw_llm_adjustment: float,
    ambiguity_result: dict[str, Any],
    vision_result: dict[str, Any],
    embedding_result: dict[str, Any],
    aggregator_result: dict[str, Any],
    archetype_label: str | None,
) -> int:
    sources = 0

    ambiguity_reviews = ambiguity_result.get("signal_reviews", []) if isinstance(ambiguity_result, dict) else []
    if ambiguity_reviews:
        sources += 1

    signal_confirmations = vision_result.get("signal_confirmations", []) if isinstance(vision_result, dict) else []
    additional_patterns = vision_result.get("additional_patterns", []) if isinstance(vision_result, dict) else []
    if signal_confirmations or additional_patterns:
        sources += 1

    embedding_top_label = None
    if isinstance(embedding_result, dict):
        similarities = embedding_result.get("similarities", [])
        if similarities:
            embedding_top_label = similarities[0].get("label")
    if archetype_label and archetype_label == aggregator_result.get("archetype_label") and archetype_label == embedding_top_label:
        sources += 1

    score_hint = (vision_result.get("score_hint", {}) or {}) if isinstance(vision_result, dict) else {}
    direction = str(score_hint.get("direction", "")).lower()
    if (raw_llm_adjustment > 0 and direction == "up") or (raw_llm_adjustment < 0 and direction == "down"):
        sources += 1

    return sources


def _clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    if candidate is None:
        return {"provider": None, "model": None, "bug_tag": "none"}
    return {
        "provider": candidate.provider,
        "model": candidate.model,
        "bug_tag": candidate.bug_tag,
    }
