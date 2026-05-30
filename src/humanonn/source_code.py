from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from humanonn.models import AuditReport, ScoreSummary, SignalBucket, SignalFinding, SignalTier
from humanonn.scoring import clamp, get_category, score_findings, MAX_RAW_SCORE
from humanonn.runtime import terminal_log


MEDIA_SCAN_SKIP_BYTES = 800 * 1024 * 1024
HARD_SCAN_SKIP_BYTES = 1500 * 1024 * 1024
LIKELY_BINARY_OR_MEDIA_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".dmg",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".pdf",
    ".png",
    ".so",
    ".tar",
    ".ttf",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}


SOURCE_RULE_POINTS = {
    # Tier 1 origin: 10 × 1.5 × 1.4 = 21
    "tailwind_default_purple_gradient": 21,
    "rounded_full_tailwind_buttons": 21,
    "systemic_backdrop_blur": 21,

    # Tier 2 origin: 6 × 1.5 × 1.4 = 13
    "default_inter_or_font_sans": 13,
    "stock_shadcn_imports_unmodified": 13,
    "missing_tailwind_design_tokens": 13,

    # Tier 3 polish: 3 × 1.0 × 1.4 = 4
    "transition_all_default": 4,
    "outline_none_without_focus_replacement": 4,
    "no_reduced_motion_source": 4,
    "magic_zindex_source": 4,
    "uniform_icon_size_source": 4,

    # Tier 4 polish: 2 × 1.0 × 1.4 = 3
    "production_placeholders": 3,
    "unoptimized_images_source": 3,

    # Additional checks (new)
    "barrel_index_exports": 13,
    "has_test_or_story_files": 4,
    "many_default_exports": 13,
    "custom_hooks_present": 13,
    "potential_prop_drilling": 13,
    "any_type_usage": 4,
    "missing_useeffect_deps": 13,
    "missing_globals_css": 6,
    "css_custom_properties_present": 6,
    "tailwind_spacing_scale_missing": 13,
    "tailwind_extend_spacing": 6,
    "at_layer_usage": 4,
    "inline_keyframes_present": 4,
    "duplicate_classname_patterns": 4,
    "unminified_build_artifacts": 3,
    "large_component_files": 4,
    "deep_component_folder_nesting": 6,
    "missing_eslint_config": 6,
    "missing_prettier_config": 4,
    "env_example_missing": 6,
    "pinned_dependencies_absent": 6,
    "missing_readme": 6,
    "lacks_storybook_config": 4,
    "many_anys_count": 4,
    "many_todos_count": 4,
    "missing_license": 3,
    "missing_gitignore": 3,
    "uses_eval_or_newfunction": 13,
    "inline_styles_usage": 4,
    "many_inline_images": 3,
    "missing_img_alt": 13,
    "multiple_package_managers": 4,
    "large_barrel_export_index": 6,
    "missing_tsconfig": 6,
    "uses_window_global_state": 6,
    "direct_dom_manipulation": 13,
    "uses_next_router_or_router_fallback": 6,
    "deprecated_lifecycle_methods": 13,
    "unstable_api_usage": 6,
}
SOURCE_SCORE_CAP = sum(SOURCE_RULE_POINTS.values())
SOURCE_DOM_SIGNAL_MAP = {
    "tailwind_default_purple_gradient": ["purple_accent", "gradient_text", "mesh_gradient"],
    "rounded_full_tailwind_buttons": ["pill_buttons"],
    "systemic_backdrop_blur": ["glassmorphism"],
    "missing_tailwind_design_tokens": ["wide_color_palette"],
    "default_inter_or_font_sans": ["inter_only"],
    "stock_shadcn_imports_unmodified": ["pill_buttons"],
    "transition_all_default": ["missing_transitions", "linear_animations"],
    "outline_none_without_focus_replacement": ["missing_focus_states", "no_focus_visible_distinction"],
    "no_reduced_motion_source": ["no_reduced_motion"],
    "magic_zindex_source": ["z_index_magic_numbers"],
    "barrel_index_exports": ["barrel_exports"],
    "missing_globals_css": ["missing_global_css"],
    "missing_useeffect_deps": ["missing_useeffect_deps"],
}


_SOURCE_SCAN_PROGRESS: dict[str, Any] = {
    "enabled": False,
    "count": 0,
    "total": 0,
    "log_fn": None,
}


@dataclass
class SourceFile:
    path: str
    content: str


@dataclass
class SourceFinding:
    id: str
    name: str
    tier: SignalTier
    bucket: SignalBucket
    weight: float
    flagged: bool
    points: int
    confidence: float
    reason: str
    evidence: dict[str, Any]


def apply_source_code_score(report: AuditReport, repo_url: str | None) -> AuditReport:
    if not repo_url:
        return report

    try:
        source_report = scan_public_github_repo(repo_url)
    except Exception as exc:
        report.source_code = {
            "repo_url": repo_url,
            "error": str(exc).splitlines()[0],
            "source_code_score": 0,
            "findings": [],
        }
        report.agent_notes.append(f"Source code scan failed for {repo_url}: {str(exc).splitlines()[0]}")
        return report

    boosted_signals = _boost_dom_confidence_from_source(report.findings, source_report)
    if boosted_signals:
        rescored = score_findings(
            report.findings,
            score_mode=report.score.score_mode,
            llm_adjustment=report.score.llm_adjustment,
        )
        report.score = rescored
        source_report["confidence_boosts"] = boosted_signals
        report.agent_notes.append(
            "Boosted DOM confidence to 1.0 from source-code agreement: "
            f"{', '.join(item['dom_signal_id'] for item in boosted_signals)}."
        )
    else:
        source_report["confidence_boosts"] = []

    scan_log = source_report.get("scan_log") or []
    if boosted_signals:
        scan_log.append(
            "Boosted DOM confidence to 1.0 from source-code agreement: "
            f"{', '.join(item['dom_signal_id'] for item in boosted_signals)}."
        )
    # source_raw_score is the weighted sum (points * confidence) of flagged source rules
    source_score = float(source_report["source_code_score"])
    normalized_source_score = _normalize_source_score(source_score)
    source_report["normalized_source_code_score"] = normalized_source_score
    source_report["raw_source_code_score"] = source_score
    rendered_vibe_score = report.score.vibe_score

    # compute live_raw_score from score summary (base_score + cluster_bonus)
    live_raw_score = float(report.score.base_score) + float(report.score.cluster_bonus)

    source_tier_counts = source_report.get("tier_counts") if isinstance(source_report.get("tier_counts"), dict) else {}

    # Unified raw pool merge
    combined_raw = live_raw_score + source_score
    MAX_COMBINED = MAX_RAW_SCORE + SOURCE_SCORE_CAP
    if MAX_COMBINED <= 0:
        final_vibe_score = 0
    else:
        final_vibe_score = round((combined_raw / MAX_COMBINED) * 100)
        final_vibe_score = max(0, min(100, final_vibe_score))

    # Keep trace fields as before
    report.score.rendered_vibe_score = rendered_vibe_score
    report.score.source_code_score = normalized_source_score
    report.score.vibe_score = final_vibe_score
    report.score.humanness_score = 100 - final_vibe_score
    report.score.category = get_category(
        final_score=final_vibe_score,
        live_tier_counts=report.score.tier_counts,
        source_tier_counts=source_tier_counts,
    )
    report.source_code = source_report
    scan_log.append(
        f"Added normalized source code score {normalized_source_score}/100 (raw {round(source_score,2)}/{SOURCE_SCORE_CAP}); "
        f"rendered vibe score {rendered_vibe_score}, final vibe score {final_vibe_score}."
    )
    source_report["scan_log"] = scan_log
    report.scan_code_log = list(scan_log)
    report.agent_notes.append(
        scan_log[-1]
    )
    return report


def build_source_only_report(url: str, repo_url: str | None) -> AuditReport:
    report = AuditReport(
        url=url,
        title="Source-only scan",
        score=ScoreSummary(
            vibe_score=0,
            humanness_score=100,
            base_score=0,
            cluster_bonus=0,
            tier_counts={},
            score_mode="source_only",
            llm_adjustment=0.0,
            rendered_vibe_score=None,
            source_code_score=0,
        ),
        findings=[],
        screenshot_path=None,
        scan_metadata={},
        agent_notes=["Live site scraping disabled by HUMANONN_LIVE_SITE_SCRAPING=false; using source-code scoring only."],
    )
    if repo_url:
        return apply_source_code_score(report, repo_url)
    report.agent_notes.append("No GitHub repo URL was provided, so source-code scoring could not run.")
    return report


def _boost_dom_confidence_from_source(findings: list[SignalFinding], source_report: dict[str, Any]) -> list[dict[str, Any]]:
    source_findings = source_report.get("findings", [])
    source_flagged = {
        finding.get("id")
        for finding in source_findings
        if isinstance(finding, dict) and finding.get("flagged")
    }
    if not source_flagged:
        return []

    findings_by_id = {finding.id: finding for finding in findings}
    boosts: list[dict[str, Any]] = []
    for source_id in source_flagged:
        for dom_signal_id in SOURCE_DOM_SIGNAL_MAP.get(str(source_id), []):
            finding = findings_by_id.get(dom_signal_id)
            if finding is None or not finding.flagged or finding.confidence >= 1.0:
                continue
            previous_confidence = finding.confidence
            finding.confidence = 1.0
            finding.evidence["source_code_agreement"] = source_id
            finding.evidence["previous_confidence"] = previous_confidence
            finding.reason = f"{finding.reason} Source code independently confirmed this signal via {source_id}."
            boosts.append(
                {
                    "source_signal_id": source_id,
                    "dom_signal_id": dom_signal_id,
                    "previous_confidence": previous_confidence,
                    "new_confidence": 1.0,
                }
            )
    return boosts


def scan_public_github_repo(repo_url: str) -> dict[str, Any]:
    owner, repo = _parse_github_repo(repo_url)
    default_branch = _fetch_default_branch(owner, repo)
    scan_log: list[str] = []
    _set_source_scan_progress(total=len(SOURCE_RULE_POINTS), scan_log=scan_log)
    _emit_source_scan_progress(f"Starting source-code scan for {owner}/{repo} on branch {default_branch}.")
    tree = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1")
    entries = tree.get("tree", [])
    file_entries, skipped_entries = _select_repo_files_for_scan(owner, repo, default_branch, entries)
    fetched_paths = [entry["path"] for entry in file_entries]
    fetched_structure = _format_repo_file_structure(fetched_paths)
    scan_log.append(
        f"Fetched repo file structure from GitHub ({len(fetched_paths)} files):\n{fetched_structure}"
    )
    files = [
        SourceFile(path=entry["path"], content=_fetch_text(_raw_github_file_url(owner, repo, default_branch, entry["path"])))
        for entry in file_entries
    ]
    _emit_source_scan_progress(
        f"Fetched {len(files)} repo files for scanning; skipped {len(skipped_entries)} oversized binary/media blobs."
    )
    findings = _evaluate_source_rules(files)
    # source_raw_score is weighted by confidence
    source_score = sum(finding.points * float(finding.confidence) for finding in findings if finding.flagged)
    # do not cap source_score here; normalization uses SOURCE_SCORE_CAP
    for finding in findings:
        state = "FLAGGED" if finding.flagged else "clear"
        scan_log.append(f"[{state}] {finding.id} - {finding.reason}")
    _emit_source_scan_progress(f"Computed raw source code score {round(source_score,2)}/{SOURCE_SCORE_CAP}.")
    _clear_source_scan_progress()
    return {
        "repo_url": repo_url,
        "owner": owner,
        "repo": repo,
        "branch": default_branch,
        "files_scanned": len(files),
        "files_skipped": len(skipped_entries),
        "fetched_file_paths": fetched_paths,
        "fetched_file_structure": fetched_structure,
        "bytes_scanned": sum(len(file.content.encode("utf-8", errors="ignore")) for file in files),
        "source_code_score": source_score,
        "normalized_source_code_score": _normalize_source_score(source_score),
        "score_cap": SOURCE_SCORE_CAP,
        "tier_counts": _source_tier_counts(findings),
        "findings": [asdict(finding) for finding in findings],
        "skipped_files": skipped_entries,
        "scan_log": scan_log,
    }


def _evaluate_source_rules(files: list[SourceFile]) -> list[SourceFinding]:
    jsx_files = [file for file in files if file.path.endswith((".tsx", ".jsx", ".js"))]
    code_files = [file for file in files if file.path.endswith((".tsx", ".jsx", ".ts", ".js", ".mjs", ".css"))]
    tailwind_files = [file for file in files if _is_tailwind_config(file.path)]
    next_config_files = [file for file in files if _is_next_config(file.path)]
    all_text = "\n".join(file.content for file in code_files)

    gradient_matches = _matches_by_file(
        code_files,
        re.compile(r"\b(?:from-(?:purple|violet)-\d{2,3}|to-pink-\d{2,3}|bg-gradient-to-br[^\"'`]*from-(?:purple|violet))\b"),
    )
    rounded_matches = _matches_by_file(jsx_files, re.compile(r"\brounded-full\b"))
    blur_matches = _matches_by_file(code_files, re.compile(r"\bbackdrop-blur(?:-[a-z0-9\[\]./]+)?\b"))
    shadcn_matches = _stock_shadcn_matches(files, code_files)
    transition_matches = _matches_by_file(
        code_files,
        re.compile(r"\btransition-all\b[^\"'`\\n]{0,80}\bduration-300\b|\btransition\s*:\s*['\"]?all\s+0\.3s\s+ease(?:-in-out)?"),
    )
    outline_matches = _outline_none_without_focus_matches(code_files)
    reduced_motion_present = "prefers-reduced-motion" in all_text
    placeholder_matches = _matches_by_file(
        code_files,
        re.compile(r"onClick=\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}|\bTODO\b|console\.log\s*\("),
    )
    magic_zindex_matches = _magic_zindex_matches(code_files)
    unoptimized_image_matches = _unoptimized_image_matches(jsx_files, next_config_files)
    uniform_icon_matches = _uniform_icon_size_matches(code_files)

    # New checks: file presence and regex-based heuristics
    barrel_matches = _barrel_export_matches(files)
    test_story_files = [file.path for file in files if re.search(r"\.test\.|\.spec\.|\.stories\.", file.path)]
    default_export_matches = _matches_by_file(code_files, re.compile(r"export\s+default\s", flags=re.IGNORECASE))
    custom_hooks = _matches_by_file(code_files, re.compile(r"function\s+use[A-Z]\w+|const\s+use[A-Z]\w+\s*=|export\s+function\s+use[A-Z]", flags=re.IGNORECASE))
    # heuristic for prop-drilling: JSX tags with many props (>5) in a single component instance
    prop_drill_matches = []
    for file in jsx_files:
        for match in re.finditer(r"<[A-Z][A-Za-z0-9_]*\b[^>]{30,400}>", file.content):
            snippet = match.group(0)
            props = re.findall(r"\w+\s*=\s*\{", snippet)
            if len(props) >= 6:
                prop_drill_matches.append({"path": file.path, "snippet": snippet[:240]})
                break
    any_usage_matches = _any_usage_matches([f for f in files if f.path.endswith(('.ts', '.tsx'))])
    useeffect_no_deps = []
    for file in code_files:
        for snippet in _useeffect_call_snippets(file.content):
            if not re.search(r"\,\s*\[\s*[^\]]*\s*\]", snippet):
                useeffect_no_deps.append({"path": file.path, "snippet": snippet[:160]})
                break
    globals_css_files = [file for file in files if file.path.endswith("globals.css") or file.path.endswith("global.css")]
    css_vars_present = bool(re.search(r"--[a-zA-Z0-9\-]+\s*:", all_text))
    tailwind_config_text = "\n".join(f.content for f in tailwind_files)
    tailwind_spacing = bool(re.search(r"spacing\s*:\s*\{", tailwind_config_text))
    tailwind_extend_spacing = bool(re.search(r"extend\s*:\s*\{[^}]*spacing", tailwind_config_text, flags=re.DOTALL))
    at_layer_usage = bool(re.search(r"@layer\b", all_text))
    inline_keyframes = bool(re.search(r"@keyframes\b", all_text))
    duplicate_classname = []
    for file in code_files:
        classes = re.findall(r"className\s*=\s*\{?['\"]([^'\"]+)['\"]\}?", file.content)
        seen: dict[str, int] = {}
        for cls in classes:
            seen[cls] = seen.get(cls, 0) + 1
            if seen[cls] >= 3:
                duplicate_classname.append({"path": file.path, "class": cls})
                break
    unminified_build = [file.path for file in files if re.search(r"\.bundle\.js$|\.min\.js$", file.path) and not file.path.endswith('.min.js')]
    large_files = [file.path for file in files if len(file.content) > 20000]
    deep_nesting = [file.path for file in files if file.path.count('/') >= 6]
    has_eslint = any(f.path.endswith('.eslintrc') or f.path.endswith('.eslintrc.json') for f in files)
    has_prettier = any(f.path.endswith('.prettierrc') or f.path.endswith('.prettierrc.js') for f in files)
    has_env_example = any(f.path.lower().endswith('.env.example') or f.path.lower().endswith('.env.sample') for f in files)
    pkg_json = next((f for f in files if f.path.endswith('package.json')), None)
    pinned_deps_absent = False
    multiple_lock = any(f.path.endswith('package-lock.json') or f.path.endswith('yarn.lock') or f.path.endswith('pnpm-lock.yaml') for f in files)
    many_anys = sum(_count_any_usages(f.content) for f in files if f.path.endswith(('.ts', '.tsx')))
    many_todos = sum(len(re.findall(r"\bTODO\b", f.content)) for f in files)
    has_license = any(f.path.lower().startswith('license') or f.path.lower().endswith('license') for f in files)
    has_gitignore = any(f.path == '.gitignore' for f in files)
    eval_usage = _matches_by_file(code_files, re.compile(r"\beval\s*\(|new\s+Function\s*\(", flags=re.IGNORECASE))
    inline_styles = _matches_by_file(jsx_files, re.compile(r"style=\{\s*\{", flags=re.IGNORECASE))
    inline_images_count = sum(len(re.findall(r"<img\b", f.content, flags=re.IGNORECASE)) for f in jsx_files)
    imgs_missing_alt = []
    for file in jsx_files:
        for m in re.finditer(r"<img\b([^>]*)>", file.content, flags=re.IGNORECASE):
            attrs = m.group(1)
            if not re.search(r"\balt\s*=", attrs):
                imgs_missing_alt.append({"path": file.path, "snippet": attrs[:120]})
                break
    multiple_pkg_mgr = multiple_lock
    large_barrel = [m for m in barrel_matches if len(m.get('matches', [])) >= 8]
    has_tsconfig = any(f.path.endswith('tsconfig.json') for f in files)
    uses_window = bool(re.search(r"\bwindow\.\w+\b", all_text))
    dom_manip = bool(re.search(r"document\.querySelector|getElementById|createElement\(|appendChild\(|innerHTML\s*=", all_text))
    uses_next_router = bool(re.search(r"next/router|useRouter\(|next/navigation", all_text))
    deprecated_lifecycle = bool(re.search(r"componentWillMount|componentWillReceiveProps|componentWillUpdate", all_text))
    unstable_api = bool(re.search(r"unstable_|experimental|next/experimental", all_text))

    # helper to compute confidence from matches list length
    def conf_from_file_count(matches_list: list[dict[str, Any]]) -> float:
        n = len(matches_list)
        if n >= 3:
            return 1.0
        if n == 2:
            return 0.8
        if n == 1:
            return 0.6
        return 0.0

    # compute confidences per rule using heuristics described
    gradient_conf = conf_from_file_count(gradient_matches)
    rounded_conf = conf_from_file_count(rounded_matches)
    blur_file_count = len({m["path"] for m in blur_matches})
    if blur_file_count >= 3:
        blur_conf = 1.0
    elif blur_file_count == 2:
        blur_conf = 0.9
    elif blur_file_count == 1:
        blur_conf = 0.6
    else:
        blur_conf = 0.0

    transition_conf = conf_from_file_count(transition_matches)
    outline_conf = conf_from_file_count(outline_matches)
    reduced_motion_conf = 0.0
    if bool(code_files) and not reduced_motion_present:
        reduced_motion_conf = 0.8 if len(code_files) >= 3 else 0.6

    placeholder_conf = conf_from_file_count(placeholder_matches)
    magic_zindex_conf = conf_from_file_count(magic_zindex_matches)

    # uniform_icon_matches may include occurrence info
    if uniform_icon_matches and isinstance(uniform_icon_matches, list) and uniform_icon_matches:
        first = uniform_icon_matches[0]
        occ = first.get("occurrences", 0)
        if occ >= 4:
            uniform_icon_conf = 1.0
        else:
            uniform_icon_conf = 0.7
    else:
        uniform_icon_conf = 0.0

    shadcn_conf = 0.7 if shadcn_matches else 0.0
    missing_tailwind_conf = 0.8 if _uses_tailwind_classes(all_text) and not _has_custom_tailwind_colors(tailwind_files) else 0.0
    default_font_conf = 0.8 if _uses_default_font_stack(code_files, tailwind_files) else 0.0
    unoptimized_images_conf = 0.9 if unoptimized_image_matches else 0.0

    # confidences for new heuristics
    barrel_conf = conf_from_file_count(barrel_matches)
    tests_conf = 0.8 if len(test_story_files) >= 2 else (0.6 if test_story_files else 0.0)
    default_export_conf = conf_from_file_count(default_export_matches)
    hooks_conf = conf_from_file_count(custom_hooks)
    prop_drill_conf = 0.9 if prop_drill_matches else 0.0
    any_conf = conf_from_file_count(any_usage_matches)
    useeffect_conf = 0.9 if useeffect_no_deps else 0.0
    globals_conf = 0.9 if globals_css_files and not css_vars_present else (0.6 if globals_css_files else 0.0)
    css_vars_conf = 0.9 if css_vars_present else 0.0
    tailwind_spacing_conf = 0.9 if not tailwind_spacing and _uses_tailwind_classes(all_text) else (0.0 if tailwind_spacing else 0.0)
    tailwind_extend_conf = 0.8 if tailwind_extend_spacing else 0.0
    at_layer_conf = 0.8 if at_layer_usage else 0.0
    keyframes_conf = 0.8 if inline_keyframes else 0.0
    dup_class_conf = 0.8 if duplicate_classname else 0.0
    unmin_conf = 0.7 if unminified_build else 0.0
    large_files_conf = 0.8 if large_files else 0.0
    deep_nest_conf = 0.7 if deep_nesting else 0.0
    eslint_conf = 0.0 if has_eslint else 0.8
    prettier_conf = 0.0 if has_prettier else 0.6
    env_conf = 0.0 if has_env_example else 0.8
    pinned_conf = 0.8 if pkg_json and re.search(r"\^|~", pkg_json.content) else 0.0
    readme_conf = 0.0 if any(f.path.lower().endswith('readme.md') for f in files) else 0.8
    storybook_conf = 0.8 if not any(f.path.startswith('.storybook') or f.path.startswith('storybook') for f in files) else 0.0
    many_anys_conf = 0.9 if many_anys >= 4 else (0.6 if many_anys >= 2 else 0.0)
    many_todos_conf = 0.8 if many_todos >= 3 else (0.6 if many_todos >= 1 else 0.0)
    license_conf = 0.0 if has_license else 0.6
    gitignore_conf = 0.0 if has_gitignore else 0.6
    eval_conf = conf_from_file_count(eval_usage)
    inline_styles_conf = conf_from_file_count(inline_styles)
    many_imgs_conf = 0.8 if inline_images_count >= 6 else (0.6 if inline_images_count >= 2 else 0.0)
    missing_alt_conf = 0.9 if imgs_missing_alt else 0.0
    multiple_mgr_conf = 0.8 if multiple_pkg_mgr else 0.0
    large_barrel_conf = 0.8 if large_barrel else 0.0
    tsconfig_conf = 0.0 if has_tsconfig else 0.8
    window_conf = 0.8 if uses_window else 0.0
    dom_conf = 0.9 if dom_manip else 0.0
    router_conf = 0.8 if uses_next_router else 0.0
    deprecated_conf = 0.9 if deprecated_lifecycle else 0.0
    unstable_conf = 0.7 if unstable_api else 0.0

    return [
        _source_finding(
            "tailwind_default_purple_gradient",
            "Default Tailwind purple/pink gradient classes in source",
            1,
            "origin",
            SOURCE_RULE_POINTS["tailwind_default_purple_gradient"],
            bool(gradient_matches),
            gradient_conf,
            "Source uses default Tailwind purple/violet/pink gradient classes."
            if gradient_matches
            else "No default Tailwind purple/pink gradient classes found in fetched source.",
            {"matches": gradient_matches[:12]},
        ),
        _source_finding(
            "rounded_full_tailwind_buttons",
            "Tailwind rounded-full used in JSX components",
            1,
            "origin",
            SOURCE_RULE_POINTS["rounded_full_tailwind_buttons"],
            bool(rounded_matches),
            rounded_conf,
            "JSX source uses rounded-full, confirming pill radius came from Tailwind classes."
            if rounded_matches
            else "No rounded-full class found in JSX/TSX source.",
            {"matches": rounded_matches[:12]},
        ),
        _source_finding(
            "systemic_backdrop_blur",
            "Backdrop blur used across multiple source files",
            1,
            "origin",
            SOURCE_RULE_POINTS["systemic_backdrop_blur"],
            len({match["path"] for match in blur_matches}) >= 2,
            blur_conf,
            "backdrop-blur appears in multiple files, suggesting systemic glassmorphism."
            if len({match["path"] for match in blur_matches}) >= 2
            else "backdrop-blur was not found across multiple unrelated files.",
            {"matches": blur_matches[:12], "file_count": len({match["path"] for match in blur_matches})},
        ),
        _source_finding(
            "missing_tailwind_design_tokens",
            "Tailwind config lacks custom color tokens",
            2,
            "origin",
            SOURCE_RULE_POINTS["missing_tailwind_design_tokens"],
            _uses_tailwind_classes(all_text) and not _has_custom_tailwind_colors(tailwind_files),
            missing_tailwind_conf,
            "Tailwind classes are used, but no custom color tokens were found in tailwind config."
            if _uses_tailwind_classes(all_text) and not _has_custom_tailwind_colors(tailwind_files)
            else "Custom Tailwind color tokens appear to be present or Tailwind usage was not confirmed.",
            {"tailwind_config_files": [file.path for file in tailwind_files]},
        ),
        _source_finding(
            "default_inter_or_font_sans",
            "Default Inter/Geist/font-sans typography in source",
            2,
            "origin",
            SOURCE_RULE_POINTS["default_inter_or_font_sans"],
            _uses_default_font_stack(code_files, tailwind_files),
            default_font_conf,
            "Source uses font-sans, Inter, or Geist without evidence of a custom font setup."
            if _uses_default_font_stack(code_files, tailwind_files)
            else "Default Inter/Geist/font-sans typography was not confirmed.",
            {"tailwind_config_files": [file.path for file in tailwind_files]},
        ),
        _source_finding(
            "stock_shadcn_imports_unmodified",
            "Stock shadcn/ui imports with near-default local primitives",
            2,
            "origin",
            SOURCE_RULE_POINTS["stock_shadcn_imports_unmodified"],
            bool(shadcn_matches),
            shadcn_conf,
            "Source imports shadcn/ui primitives and local component files look near-default."
            if shadcn_matches
            else "No near-default shadcn/ui primitive usage was confirmed.",
            {"matches": shadcn_matches[:12]},
        ),
        _source_finding(
            "transition_all_default",
            "Default transition-all duration-300 animation pattern",
            3,
            "polish",
            SOURCE_RULE_POINTS["transition_all_default"],
            bool(transition_matches),
            transition_conf,
            "Source uses transition-all/duration-300 or transition: all 0.3s ease."
            if transition_matches
            else "No default transition-all pattern found.",
            {"matches": transition_matches[:12]},
        ),
        _source_finding(
            "outline_none_without_focus_replacement",
            "outline-none without nearby focus replacement",
            3,
            "polish",
            SOURCE_RULE_POINTS["outline_none_without_focus_replacement"],
            bool(outline_matches),
            outline_conf,
            "Source removes outlines without nearby ring/focus-visible replacement classes."
            if outline_matches
            else "No unsafe outline-none usage confirmed.",
            {"matches": outline_matches[:12]},
        ),
        _source_finding(
            "no_reduced_motion_source",
            "No prefers-reduced-motion handling in source",
            3,
            "polish",
            SOURCE_RULE_POINTS["no_reduced_motion_source"],
            bool(code_files) and not reduced_motion_present,
            reduced_motion_conf,
            "No prefers-reduced-motion media query or equivalent source handling was found."
            if bool(code_files) and not reduced_motion_present
            else "prefers-reduced-motion handling was found or no source files were scanned.",
            {"files_checked": len(code_files)},
        ),
        _source_finding(
            "production_placeholders",
            "Production placeholders or debug logs left in components",
            3,
            "polish",
            SOURCE_RULE_POINTS["production_placeholders"],
            bool(placeholder_matches),
            placeholder_conf,
            "Source contains empty onClick handlers, TODO markers, or console.log calls."
            if placeholder_matches
            else "No obvious production placeholders or debug logs found.",
            {"matches": placeholder_matches[:12]},
        ),
        _source_finding(
            "magic_zindex_source",
            "Magic z-index values in source",
            3,
            "origin",
            SOURCE_RULE_POINTS["magic_zindex_source"],
            bool(magic_zindex_matches),
            magic_zindex_conf,
            "Source uses arbitrary z-index values such as z-[999] or z-index: 999."
            if magic_zindex_matches
            else "No magic z-index values found in source.",
            {"matches": magic_zindex_matches[:12]},
        ),
        _source_finding(
            "unoptimized_images_source",
            "Raw <img> tags in a Next.js frontend",
            4,
            "polish",
            SOURCE_RULE_POINTS["unoptimized_images_source"],
            bool(unoptimized_image_matches),
            unoptimized_images_conf,
            "Next.js source uses raw <img> tags instead of next/image."
            if unoptimized_image_matches
            else "No unoptimized raw <img> usage confirmed in a Next.js frontend.",
            {"matches": unoptimized_image_matches[:12], "next_config_files": [file.path for file in next_config_files]},
        ),
        _source_finding(
            "uniform_icon_size_source",
            "Lucide icons all use the same size",
            4,
            "origin",
            SOURCE_RULE_POINTS["uniform_icon_size_source"],
            bool(uniform_icon_matches),
            uniform_icon_conf,
            "Lucide icon usages all resolve to the same size value across the scanned source."
            if uniform_icon_matches
            else "Icon sizing varies or could not be confirmed from the scanned source.",
            {"matches": uniform_icon_matches[:12]},
        ),
        _source_finding(
            "barrel_index_exports",
            "Barrel/index export usage detected",
            2,
            "structure",
            SOURCE_RULE_POINTS["barrel_index_exports"],
            bool(barrel_matches),
            barrel_conf,
            "Found index/barrel exports in source files.",
            {"matches": barrel_matches[:12]},
        ),
        _source_finding(
            "has_test_or_story_files",
            "Repository contains test or story files",
            3,
            "quality",
            SOURCE_RULE_POINTS["has_test_or_story_files"],
            bool(test_story_files),
            tests_conf,
            "Found test or story files indicating component testing/stories.",
            {"files": test_story_files[:12]},
        ),
        _source_finding(
            "many_default_exports",
            "Multiple default exports present",
            2,
            "structure",
            SOURCE_RULE_POINTS["many_default_exports"],
            bool(default_export_matches),
            default_export_conf,
            "Default exports present in source files.",
            {"matches": default_export_matches[:12]},
        ),
        _source_finding(
            "custom_hooks_present",
            "Custom React hooks detected",
            2,
            "structure",
            SOURCE_RULE_POINTS["custom_hooks_present"],
            bool(custom_hooks),
            hooks_conf,
            "Custom hooks (useXYZ) were detected in source.",
            {"matches": custom_hooks[:12]},
        ),
        _source_finding(
            "potential_prop_drilling",
            "Potential prop-drilling heuristic (large prop lists)",
            2,
            "structure",
            SOURCE_RULE_POINTS["potential_prop_drilling"],
            bool(prop_drill_matches),
            prop_drill_conf,
            "Found component instances with many props in JSX suggesting prop drilling.",
            {"matches": prop_drill_matches[:6]},
        ),
        _source_finding(
            "any_type_usage",
            "Uses TypeScript `any` type",
            3,
            "quality",
            SOURCE_RULE_POINTS["any_type_usage"],
            bool(any_usage_matches),
            any_conf,
            "Found uses of `any` in TypeScript source.",
            {"matches": any_usage_matches[:12]},
        ),
        _source_finding(
            "missing_useeffect_deps",
            "useEffect calls without dependency arrays",
            2,
            "quality",
            SOURCE_RULE_POINTS["missing_useeffect_deps"],
            bool(useeffect_no_deps),
            useeffect_conf,
            "Detected useEffect calls that likely lack dependency arrays.",
            {"matches": useeffect_no_deps[:12]},
        ),
        _source_finding(
            "missing_globals_css",
            "Missing or empty globals.css",
            2,
            "style",
            SOURCE_RULE_POINTS["missing_globals_css"],
            bool(globals_css_files) and not css_vars_present,
            globals_conf,
            "globals.css present but no CSS custom properties found." if globals_css_files else "No globals.css found.",
            {"globals_files": [f.path for f in globals_css_files]},
        ),
        _source_finding(
            "css_custom_properties_present",
            "CSS custom properties present",
            2,
            "style",
            SOURCE_RULE_POINTS["css_custom_properties_present"],
            css_vars_present,
            css_vars_conf,
            "CSS custom properties were found in source.",
            {"present": css_vars_present},
        ),
        _source_finding(
            "tailwind_spacing_scale_missing",
            "Tailwind spacing scale appears unextended",
            2,
            "style",
            SOURCE_RULE_POINTS["tailwind_spacing_scale_missing"],
            _uses_tailwind_classes(all_text) and not tailwind_spacing,
            tailwind_spacing_conf,
            "Tailwind classes used but no spacing scale found in tailwind.config.",
            {"tailwind_config_files": [file.path for file in tailwind_files]},
        ),
        _source_finding(
            "tailwind_extend_spacing",
            "Tailwind extend.spacing usage detected",
            3,
            "style",
            SOURCE_RULE_POINTS["tailwind_extend_spacing"],
            tailwind_extend_spacing,
            tailwind_extend_conf,
            "tailwind.config extends spacing scale.",
            {"tailwind_config_files": [file.path for file in tailwind_files]},
        ),
        _source_finding(
            "at_layer_usage",
            "@layer usage in CSS/tailwind",
            3,
            "style",
            SOURCE_RULE_POINTS["at_layer_usage"],
            at_layer_usage,
            at_layer_conf,
            "Found @layer rules in CSS or config.",
            {},
        ),
        _source_finding(
            "inline_keyframes_present",
            "Inline @keyframes detected",
            3,
            "style",
            SOURCE_RULE_POINTS["inline_keyframes_present"],
            inline_keyframes,
            keyframes_conf,
            "Inline keyframes present in CSS/source.",
            {},
        ),
        _source_finding(
            "duplicate_classname_patterns",
            "Duplicate className patterns in source",
            3,
            "style",
            SOURCE_RULE_POINTS["duplicate_classname_patterns"],
            bool(duplicate_classname),
            dup_class_conf,
            "Found duplicate className patterns in files.",
            {"matches": duplicate_classname[:12]},
        ),
        _source_finding(
            "unminified_build_artifacts",
            "Unminified build artifacts present",
            4,
            "ops",
            SOURCE_RULE_POINTS["unminified_build_artifacts"],
            bool(unminified_build),
            unmin_conf,
            "Found build artifact files that look unminified or bundles.",
            {"files": unminified_build[:12]},
        ),
        _source_finding(
            "large_component_files",
            "Large source files detected",
            3,
            "quality",
            SOURCE_RULE_POINTS["large_component_files"],
            bool(large_files),
            large_files_conf,
            "Found very large source files (>20k chars).",
            {"files": large_files[:12]},
        ),
        _source_finding(
            "deep_component_folder_nesting",
            "Deeply nested component folders detected",
            2,
            "structure",
            SOURCE_RULE_POINTS["deep_component_folder_nesting"],
            bool(deep_nesting),
            deep_nest_conf,
            "Found files nested deeply in directories (>=6 levels).",
            {"examples": deep_nesting[:12]},
        ),
        _source_finding(
            "missing_eslint_config",
            "ESLint config missing",
            2,
            "quality",
            SOURCE_RULE_POINTS["missing_eslint_config"],
            not has_eslint,
            eslint_conf,
            "No ESLint configuration file found.",
            {},
        ),
        _source_finding(
            "missing_prettier_config",
            "Prettier config missing",
            3,
            "quality",
            SOURCE_RULE_POINTS["missing_prettier_config"],
            not has_prettier,
            prettier_conf,
            "No Prettier configuration file found.",
            {},
        ),
        _source_finding(
            "env_example_missing",
            "Missing .env.example or sample",
            2,
            "ops",
            SOURCE_RULE_POINTS["env_example_missing"],
            not has_env_example,
            env_conf,
            "No .env.example or .env.sample found in repository.",
            {},
        ),
        _source_finding(
            "pinned_dependencies_absent",
            "Dependencies appear unpinned (semver ranges)",
            2,
            "ops",
            SOURCE_RULE_POINTS["pinned_dependencies_absent"],
            bool(pkg_json) and bool(re.search(r"\^|~", pkg_json.content)),
            pinned_conf,
            "package.json contains semver ranges like ^ or ~ indicating unpinned deps.",
            {"path": pkg_json.path if pkg_json else None},
        ),
        _source_finding(
            "missing_readme",
            "Repository README missing",
            2,
            "ops",
            SOURCE_RULE_POINTS["missing_readme"],
            not any(f.path.lower().endswith('readme.md') for f in files),
            readme_conf,
            "No README.md found in repository.",
            {},
        ),
        _source_finding(
            "lacks_storybook_config",
            "No Storybook config detected",
            3,
            "quality",
            SOURCE_RULE_POINTS["lacks_storybook_config"],
            not any(f.path.startswith('.storybook') or f.path.startswith('storybook') for f in files),
            storybook_conf,
            "No .storybook or storybook config found.",
            {},
        ),
        _source_finding(
            "many_anys_count",
            "High count of TypeScript `any` usages",
            3,
            "quality",
            SOURCE_RULE_POINTS["many_anys_count"],
            many_anys >= 4,
            many_anys_conf,
            f"Found {many_anys} uses of `any` in TS files.",
            {},
        ),
        _source_finding(
            "many_todos_count",
            "Many TODO markers in source",
            3,
            "quality",
            SOURCE_RULE_POINTS["many_todos_count"],
            many_todos >= 3,
            many_todos_conf,
            f"Found {many_todos} TODO markers in source.",
            {},
        ),
        _source_finding(
            "missing_license",
            "LICENSE file missing",
            4,
            "ops",
            SOURCE_RULE_POINTS["missing_license"],
            not has_license,
            license_conf,
            "No LICENSE file found in repository.",
            {},
        ),
        _source_finding(
            "missing_gitignore",
            ".gitignore missing",
            4,
            "ops",
            SOURCE_RULE_POINTS["missing_gitignore"],
            not has_gitignore,
            gitignore_conf,
            "No .gitignore file found in repository.",
            {},
        ),
        _source_finding(
            "uses_eval_or_newfunction",
            "Uses eval() or new Function()",
            2,
            "security",
            SOURCE_RULE_POINTS["uses_eval_or_newfunction"],
            bool(eval_usage),
            eval_conf,
            "Found use of eval or new Function in source.",
            {"matches": eval_usage[:6]},
        ),
        _source_finding(
            "inline_styles_usage",
            "Inline style props detected",
            3,
            "style",
            SOURCE_RULE_POINTS["inline_styles_usage"],
            bool(inline_styles),
            inline_styles_conf,
            "Found inline style={{ ... }} usage in JSX.",
            {"matches": inline_styles[:6]},
        ),
        _source_finding(
            "many_inline_images",
            "Many raw <img> tags detected",
            4,
            "content",
            SOURCE_RULE_POINTS["many_inline_images"],
            inline_images_count >= 6,
            many_imgs_conf,
            f"Found {inline_images_count} raw <img> usages in JSX files.",
            {},
        ),
        _source_finding(
            "missing_img_alt",
            "Images missing alt attributes",
            2,
            "a11y",
            SOURCE_RULE_POINTS["missing_img_alt"],
            bool(imgs_missing_alt),
            missing_alt_conf,
            "Found <img> elements without alt attributes.",
            {"matches": imgs_missing_alt[:12]},
        ),
        _source_finding(
            "multiple_package_managers",
            "Multiple package manager lockfiles detected",
            4,
            "ops",
            SOURCE_RULE_POINTS["multiple_package_managers"],
            multiple_pkg_mgr,
            multiple_mgr_conf,
            "Found multiple package manager lockfiles (yarn.lock, package-lock.json, pnpm-lock.yaml).",
            {},
        ),
        _source_finding(
            "large_barrel_export_index",
            "Large barrel/index export detected",
            3,
            "structure",
            SOURCE_RULE_POINTS["large_barrel_export_index"],
            bool(large_barrel),
            large_barrel_conf,
            "Found barrel/index exports exporting many symbols.",
            {"examples": large_barrel[:6]},
        ),
        _source_finding(
            "missing_tsconfig",
            "tsconfig.json missing for TypeScript projects",
            2,
            "ops",
            SOURCE_RULE_POINTS["missing_tsconfig"],
            not has_tsconfig and any(f.path.endswith(('.ts', '.tsx')) for f in files),
            tsconfig_conf,
            "No tsconfig.json found while TypeScript files exist.",
            {},
        ),
        _source_finding(
            "uses_window_global_state",
            "Window global state or globals used",
            2,
            "quality",
            SOURCE_RULE_POINTS["uses_window_global_state"],
            uses_window,
            window_conf,
            "Detected usage of window.<var> in source.",
            {},
        ),
        _source_finding(
            "direct_dom_manipulation",
            "Direct DOM manipulation detected",
            2,
            "security",
            SOURCE_RULE_POINTS["direct_dom_manipulation"],
            dom_manip,
            dom_conf,
            "Found document.querySelector / innerHTML / appendChild usage.",
            {},
        ),
        _source_finding(
            "uses_next_router_or_router_fallback",
            "Next/router or router fallbacks detected",
            2,
            "structure",
            SOURCE_RULE_POINTS["uses_next_router_or_router_fallback"],
            uses_next_router,
            router_conf,
            "Detected next/router or useRouter usage.",
            {},
        ),
        _source_finding(
            "deprecated_lifecycle_methods",
            "Deprecated React lifecycle methods detected",
            2,
            "quality",
            SOURCE_RULE_POINTS["deprecated_lifecycle_methods"],
            deprecated_lifecycle,
            deprecated_conf,
            "Found deprecated React component lifecycle methods.",
            {},
        ),
        _source_finding(
            "unstable_api_usage",
            "Unstable/experimental API usage detected",
            3,
            "quality",
            SOURCE_RULE_POINTS["unstable_api_usage"],
            unstable_api,
            unstable_conf,
            "Found keywords like unstable_/experimental or next/experimental imports.",
            {},
        ),
    ]


def _parse_github_repo(repo_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(repo_url.strip())
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        raise ValueError("Repo URL must be a public github.com URL.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub repo URL must include owner and repo.")
    return parts[0], parts[1].removesuffix(".git")


def _fetch_default_branch(owner: str, repo: str) -> str:
    repo_info = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
    default_branch = str(repo_info.get("default_branch") or "main").strip()
    return default_branch or "main"


def _select_repo_files_for_scan(
    owner: str,
    repo: str,
    default_branch: str,
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    file_entries: list[dict[str, Any]] = []
    skipped_entries: list[dict[str, Any]] = []
    for item in entries:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path", ""))
        size_bytes = _entry_size_bytes(owner, repo, item)
        if not _should_fetch_path(path, size_bytes=size_bytes):
            skipped_entries.append(
                {
                    "path": path,
                    "size_bytes": size_bytes,
                    "reason": _skip_reason_for_path(path, size_bytes),
                }
            )
            continue
        file_entries.append(item)
    return file_entries, skipped_entries


def _should_fetch_path(path: str, size_bytes: int | None = None) -> bool:
    normalized = path.replace("\\", "/")
    if size_bytes is None:
        return True
    if size_bytes > HARD_SCAN_SKIP_BYTES:
        return False
    if size_bytes >= MEDIA_SCAN_SKIP_BYTES and _looks_like_binary_or_media_path(normalized):
        return False
    return True


def _skip_reason_for_path(path: str, size_bytes: int | None) -> str:
    normalized = path.replace("\\", "/")
    if size_bytes is None:
        return "size unavailable"
    if size_bytes > HARD_SCAN_SKIP_BYTES:
        return f"size {size_bytes} exceeds hard scan limit"
    if size_bytes >= MEDIA_SCAN_SKIP_BYTES and _looks_like_binary_or_media_path(normalized):
        return f"media/binary file {size_bytes} bytes exceeds practical scan threshold"
    return "filtered out"


def _entry_size_bytes(owner: str, repo: str, item: dict[str, Any]) -> int | None:
    size = item.get("size")
    if isinstance(size, int):
        return size
    if isinstance(size, str) and size.isdigit():
        return int(size)
    sha = item.get("sha")
    if not sha:
        return None
    try:
        blob = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha}")
    except Exception:
        return None
    blob_size = blob.get("size")
    if isinstance(blob_size, int):
        return blob_size
    if isinstance(blob_size, str) and blob_size.isdigit():
        return int(blob_size)
    return None


def _looks_like_binary_or_media_path(path: str) -> bool:
    lowered = path.lower()
    return any(lowered.endswith(extension) for extension in LIKELY_BINARY_OR_MEDIA_EXTENSIONS)


def _raw_github_file_url(owner: str, repo: str, default_branch: str, path: str) -> str:
    encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{encoded_path}"


def _format_repo_file_structure(paths: list[str]) -> str:
    cleaned_paths = sorted({path.replace("\\", "/").strip("/") for path in paths if path})
    if not cleaned_paths:
        return "(no files fetched)"

    tree: dict[str, Any] = {}
    for path in cleaned_paths:
        node = tree
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(parts[-1])

    lines: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        dir_names = sorted(name for name in node.keys() if name != "__files__")
        file_names = sorted(node.get("__files__", []))
        entries = [("dir", name) for name in dir_names] + [("file", name) for name in file_names]
        for index, (entry_type, name) in enumerate(entries):
            is_last = index == len(entries) - 1
            branch = "`-- " if is_last else "|-- "
            suffix = "/" if entry_type == "dir" else ""
            lines.append(f"{prefix}{branch}{name}{suffix}")
            if entry_type == "dir":
                walk(node[name], prefix + ("    " if is_last else "|   "))

    walk(tree, "")
    return "\n".join(lines)


def _is_tailwind_config(path: str) -> bool:
    name = path.replace("\\", "/").split("/")[-1]
    return name.startswith("tailwind.config.")


def _is_next_config(path: str) -> bool:
    name = path.replace("\\", "/").split("/")[-1]
    return name.startswith("next.config.")


def _fetch_json(url: str) -> dict[str, Any]:
    return json.loads(_fetch_text(url))


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "humanonn-source-scanner"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _matches_by_file(files: list[SourceFile], pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for file in files:
        found = sorted(set(pattern.findall(file.content)))
        if found:
            matches.append({"path": file.path, "matches": found[:12]})
    return matches


def _barrel_export_matches(files: list[SourceFile]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for file in files:
        normalized = file.path.replace("\\", "/")
        filename = normalized.rsplit("/", 1)[-1]
        if not re.match(r"^index\.(?:ts|tsx|js|jsx|mjs)$", filename):
            continue
        export_lines = re.findall(
            r"^\s*export\s+(?:\*\s+from|\{[^}]+\}\s+from)\s+['\"][^'\"]+['\"]\s*$",
            file.content,
            flags=re.MULTILINE,
        )
        if len(export_lines) >= 2 or any("export * from" in line for line in export_lines):
            matches.append({"path": file.path, "matches": export_lines[:12]})
    return matches


def _any_usage_matches(files: list[SourceFile]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?:\b:\s*any\b|\bas\s+any\b|\bany\s*\[\]|\bArray<\s*any\s*>|\bPromise<\s*any\s*>|\bRecord<[^>]*\bany\b[^>]*>)",
        flags=re.IGNORECASE,
    )
    for file in files:
        snippets: list[str] = []
        for match in pattern.finditer(file.content):
            start = max(0, match.start() - 24)
            end = min(len(file.content), match.end() + 24)
            snippets.append(" ".join(file.content[start:end].split())[:120])
        if snippets:
            matches.append({"path": file.path, "matches": snippets[:12]})
    return matches


def _count_any_usages(content: str) -> int:
    pattern = re.compile(
        r"(?:\b:\s*any\b|\bas\s+any\b|\bany\s*\[\]|\bArray<\s*any\s*>|\bPromise<\s*any\s*>|\bRecord<[^>]*\bany\b[^>]*>)",
        flags=re.IGNORECASE,
    )
    return len(pattern.findall(content))


def _useeffect_call_snippets(content: str) -> list[str]:
    snippets: list[str] = []
    for match in re.finditer(r"useEffect\s*\((?:.|\n){0,500}?\)", content):
        snippets.append(match.group(0))
    return snippets


def _normalize_source_score(source_score: int) -> int:
    if SOURCE_SCORE_CAP <= 0:
        return 0
    return clamp(0, 100, round((source_score / SOURCE_SCORE_CAP) * 100))


def _unoptimized_image_matches(jsx_files: list[SourceFile], next_config_files: list[SourceFile]) -> list[dict[str, Any]]:
    if not next_config_files:
        return []
    matches: list[dict[str, Any]] = []
    pattern = re.compile(r"<img\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
    for file in jsx_files:
        snippets = []
        for match in pattern.finditer(file.content):
            start = max(0, match.start() - 40)
            end = min(len(file.content), match.end() + 40)
            snippets.append(" ".join(file.content[start:end].split())[:180])
        if snippets:
            matches.append({"path": file.path, "matches": snippets[:6]})
    return matches


def _uniform_icon_size_matches(files: list[SourceFile]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    all_sizes: list[str] = []
    for file in files:
        icon_names = _lucide_icon_names(file.content)
        if not icon_names:
            continue
        snippets: list[str] = []
        for icon_name in icon_names:
            icon_pattern = re.compile(rf"\b{re.escape(icon_name)}\b[^\n<]{{0,120}}\bsize\s*=\s*(?:\{{\s*)?['\"]?(\d+(?:\.\d+)?)['\"]?(?:\s*\}})?")
            for match in icon_pattern.finditer(file.content):
                size = match.group(1)
                all_sizes.append(size)
                start = max(0, match.start() - 40)
                end = min(len(file.content), match.end() + 60)
                snippets.append(" ".join(file.content[start:end].split())[:180])
        if snippets:
            matches.append({"path": file.path, "matches": snippets[:6]})
    unique_sizes = {size for size in all_sizes if size}
    if len(all_sizes) >= 4 and len(unique_sizes) == 1:
        return [{"size": next(iter(unique_sizes)), "occurrences": len(all_sizes), "files": len(matches), "matches": matches[:12]}]
    return []


def _magic_zindex_matches(files: list[SourceFile]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    pattern = re.compile(r"(?:\bz-\[(\d{3,})\]\b|\bzIndex\s*[:=]\s*['\"]?(\d{3,})['\"]?|\bz-index\s*:\s*['\"]?(\d{3,})['\"]?)")
    for file in files:
        snippets: list[str] = []
        for match in pattern.finditer(file.content):
            start = max(0, match.start() - 40)
            end = min(len(file.content), match.end() + 60)
            snippets.append(" ".join(file.content[start:end].split())[:180])
        if snippets:
            matches.append({"path": file.path, "matches": snippets[:6]})
    return matches


def _lucide_icon_names(content: str) -> list[str]:
    imports = re.findall(r"import\s*\{([^}]+)\}\s*from\s*['\"]lucide-react['\"]", content)
    names: set[str] = set()
    for group in imports:
        for raw_name in group.split(","):
            name = raw_name.strip().split(" as ")[-1].strip()
            if name:
                names.add(name)
    return sorted(names)


def _source_finding(
    signal_id: str,
    name: str,
    tier: SignalTier,
    bucket: SignalBucket,
    points: int,
    flagged: bool,
    confidence: float,
    reason: str,
    evidence: dict[str, Any],
) -> SourceFinding:
    finding = SourceFinding(
        id=signal_id,
        name=name,
        tier=tier,
        bucket=bucket,
        weight=float(points),
        flagged=flagged,
        points=points if flagged else 0,
        confidence=confidence,
        reason=reason,
        evidence=evidence,
    )
    _emit_source_scan_progress(f"Checked source rule {_SOURCE_SCAN_PROGRESS['count'] + 1}/{_SOURCE_SCAN_PROGRESS['total']}: {signal_id} -> {'FLAGGED' if flagged else 'clear'}")
    _SOURCE_SCAN_PROGRESS["count"] = int(_SOURCE_SCAN_PROGRESS["count"]) + 1
    return finding


def _source_tier_counts(findings: list[SourceFinding]) -> dict[int, int]:
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for finding in findings:
        if finding.flagged:
            counts[int(finding.tier)] += 1
    return counts


def _stock_shadcn_matches(files: list[SourceFile], code_files: list[SourceFile]) -> list[dict[str, Any]]:
    import_pattern = re.compile(r"@/components/ui/(button|card|badge)")
    imported_primitives = sorted(
        {
            primitive
            for file in code_files
            for primitive in import_pattern.findall(file.content)
        }
    )
    if not imported_primitives:
        return []

    files_by_path = {file.path.replace("\\", "/").lower(): file for file in files}
    matches: list[dict[str, Any]] = []
    for primitive in imported_primitives:
        candidates = [
            f"components/ui/{primitive}.tsx",
            f"src/components/ui/{primitive}.tsx",
            f"app/components/ui/{primitive}.tsx",
        ]
        component = next((files_by_path[path] for path in candidates if path in files_by_path), None)
        if component is None:
            matches.append({"primitive": primitive, "reason": "imported but local primitive file was not found"})
            continue
        if _looks_like_stock_shadcn_primitive(primitive, component.content):
            matches.append({"primitive": primitive, "path": component.path, "reason": "local primitive matches stock shadcn structure"})
    return matches


def _looks_like_stock_shadcn_primitive(primitive: str, content: str) -> bool:
    stock_markers = [
        "class-variance-authority",
        "Slot",
        "cn(",
        "React.forwardRef",
        "VariantProps",
    ]
    marker_count = sum(1 for marker in stock_markers if marker in content)
    primitive_markers = {
        "button": ["buttonVariants", "defaultVariants", "variant", "size"],
        "card": ["CardHeader", "CardContent", "CardFooter", "CardTitle"],
        "badge": ["badgeVariants", "defaultVariants", "variant"],
    }
    primitive_count = sum(1 for marker in primitive_markers.get(primitive, []) if marker in content)
    return marker_count >= 3 and primitive_count >= 2


def _outline_none_without_focus_matches(files: list[SourceFile]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    replacement_pattern = re.compile(r"\b(?:focus:|focus-visible:|focus-within:|ring-|focus:ring|focus-visible:ring)\b")
    for file in files:
        snippets: list[str] = []
        for match in re.finditer(r"\boutline-none\b", file.content):
            start = max(0, match.start() - 120)
            end = min(len(file.content), match.end() + 160)
            snippet = file.content[start:end]
            if replacement_pattern.search(snippet):
                continue
            snippets.append(" ".join(snippet.split())[:180])
        if snippets:
            matches.append({"path": file.path, "matches": snippets[:6]})
    return matches


def _uses_tailwind_classes(text: str) -> bool:
    return any(token in text for token in ("className=", "rounded-", "bg-", "text-", "from-", "to-", "font-sans"))


def _has_custom_tailwind_colors(tailwind_files: list[SourceFile]) -> bool:
    for file in tailwind_files:
        if re.search(r"\bcolors\s*:\s*\{[^}]+", file.content, flags=re.DOTALL):
            return True
    return False


def _uses_default_font_stack(code_files: list[SourceFile], tailwind_files: list[SourceFile]) -> bool:
    combined = "\n".join(file.content for file in [*code_files, *tailwind_files])
    has_default = bool(re.search(r"\bfont-sans\b|['\"](?:Inter|Geist|Geist Sans)['\"]", combined))
    has_custom_font_config = bool(
        re.search(r"\bfontFamily\s*:\s*\{[^}]+", "\n".join(file.content for file in tailwind_files), flags=re.DOTALL)
    )
    return has_default and not has_custom_font_config


def _set_source_scan_progress(total: int, scan_log: list[str]) -> None:
    _SOURCE_SCAN_PROGRESS["enabled"] = True
    _SOURCE_SCAN_PROGRESS["count"] = 0
    _SOURCE_SCAN_PROGRESS["total"] = total
    _SOURCE_SCAN_PROGRESS["log_fn"] = scan_log.append


def _clear_source_scan_progress() -> None:
    _SOURCE_SCAN_PROGRESS["enabled"] = False
    _SOURCE_SCAN_PROGRESS["count"] = 0
    _SOURCE_SCAN_PROGRESS["total"] = 0
    _SOURCE_SCAN_PROGRESS["log_fn"] = None


def _emit_source_scan_progress(message: str) -> None:
    if not _SOURCE_SCAN_PROGRESS.get("enabled"):
        return
    log_fn = _SOURCE_SCAN_PROGRESS.get("log_fn")
    if callable(log_fn):
        log_fn(message)
    terminal_log(message, True)
