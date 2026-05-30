from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from humanonn.models import AuditReport, ScoreSummary, SignalBucket, SignalFinding, SignalTier
from humanonn.scoring import clamp, get_category, score_findings, MAX_RAW_SCORE


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
    scan_log = [f"Starting source-code scan for {owner}/{repo} on branch {default_branch}."]
    tree = _fetch_json(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1")
    entries = tree.get("tree", [])
    paths = [
        item["path"]
        for item in entries
        if item.get("type") == "blob" and _should_fetch_path(item.get("path", ""))
    ]
    files = [
        SourceFile(path=path, content=_fetch_text(f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{path}"))
        for path in paths
    ]
    scan_log.append(f"Fetched {len(files)} source files for scanning.")
    findings = _evaluate_source_rules(files)
    # source_raw_score is weighted by confidence
    source_score = sum(finding.points * float(finding.confidence) for finding in findings if finding.flagged)
    # do not cap source_score here; normalization uses SOURCE_SCORE_CAP
    for finding in findings:
        state = "FLAGGED" if finding.flagged else "clear"
        scan_log.append(f"[{state}] {finding.id} - {finding.reason}")
    scan_log.append(f"Computed raw source code score {round(source_score,2)}/{SOURCE_SCORE_CAP}.")
    return {
        "repo_url": repo_url,
        "owner": owner,
        "repo": repo,
        "branch": default_branch,
        "files_scanned": len(files),
        "bytes_scanned": sum(len(file.content.encode("utf-8", errors="ignore")) for file in files),
        "source_code_score": source_score,
        "normalized_source_code_score": _normalize_source_score(source_score),
        "score_cap": SOURCE_SCORE_CAP,
        "tier_counts": _source_tier_counts(findings),
        "findings": [asdict(finding) for finding in findings],
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


def _should_fetch_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if _is_tailwind_config(normalized) or _is_next_config(normalized):
        return True
    return (
        normalized.startswith(("src/", "app/", "components/", "styles/"))
        or normalized.endswith((".css", ".tsx", ".ts", ".jsx", ".js", ".mjs", ".html", ".htm", ".mdx", ".vue", ".svelte", ".astro"))
    )


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
    return SourceFinding(
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
