from __future__ import annotations

from .models import SignalBucket, SignalDefinition


TIER_BASE_POINTS: dict[int, float] = {1: 10.0, 2: 6.0, 3: 3.0, 4: 2.0}
BUCKET_MULTIPLIERS: dict[SignalBucket, float] = {"origin": 1.5, "polish": 1.0}


def signal_weight(tier: int, bucket: SignalBucket) -> float:
    return TIER_BASE_POINTS[tier] * BUCKET_MULTIPLIERS[bucket]


def signal(
    signal_id: str,
    tier: int,
    name: str,
    kind: str,
    bucket: SignalBucket,
    fix: str,
) -> SignalDefinition:
    return SignalDefinition(signal_id, tier, name, kind, bucket, signal_weight(tier, bucket), fix)


SIGNALS: list[SignalDefinition] = [
    signal("purple_accent", 1, "Purple/violet default accent color", "deterministic", "origin", "Use a brand-specific accent palette instead of default violet."),
    signal("pill_buttons", 1, "Pill-shaped buttons everywhere", "deterministic", "origin", "Reduce button radius to a deliberate component radius."),
    signal("mesh_gradient", 1, "Mesh gradient / aurora hero background", "deterministic", "origin", "Replace generic aurora treatment with brand-specific art direction."),
    signal("glassmorphism", 1, "Glassmorphism cards", "deterministic", "origin", "Use solid surfaces or apply glass sparingly with clear hierarchy."),
    signal("dark_only", 1, "Dark mode default with no light variant", "deterministic", "origin", "Provide an intentional light variant or make dark mode feel fully resolved."),

    signal("gradient_text", 2, "Gradient hero headline text", "deterministic", "origin", "Use plain display type or a more restrained brand treatment."),
    signal("canvas_webgl_hero_background", 2, "Hero background is canvas-rendered or WebGL instead of CSS", "deterministic", "origin", "Use a CSS background or intentionally document the canvas/WebGL treatment."),
    signal("canvas_rendered_ui", 2, "Interactive UI is rendered within a canvas layer", "deterministic", "origin", "Render interactive UI with DOM elements instead of placing it inside a canvas layer."),
    signal("bento_grid", 2, "Bento grid feature section", "ambiguous", "origin", "Break the grid rhythm with content-led layout variation."),
    signal("beta_badge", 2, "Floating beta badge above hero", "deterministic", "origin", "Use context-specific launch/status copy or remove the badge."),
    signal("dot_grid_overlay", 2, "Dot/grid pattern overlay", "deterministic", "origin", "Replace decorative overlays with purposeful imagery or whitespace."),
    signal("cta_glow", 2, "Glow/halo CTA shadow", "deterministic", "origin", "Use normal elevation and hover states instead of default glow."),
    signal("repeated_section_rhythm", 2, "Repeated section rhythm", "ambiguous", "origin", "Vary section composition based on content priority."),
    signal("inter_only", 2, "Inter or Geist as the only font", "deterministic", "origin", "Add a deliberate display/body pairing or customize typography."),
    signal("no_responsive_breakpoints", 2, "Layout does not meaningfully change between 375px and 1440px viewport", "deterministic", "origin", "Ensure responsive breakpoints adapt layout across viewports."),
    signal("wide_color_palette", 2, "Site uses 8+ distinct computed colors with no token system", "ambiguous", "origin", "Consolidate on a token palette and reduce redundant computed colors."),
    signal("missing_meta_og", 2, "No meta description, OG title, or OG image", "deterministic", "origin", "Add meta description and Open Graph metadata."),
    signal("font_weight_only_400_700", 2, "Only font-weight 400 and 700 used", "deterministic", "origin", "Introduce intermediate font weights for typographic hierarchy."),
    signal("heading_size_non_proportional", 2, "Heading sizes not on a modular scale", "deterministic", "origin", "Adjust headings to a consistent modular scale."),

    signal("no_card_hover", 3, "No hover states on feature cards", "deterministic", "origin", "Add subtle hover feedback to interactive cards."),
    signal("uniform_icons", 3, "Icons all same weight, size, and color", "ambiguous", "origin", "Vary icon treatment where hierarchy requires it."),
    signal("generic_ctas", 3, "Generic CTA copy", "deterministic", "origin", "Replace generic CTAs with outcome-specific action text."),
    signal("no_scroll_animations", 3, "No scroll-triggered entrance animations", "ambiguous", "polish", "Add purposeful motion only where it clarifies hierarchy."),
    signal("text_logo", 3, "Logo is just bold product name", "deterministic", "origin", "Add a distinctive mark, wordmark treatment, or custom lockup."),
    signal("all_centered_headings", 3, "Every heading is centered", "deterministic", "origin", "Mix left-aligned and centered sections based on content."),
    signal("spacing_rhythm_off", 3, "Inconsistent spacing rhythm", "deterministic", "origin", "Normalize spacing to an 8px rhythm or chosen spacing scale."),
    signal("no_loading_states", 3, "No loading/skeleton states", "ambiguous", "polish", "Design loading states for async areas."),
    signal("too_many_dividers", 3, "Dividers between every section", "deterministic", "origin", "Use whitespace and background shifts instead of constant borders."),
    signal("no_pointer_cards", 3, "No cursor pointer on clickable cards", "deterministic", "polish", "Add cursor and focus/hover states to clickable cards."),
    signal("muted_social_proof", 3, "Grayscale low-opacity social proof logos", "ambiguous", "origin", "Tune logo treatment so proof still feels credible."),
    signal("generic_footer", 3, "Footer has four identical link columns", "ambiguous", "origin", "Shape footer navigation around real product priorities."),
    signal("dynamic_injected_styles", 3, "Inline style values appear dynamically injected at runtime", "deterministic", "origin", "Move repeated gradients, blur, or opacity decisions into stable CSS or document the runtime injection."),
    signal("no_shadow_system", 3, "No layered elevation system (all flat or one glow)", "ambiguous", "origin", "Introduce layered elevation and subtle shadows for hierarchy."),
    signal("card_padding_inconsistent", 3, "Internal card padding varies across similar cards", "deterministic", "polish", "Standardize card padding via tokens."),
    signal("no_semantic_html", 3, "Interactive elements use div/span instead of semantic tags", "deterministic", "origin", "Use semantic elements like button/nav/main/section."),
    signal("z_index_magic_numbers", 3, "Computed stacking contexts use arbitrary magic numbers", "deterministic", "origin", "Refactor stacking contexts to use a scale or tokens."),
    signal("no_reduced_motion", 3, "Animations without prefers-reduced-motion handling", "deterministic", "polish", "Respect prefers-reduced-motion for nonessential animations."),
    signal("image_no_aspect_ratio", 3, "Images have no aspect-ratio set", "deterministic", "polish", "Set aspect-ratio or intrinsic size to avoid layout shift."),
    signal("stock_image_pattern", 3, "Images match Unsplash/stock patterns", "ambiguous", "polish", "Prefer original product/team/customer imagery."),
    signal("color_contrast_borderline", 3, "Contrast passes but is borderline and feels wrong", "ambiguous", "polish", "Increase contrast and tune surface hierarchy."),

    signal("missing_focus_states", 4, "No focus states on inputs", "deterministic", "polish", "Add visible focus rings with accessible contrast."),
    signal("placeholder_only_labels", 4, "Placeholder disappears on focus", "deterministic", "polish", "Use persistent labels or floating labels."),
    signal("missing_active_state", 4, "Button has no active/pressed state", "deterministic", "polish", "Add :active scale, brightness, or pressed-state feedback."),
    signal("tight_body_line_height", 4, "Body copy line-height too tight", "deterministic", "polish", "Set body copy line-height around 1.6 to 1.75."),
    signal("uppercase_no_tracking", 4, "Uppercase labels have zero letter spacing", "deterministic", "polish", "Add letter-spacing to uppercase labels."),
    signal("bad_reading_width", 4, "Reading width too wide or narrow", "deterministic", "polish", "Constrain paragraph measure to comfortable line lengths."),
    signal("missing_transitions", 4, "No transition on theme/state changes", "deterministic", "polish", "Add transitions to interactive state changes."),
    signal("no_nav_active", 4, "Nav has no active/current indicator", "deterministic", "polish", "Add active state for the current route or section."),
    signal("linear_animations", 4, "Animations are linear", "deterministic", "polish", "Use a custom cubic-bezier easing curve."),
    signal("default_scrollbar", 4, "Scrollbar is browser default", "ambiguous", "polish", "Style scrollbars when they are visible in the product surface."),
    signal("default_selection", 4, "Selection color is browser default blue", "ambiguous", "polish", "Add branded ::selection colors."),
    signal("no_empty_states", 4, "No empty state design", "ambiguous", "polish", "Design empty states for data-driven components."),
    signal("native_title_tooltips", 4, "Tooltips use native title attribute", "deterministic", "polish", "Replace title-only tooltips with accessible custom tooltips."),
    signal("no_fluid_type", 4, "Font size does not scale on mobile", "deterministic", "polish", "Use responsive type tokens or clamp()."),
    signal("weak_disabled_states", 4, "Disabled states are just opacity", "deterministic", "polish", "Define disabled color, cursor, and interaction behavior."),
    signal("poor_alt_text", 4, "Images have no meaningful alt text", "deterministic", "polish", "Write useful alt text or mark decorative images empty."),
    signal("link_underline_static", 4, "Link underlines always on or off", "deterministic", "polish", "Add a link hover/focus underline transition."),
    signal("no_custom_404", 4, "No custom 404 page", "ambiguous", "polish", "Add a branded 404 route."),
    signal("unconstrained_paragraphs", 4, "Paragraph lines too long", "deterministic", "polish", "Constrain paragraph max-width."),
    signal("no_favicon", 4, "No favicon", "deterministic", "polish", "Add a real favicon and app icons."),
    signal("modal_focus_not_trapped", 4, "Modals do not trap focus", "ambiguous", "polish", "Use a modal primitive that traps focus."),
    signal("no_page_transition", 4, "No page transition between routes", "ambiguous", "polish", "Add route transitions only where they improve continuity."),
    signal("autofill_dark_breaks", 4, "Input autofill breaks dark mode", "ambiguous", "polish", "Override webkit autofill colors for dark inputs."),
    signal("no_word_break", 4, "Long words and URLs overflow on narrow viewports", "deterministic", "polish", "Add word-break/overflow-wrap rules."),
    signal("border_color_hardcoded", 4, "Border colors hardcoded hex values", "deterministic", "polish", "Use CSS variables or opacity tokens for borders."),
    signal("body_bg_not_extended", 4, "Background color stops at content end", "deterministic", "polish", "Extend background to full viewport height."),
    signal("no_print_styles", 4, "No @media print styles", "deterministic", "polish", "Add print-specific style rules."),
    signal("missing_lang_attribute", 4, "HTML missing lang attribute", "deterministic", "polish", "Add a lang attribute to the html element."),
    signal("no_skip_link", 4, "No skip-to-content link for keyboard users", "deterministic", "polish", "Add an accessible skip-link."),
    signal("icon_only_buttons_no_label", 4, "Icon-only buttons missing aria-label", "deterministic", "polish", "Provide aria-label or visible text for icon buttons."),
    signal("input_no_autocomplete", 4, "Form inputs missing autocomplete attributes", "deterministic", "polish", "Add autocomplete attributes."),
    signal("missing_error_states", 4, "Form fields show no error state", "deterministic", "polish", "Add visual and textual error states."),
    signal("no_success_feedback", 4, "Form submission shows no success confirmation", "deterministic", "polish", "Show success messages or confirmations."),
    signal("animation_no_will_change", 4, "Animated elements missing will-change hint", "deterministic", "polish", "Add will-change where appropriate to hint the compositor."),
    signal("tap_target_too_small", 4, "Interactive elements under recommended touch size", "deterministic", "polish", "Increase tap targets to 44×44px."),
    signal("overflow_hidden_clipping", 4, "overflow:hidden clips content unexpectedly on mobile", "deterministic", "polish", "Audit and remove destructive overflow rules."),
    signal("hardcoded_px_spacing", 4, "Hardcoded px spacing everywhere", "deterministic", "polish", "Use rem/spacing tokens instead of px."),
    signal("no_focus_visible_distinction", 4, "No distinction between :focus and :focus-visible", "deterministic", "polish", "Use :focus-visible to avoid mouse-triggered focus rings."),
    signal("image_lazy_load_missing", 4, "Below-fold images missing loading=\"lazy\"", "deterministic", "polish", "Add lazy loading to below-the-fold images."),
    signal("no_viewport_meta", 4, "Missing or incomplete viewport meta tag", "deterministic", "polish", "Add a responsive viewport meta tag."),
    signal("color_scheme_meta_missing", 4, "No color-scheme meta tag", "deterministic", "polish", "Add color-scheme meta to hint light/dark UI."),
    signal("heading_hierarchy_skipped", 4, "Heading levels skip (e.g. h1 → h3)", "deterministic", "polish", "Fix the document outline and heading hierarchy."),
]

SIGNAL_BY_ID = {signal.id: signal for signal in SIGNALS}
