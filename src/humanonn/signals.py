from __future__ import annotations

from .models import SignalDefinition


def tier_weight(tier: int) -> float:
    return {1: 4.0, 2: 6.0, 3: 6.0, 4: 8.0}[tier]


SIGNALS: list[SignalDefinition] = [
    SignalDefinition("purple_accent", 1, "Purple/violet default accent color", "deterministic", tier_weight(1), "Use a brand-specific accent palette instead of default violet."),
    SignalDefinition("pill_buttons", 1, "Pill-shaped buttons everywhere", "deterministic", tier_weight(1), "Reduce button radius to a deliberate component radius."),
    SignalDefinition("mesh_gradient", 1, "Mesh gradient / aurora hero background", "deterministic", tier_weight(1), "Replace generic aurora treatment with brand-specific art direction."),
    SignalDefinition("glassmorphism", 1, "Glassmorphism cards", "deterministic", tier_weight(1), "Use solid surfaces or apply glass sparingly with clear hierarchy."),
    SignalDefinition("dark_only", 1, "Dark mode default with no light variant", "deterministic", tier_weight(1), "Provide an intentional light variant or make dark mode feel fully resolved."),
    SignalDefinition("gradient_text", 2, "Gradient hero headline text", "deterministic", tier_weight(2), "Use plain display type or a more restrained brand treatment."),
    SignalDefinition("bento_grid", 2, "Bento grid feature section", "ambiguous", tier_weight(2), "Break the grid rhythm with content-led layout variation."),
    SignalDefinition("beta_badge", 2, "Floating beta badge above hero", "deterministic", tier_weight(2), "Use context-specific launch/status copy or remove the badge."),
    SignalDefinition("dot_grid_overlay", 2, "Dot/grid pattern overlay", "deterministic", tier_weight(2), "Replace decorative overlays with purposeful imagery or whitespace."),
    SignalDefinition("cta_glow", 2, "Glow/halo CTA shadow", "deterministic", tier_weight(2), "Use normal elevation and hover states instead of default glow."),
    SignalDefinition("repeated_section_rhythm", 2, "Repeated section rhythm", "ambiguous", tier_weight(2), "Vary section composition based on content priority."),
    SignalDefinition("inter_only", 2, "Inter or Geist as the only font", "deterministic", tier_weight(2), "Add a deliberate display/body pairing or customize typography."),
    SignalDefinition("tier2_spare", 2, "Reserved Tier 2 signal slot", "ambiguous", tier_weight(2), "Review manually once this signal is defined."),
    SignalDefinition("no_card_hover", 3, "No hover states on feature cards", "deterministic", tier_weight(3), "Add subtle hover feedback to interactive cards."),
    SignalDefinition("uniform_icons", 3, "Icons all same weight, size, and color", "ambiguous", tier_weight(3), "Vary icon treatment where hierarchy requires it."),
    SignalDefinition("generic_ctas", 3, "Generic CTA copy", "deterministic", tier_weight(3), "Replace generic CTAs with outcome-specific action text."),
    SignalDefinition("no_scroll_animations", 3, "No scroll-triggered entrance animations", "ambiguous", tier_weight(3), "Add purposeful motion only where it clarifies hierarchy."),
    SignalDefinition("text_logo", 3, "Logo is just bold product name", "deterministic", tier_weight(3), "Add a distinctive mark, wordmark treatment, or custom lockup."),
    SignalDefinition("all_centered_headings", 3, "Every heading is centered", "deterministic", tier_weight(3), "Mix left-aligned and centered sections based on content."),
    SignalDefinition("spacing_rhythm_off", 3, "Inconsistent spacing rhythm", "deterministic", tier_weight(3), "Normalize spacing to an 8px rhythm or chosen spacing scale."),
    SignalDefinition("no_loading_states", 3, "No loading/skeleton states", "ambiguous", tier_weight(3), "Design loading states for async areas."),
    SignalDefinition("too_many_dividers", 3, "Dividers between every section", "deterministic", tier_weight(3), "Use whitespace and background shifts instead of constant borders."),
    SignalDefinition("no_pointer_cards", 3, "No cursor pointer on clickable cards", "deterministic", tier_weight(3), "Add cursor and focus/hover states to clickable cards."),
    SignalDefinition("muted_social_proof", 3, "Grayscale low-opacity social proof logos", "ambiguous", tier_weight(3), "Tune logo treatment so proof still feels credible."),
    SignalDefinition("generic_footer", 3, "Footer has four identical link columns", "ambiguous", tier_weight(3), "Shape footer navigation around real product priorities."),
    SignalDefinition("missing_focus_states", 4, "No focus states on inputs", "deterministic", tier_weight(4), "Add visible focus rings with accessible contrast."),
    SignalDefinition("placeholder_only_labels", 4, "Placeholder disappears on focus", "deterministic", tier_weight(4), "Use persistent labels or floating labels."),
    SignalDefinition("missing_active_state", 4, "Button has no active/pressed state", "deterministic", tier_weight(4), "Add :active scale, brightness, or pressed-state feedback."),
    SignalDefinition("stock_images", 4, "Images appear to be Unsplash stock photos", "ambiguous", tier_weight(4), "Use original product, team, or customer imagery."),
    SignalDefinition("tight_body_line_height", 4, "Body copy line-height too tight", "deterministic", tier_weight(4), "Set body copy line-height around 1.6 to 1.75."),
    SignalDefinition("uppercase_no_tracking", 4, "Uppercase labels have zero letter spacing", "deterministic", tier_weight(4), "Add letter-spacing to uppercase labels."),
    SignalDefinition("bad_reading_width", 4, "Reading width too wide or narrow", "deterministic", tier_weight(4), "Constrain paragraph measure to comfortable line lengths."),
    SignalDefinition("missing_transitions", 4, "No transition on theme/state changes", "deterministic", tier_weight(4), "Add transitions to interactive state changes."),
    SignalDefinition("weak_contrast_feel", 4, "Contrast passes but feels wrong", "ambiguous", tier_weight(4), "Tune perceived contrast and surface hierarchy."),
    SignalDefinition("no_nav_active", 4, "Nav has no active/current indicator", "deterministic", tier_weight(4), "Add active state for the current route or section."),
    SignalDefinition("linear_animations", 4, "Animations are linear", "deterministic", tier_weight(4), "Use a custom cubic-bezier easing curve."),
    SignalDefinition("default_scrollbar", 4, "Scrollbar is browser default", "ambiguous", tier_weight(4), "Style scrollbars when they are visible in the product surface."),
    SignalDefinition("default_selection", 4, "Selection color is browser default blue", "ambiguous", tier_weight(4), "Add branded ::selection colors."),
    SignalDefinition("no_empty_states", 4, "No empty state design", "ambiguous", tier_weight(4), "Design empty states for data-driven components."),
    SignalDefinition("native_title_tooltips", 4, "Tooltips use native title attribute", "deterministic", tier_weight(4), "Replace title-only tooltips with accessible custom tooltips."),
    SignalDefinition("no_fluid_type", 4, "Font size does not scale on mobile", "deterministic", tier_weight(4), "Use responsive type tokens or clamp()."),
    SignalDefinition("weak_disabled_states", 4, "Disabled states are just opacity", "deterministic", tier_weight(4), "Define disabled color, cursor, and interaction behavior."),
    SignalDefinition("poor_alt_text", 4, "Images have no meaningful alt text", "deterministic", tier_weight(4), "Write useful alt text or mark decorative images empty."),
    SignalDefinition("link_underline_static", 4, "Link underlines always on or off", "deterministic", tier_weight(4), "Add a link hover/focus underline transition."),
    SignalDefinition("no_custom_404", 4, "No custom 404 page", "ambiguous", tier_weight(4), "Add a branded 404 route."),
    SignalDefinition("unconstrained_paragraphs", 4, "Paragraph lines too long", "deterministic", tier_weight(4), "Constrain paragraph max-width."),
    SignalDefinition("no_favicon", 4, "No favicon", "deterministic", tier_weight(4), "Add a real favicon and app icons."),
    SignalDefinition("modal_focus_not_trapped", 4, "Modals do not trap focus", "ambiguous", tier_weight(4), "Use a modal primitive that traps focus."),
    SignalDefinition("no_page_transition", 4, "No page transition between routes", "ambiguous", tier_weight(4), "Add route transitions only where they improve continuity."),
    SignalDefinition("autofill_dark_breaks", 4, "Input autofill breaks dark mode", "ambiguous", tier_weight(4), "Override webkit autofill colors for dark inputs."),
]


SIGNAL_BY_ID = {signal.id: signal for signal in SIGNALS}

