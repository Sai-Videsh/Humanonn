from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from .models import AuditSnapshot, SignalDefinition, SignalFinding
from .signals import SIGNALS


Evaluator = Callable[[AuditSnapshot, SignalDefinition], SignalFinding]


def finding(
    signal: SignalDefinition,
    flagged: bool,
    confidence: float,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> SignalFinding:
    return SignalFinding(
        id=signal.id,
        name=signal.name,
        tier=signal.tier,
        bucket=signal.bucket,
        weight=signal.weight,
        flagged=flagged,
        confidence=confidence if flagged else 0.0,
        reason=reason,
        fix=signal.fix,
        evidence=evidence or {},
    )


def evaluate_rules(snapshot: AuditSnapshot) -> list[SignalFinding]:
    findings: list[SignalFinding] = []
    for signal in SIGNALS:
        evaluator = EVALUATORS.get(signal.id, ambiguous_placeholder)
        findings.append(evaluator(snapshot, signal))
    return findings


def ambiguous_placeholder(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    return finding(signal, False, 0.0, "Needs LLM or deeper product-specific inspection.")


def purple_accent(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    color_text = " ".join(map(str, snapshot.colors.get("all", []))).lower()
    flagged = any(token in color_text for token in ["rgb(139, 92, 246)", "rgb(124, 58, 237)", "violet", "purple"])
    return finding(signal, flagged, 0.7, "Violet/purple appears in prominent extracted colors." if flagged else "No dominant default purple accent detected.", {"colors": snapshot.colors.get("all", [])[:12]})


def pill_buttons(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    buttons = snapshot.buttons
    if not buttons:
        return finding(signal, False, 0.0, "No buttons found.")
    pill_count = sum(1 for b in buttons if _px(b.get("borderRadius")) >= 24)
    ratio = pill_count / len(buttons)
    flagged = len(buttons) >= 2 and ratio >= 0.6
    return finding(signal, flagged, min(1.0, ratio), f"{pill_count}/{len(buttons)} buttons have radius >= 24px." if flagged else "Button radii are not consistently pill-shaped.", {"pill_count": pill_count, "button_count": len(buttons)})


def mesh_gradient(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    bg = f"{snapshot.body.get('backgroundImage', '')} {snapshot.body.get('heroBackgroundImage', '')}".lower()
    radial_count = bg.count("radial-gradient")
    flagged = radial_count >= 2 or any(word in bg for word in ["aurora", "mesh"])
    return finding(signal, flagged, 0.8, f"Background contains {radial_count} radial gradients." if flagged else "No mesh/aurora gradient background detected.", {"radial_gradient_count": radial_count})


def glassmorphism(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    glass_sections = [s for s in snapshot.sections if "blur" in str(s.get("backdropFilter", "")).lower()]
    flagged = bool(glass_sections)
    return finding(signal, flagged, 0.8, f"{len(glass_sections)} section/card surfaces use backdrop blur." if flagged else "No backdrop-filter blur detected.", {"count": len(glass_sections)})


def dark_only(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    bg = snapshot.body.get("backgroundColor", "")
    is_dark = _relative_luminance(bg) < 0.18
    has_light_toggle = any("theme" in link.get("text", "").lower() or "light" in link.get("text", "").lower() for link in snapshot.links)
    flagged = is_dark and not has_light_toggle
    return finding(signal, flagged, 0.65, "Body background is dark and no obvious light/theme toggle was found." if flagged else "Dark-only pattern not confirmed.", {"backgroundColor": bg})


def gradient_text(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    items = [h for h in snapshot.headings if "text" in str(h.get("backgroundClip", "")).lower() or h.get("textFillColor") == "rgba(0, 0, 0, 0)"]
    return finding(signal, bool(items), 0.8, "Heading text uses background-clip/text-fill gradient treatment." if items else "No gradient heading text detected.", {"count": len(items)})


def beta_badge(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    text = snapshot.text.get("visible", "").lower()
    flagged = any(phrase in text for phrase in ["now in beta", "beta", "early access"])
    return finding(signal, flagged, 0.6, "Launch/beta badge copy appears in visible text." if flagged else "No beta badge copy detected.")


def dot_grid_overlay(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    bg = f"{snapshot.body.get('backgroundImage', '')} {' '.join(str(s.get('backgroundImage', '')) for s in snapshot.sections)}".lower()
    flagged = any(token in bg for token in ["dot", "radial-gradient"]) and "radial-gradient" in bg
    return finding(signal, flagged, 0.55, "Radial background patterns may indicate dot/grid overlay." if flagged else "No dot/grid overlay detected.")


def cta_glow(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    glowing = [b for b in snapshot.buttons if _shadow_has_glow(str(b.get("boxShadow", "")))]
    return finding(signal, bool(glowing), 0.75, f"{len(glowing)} buttons use large colored/glow shadows." if glowing else "No CTA glow shadows detected.", {"count": len(glowing)})


def inter_only(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    fonts = [font.lower() for font in snapshot.fonts]
    unique = {font for font in fonts if font and font not in {"sans-serif", "system-ui", "arial"}}
    flagged = len(unique) == 1 and next(iter(unique), "") in {"inter", "geist", "geist sans"}
    return finding(signal, flagged, 0.8, f"Only detected custom font is {next(iter(unique), 'none')}." if flagged else "Typography is not Inter/Geist-only.", {"fonts": snapshot.fonts})


def no_card_hover(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    cards = [s for s in snapshot.sections if s.get("looksCard")]
    no_feedback = [c for c in cards if not c.get("hasHoverEffect")]
    no_transition = [c for c in cards if c.get("transitionDuration") in {"0s", "0ms"}]
    flagged = len(cards) >= 3 and len(no_feedback) / len(cards) > 0.7
    reason = "Most card-like surfaces showed no hover-state change during probing." if flagged else "Card hover absence not confirmed."
    return finding(
        signal,
        flagged,
        0.7 if flagged else 0.0,
        reason,
        {
            "cards": len(cards),
            "without_hover_feedback": len(no_feedback),
            "without_transition": len(no_transition),
        },
    )


def generic_ctas(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    generic = {"get started", "learn more", "start now", "try for free", "sign up", "book a demo"}
    texts = [b.get("text", "").strip().lower() for b in snapshot.buttons + snapshot.links]
    hits = [text for text in texts if text in generic]
    flagged = len(set(hits)) >= 2 or len(hits) >= 3
    return finding(signal, flagged, 0.75, f"Generic CTA labels found: {', '.join(sorted(set(hits)))}." if flagged else "CTA copy is not strongly generic.", {"matches": hits})


def text_logo(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    title = snapshot.title.lower().strip()
    nav_texts = [link.get("text", "").lower().strip() for link in snapshot.links[:5]]
    flagged = bool(title) and any(title in text or text in title for text in nav_texts if len(text) > 2)
    return finding(signal, flagged, 0.45, "Page title appears as plain nav text; logo may be text-only." if flagged else "Text-only logo not confirmed.")


def all_centered_headings(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    headings = snapshot.headings
    if len(headings) < 3:
        return finding(signal, False, 0.0, "Not enough headings to judge alignment rhythm.")
    centered = sum(1 for h in headings if h.get("textAlign") == "center")
    flagged = centered / len(headings) >= 0.8
    return finding(signal, flagged, 0.7, f"{centered}/{len(headings)} headings are centered." if flagged else "Heading alignment has variation.", {"centered": centered, "heading_count": len(headings)})


def spacing_rhythm_off(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    spacings = [int(round(_px(s.get("paddingTop")))) for s in snapshot.sections if _px(s.get("paddingTop")) > 0]
    off_grid = [value for value in spacings if value % 8 not in {0, 1, 7}]
    flagged = len(spacings) >= 4 and len(off_grid) / len(spacings) >= 0.4
    return finding(signal, flagged, 0.6, "Several section spacings fall outside an 8px rhythm." if flagged else "Spacing rhythm does not look obviously broken.", {"spacings": spacings[:12], "off_grid": off_grid[:12]})


def too_many_dividers(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    bordered = [s for s in snapshot.sections if _has_visible_border(s)]
    flagged = len(snapshot.sections) >= 4 and len(bordered) / len(snapshot.sections) >= 0.7
    return finding(signal, flagged, 0.55, "Most sections appear separated by borders." if flagged else "Divider overuse not detected.", {"bordered_sections": len(bordered), "section_count": len(snapshot.sections)})


def no_pointer_cards(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    clickable_cards = [s for s in snapshot.sections if s.get("looksCard") and s.get("hasLink")]
    missing = [c for c in clickable_cards if c.get("cursor") != "pointer"]
    flagged = bool(clickable_cards) and len(missing) / len(clickable_cards) >= 0.5
    return finding(signal, flagged, 0.65, "Clickable card-like elements lack cursor:pointer." if flagged else "Clickable card cursor issue not detected.", {"clickable_cards": len(clickable_cards), "missing_pointer": len(missing)})


def missing_focus_states(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    inputs = snapshot.inputs
    if not inputs:
        return finding(signal, False, 0.0, "No inputs found.")
    missing = [i for i in inputs if not i.get("hasVisibleFocus")]
    flagged = len(missing) / len(inputs) >= 0.5
    return finding(signal, flagged, 0.85, f"{len(missing)}/{len(inputs)} inputs lack visible focus indicators." if flagged else "Input focus states look present.", {"inputs": len(inputs), "missing": len(missing)})


def placeholder_only_labels(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    inputs = snapshot.inputs
    placeholder_only = [i for i in inputs if i.get("placeholder") and not i.get("label")]
    flagged = len(inputs) > 0 and len(placeholder_only) / len(inputs) >= 0.6
    return finding(signal, flagged, 0.7, "Most inputs rely on placeholders without persistent labels." if flagged else "Placeholder-only labels not detected.", {"placeholder_only": len(placeholder_only), "inputs": len(inputs)})


def missing_active_state(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    buttons = snapshot.buttons
    if not buttons:
        return finding(signal, False, 0.0, "No buttons found.")
    probed = [b for b in buttons if b.get("componentId")]
    if probed:
        missing = [b for b in probed if not b.get("hasActiveProbeEffect")]
        total = len(probed)
        flagged = total > 0 and len(missing) / total >= 0.7
        return finding(
            signal,
            flagged,
            0.85 if flagged else 0.0,
            f"{len(missing)}/{total} probed buttons showed no pressed-state change." if flagged else "Button pressed-state feedback appears present.",
            {"buttons": total, "missing": len(missing), "probe_based": True},
        )
    missing = [b for b in buttons if not b.get("hasActiveFeedback")]
    flagged = len(missing) / len(buttons) >= 0.7
    return finding(signal, flagged, 0.75, f"{len(missing)}/{len(buttons)} buttons show no active-state feedback." if flagged else "Button active feedback appears present.", {"buttons": len(buttons), "missing": len(missing)})


def tight_body_line_height(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    line_height = _line_height_ratio(snapshot.body.get("lineHeight"), snapshot.body.get("fontSize"))
    flagged = 0 < line_height < 1.5
    return finding(signal, flagged, 0.75, f"Body line-height ratio is {line_height:.2f}." if flagged else "Body line-height is not too tight.", {"line_height_ratio": line_height})


def uppercase_no_tracking(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    labels = [h for h in snapshot.headings if h.get("text", "").isupper() and len(h.get("text", "")) >= 4]
    cramped = [h for h in labels if abs(_px(h.get("letterSpacing"))) < 0.2]
    flagged = bool(labels) and len(cramped) / len(labels) >= 0.5
    return finding(signal, flagged, 0.75, "Uppercase labels use little or no letter-spacing." if flagged else "Uppercase tracking issue not detected.", {"uppercase_labels": len(labels), "cramped": len(cramped)})


def bad_reading_width(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    widths = [s.get("paragraphMaxWidth", 0) for s in snapshot.sections if s.get("paragraphMaxWidth")]
    bad = [w for w in widths if w > 820 or w < 320]
    flagged = bool(widths) and len(bad) / len(widths) >= 0.5
    return finding(signal, flagged, 0.6, "Paragraph widths look uncomfortable for reading." if flagged else "Reading widths are not obviously bad.", {"widths": widths[:12]})


def missing_transitions(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    elements = snapshot.buttons + snapshot.links
    if not elements:
        return finding(signal, False, 0.0, "No interactive elements found.")
    missing = [e for e in elements if e.get("transitionDuration") in {"0s", "0ms"}]
    flagged = len(missing) / len(elements) >= 0.75
    return finding(signal, flagged, 0.65, "Most interactive elements have no transitions." if flagged else "Interactive transitions are present.", {"interactive": len(elements), "missing": len(missing)})


def no_nav_active(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    nav_links = [l for l in snapshot.links if l.get("inNav")]
    active = [l for l in nav_links if l.get("ariaCurrent") or "active" in str(l.get("className", "")).lower()]
    flagged = len(nav_links) >= 3 and not active
    return finding(signal, flagged, 0.55, "Navigation links have no obvious active/current state." if flagged else "Nav active-state issue not confirmed.", {"nav_links": len(nav_links), "active": len(active)})


def linear_animations(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    timing = " ".join(str(x.get("transitionTimingFunction", "")) for x in snapshot.buttons + snapshot.links + snapshot.sections)
    flagged = "linear" in timing
    return finding(signal, flagged, 0.65, "Linear timing functions found in transitions." if flagged else "No linear animation timing detected.")


def native_title_tooltips(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    count = snapshot.raw.get("titleAttributeCount", 0)
    return finding(signal, count > 0, 0.65, f"{count} elements use title attributes." if count else "No native title tooltips detected.", {"count": count})


def no_fluid_type(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    raw_sizes = " ".join(str(h.get("fontSizeRaw", "")) for h in snapshot.headings)
    flagged = len(snapshot.headings) >= 2 and "clamp(" not in raw_sizes and "vw" not in raw_sizes
    return finding(signal, flagged, 0.4, "No fluid heading type tokens detected in computed snapshot." if flagged else "Fluid type hints detected.")


def weak_disabled_states(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    disabled = [b for b in snapshot.buttons if b.get("disabled")]
    weak = [b for b in disabled if b.get("opacity") in {"0.5", "0.50"}]
    flagged = bool(disabled) and len(weak) / len(disabled) >= 0.5
    return finding(signal, flagged, 0.55, "Disabled buttons appear to rely mostly on opacity." if flagged else "Weak disabled-state pattern not detected.", {"disabled": len(disabled), "weak": len(weak)})


def poor_alt_text(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    images = snapshot.images
    if not images:
        return finding(signal, False, 0.0, "No images found.")
    poor = [img for img in images if not img.get("alt") or str(img.get("alt")).lower() in {"image", "photo", "picture"}]
    flagged = len(poor) / len(images) >= 0.4
    return finding(signal, flagged, 0.75, f"{len(poor)}/{len(images)} images have missing or weak alt text." if flagged else "Image alt text does not look broadly weak.", {"images": len(images), "poor": len(poor)})


def link_underline_static(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    links = snapshot.links
    if len(links) < 3:
        return finding(signal, False, 0.0, "Not enough links to judge underline behavior.")
    underlined = sum(1 for l in links if l.get("textDecorationLine") == "underline")
    ratio = underlined / len(links)
    flagged = ratio in {0.0, 1.0}
    return finding(signal, flagged, 0.45, "Links appear uniformly underlined or uniformly plain." if flagged else "Link underline treatment has variation.", {"underlined": underlined, "links": len(links)})


def unconstrained_paragraphs(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    widths = [s.get("paragraphMaxWidth", 0) for s in snapshot.sections if s.get("paragraphMaxWidth")]
    long = [w for w in widths if w > 900]
    flagged = bool(long)
    return finding(signal, flagged, 0.7, "Some paragraph lines exceed comfortable reading width." if flagged else "Paragraphs appear constrained.", {"long_widths": long[:12]})


def no_favicon(snapshot: AuditSnapshot, signal: SignalDefinition) -> SignalFinding:
    has_favicon = bool(snapshot.raw.get("hasFavicon"))
    return finding(signal, not has_favicon, 0.9, "No favicon link was found." if not has_favicon else "Favicon present.")


def _px(value: Any) -> float:
    if value is None:
        return 0.0
    match = re.search(r"-?\d+(\.\d+)?", str(value))
    return float(match.group(0)) if match else 0.0


def _line_height_ratio(line_height: Any, font_size: Any) -> float:
    lh = _px(line_height)
    fs = _px(font_size)
    return lh / fs if fs else 0.0


def _shadow_has_glow(value: str) -> bool:
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", value)]
    return len(nums) >= 4 and max(nums[:4]) >= 24


def _relative_luminance(rgb: str) -> float:
    nums = [int(n) for n in re.findall(r"\d+", rgb)[:3]]
    if len(nums) < 3:
        return 1.0
    channels = []
    for n in nums:
        c = n / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _has_visible_border(section: dict[str, Any]) -> bool:
    values = [section.get("borderTopWidth"), section.get("borderBottomWidth")]
    return any(_px(value) > 0 for value in values)


EVALUATORS: dict[str, Evaluator] = {
    "purple_accent": purple_accent,
    "pill_buttons": pill_buttons,
    "mesh_gradient": mesh_gradient,
    "glassmorphism": glassmorphism,
    "dark_only": dark_only,
    "gradient_text": gradient_text,
    "beta_badge": beta_badge,
    "dot_grid_overlay": dot_grid_overlay,
    "cta_glow": cta_glow,
    "inter_only": inter_only,
    "no_card_hover": no_card_hover,
    "generic_ctas": generic_ctas,
    "text_logo": text_logo,
    "all_centered_headings": all_centered_headings,
    "spacing_rhythm_off": spacing_rhythm_off,
    "too_many_dividers": too_many_dividers,
    "no_pointer_cards": no_pointer_cards,
    "missing_focus_states": missing_focus_states,
    "placeholder_only_labels": placeholder_only_labels,
    "missing_active_state": missing_active_state,
    "tight_body_line_height": tight_body_line_height,
    "uppercase_no_tracking": uppercase_no_tracking,
    "bad_reading_width": bad_reading_width,
    "missing_transitions": missing_transitions,
    "no_nav_active": no_nav_active,
    "linear_animations": linear_animations,
    "native_title_tooltips": native_title_tooltips,
    "no_fluid_type": no_fluid_type,
    "weak_disabled_states": weak_disabled_states,
    "poor_alt_text": poor_alt_text,
    "link_underline_static": link_underline_static,
    "unconstrained_paragraphs": unconstrained_paragraphs,
    "no_favicon": no_favicon,
}
