# Humanonn — Complete Signal List

> Total signals: 91
> Format: `signal_id` | Brief | Bucket | Tier
> Bucket: **Origin** = proves AI generated it | **Polish** = any dev might miss it
> Tier weights: Tier 1 = 10pts | Tier 2 = 6pts | Tier 3 = 3pts | Tier 4 = 2pts
> Bucket multiplier: Origin = 1.5× | Polish = 1.0×

---

## Tier 1 — Loudest AI Fingerprints (Origin, 10pts × 1.5 = 15pts max each)

| Signal ID | Brief | Bucket | Tier |
|---|---|---|---|
| `purple_accent` | Purple/violet is the default accent color across the site | Origin | 1 |
| `pill_buttons` | All interactive buttons use border-radius ≥ 24px (pill shape) | Origin | 1 |
| `mesh_gradient` | Hero background uses radial/mesh aurora gradient in purple-blue-pink | Origin | 1 |
| `glassmorphism` | Cards use backdrop-filter blur with semi-transparent backgrounds | Origin | 1 |
| `dark_only` | Site ships dark mode as default with no light mode variant | Origin | 1 |

---

## Tier 2 — Strong Visual Fingerprints (Origin, 6pts × 1.5 = 9pts max each)

| Signal ID | Brief | Bucket | Tier |
|---|---|---|---|
| `gradient_text` | Hero headline uses background-clip:text gradient treatment | Origin | 2 |
| `bento_grid` | Feature section uses asymmetric bento grid card layout | Origin | 2 |
| `beta_badge` | Floating pill badge (e.g. "✦ Now in beta") sits above the h1 | Origin | 2 |
| `dot_grid_overlay` | Decorative dot or line grid pattern overlaid on hero section | Origin | 2 |
| `cta_glow` | Primary CTA button has a soft colored box-shadow glow/halo | Origin | 2 |
| `repeated_section_rhythm` | Every scroll section follows identical Hero→Features→Pricing→CTA template | Origin | 2 |
| `inter_only` | Inter or Geist is the only typeface — no display/body pairing | Origin | 2 |
| `no_responsive_breakpoints` | Layout does not meaningfully change between 375px and 1440px viewport | Origin | 2 |
| `wide_color_palette` | Site uses 8+ distinct computed colors with no token system — gradient-sampled sprawl | Origin | 2 |
| `missing_meta_og` | No meta description, OG title, or OG image present in document head | Origin | 2 |
| `font_weight_only_400_700` | Only font-weight 400 and 700 used — no 300/500/600 for hierarchy | Origin | 2 |
| `heading_size_non_proportional` | h1→h2→h3 size ratios are not on a modular scale (e.g. random jumps like 48→28→22px) | Origin | 2 |

---

## Tier 3 — Human-Touch Gaps (Mix of Origin and Polish, 3pts)

| Signal ID | Brief | Bucket | Tier |
|---|---|---|---|
| `no_card_hover` | Feature cards have no hover state — style identical on hover vs default | Origin | 3 |
| `uniform_icons` | All icons are same size, same stroke weight, same color with no hierarchy | Origin | 3 |
| `generic_ctas` | CTAs say "Get started" or "Learn more" — no outcome-specific action copy | Origin | 3 |
| `no_scroll_animations` | No scroll-triggered entrance animations on any section or element | Polish | 3 |
| `text_logo` | Logo is just the product name in bold Inter/sans — no logomark or custom treatment | Origin | 3 |
| `all_centered_headings` | Every heading across all sections is center-aligned — no left-aligned variation | Origin | 3 |
| `spacing_rhythm_off` | Padding and margin values mix 16/20/24/32px with no consistent 8px grid system | Origin | 3 |
| `no_loading_states` | No skeleton screens or loading indicators for async content areas | Polish | 3 |
| `too_many_dividers` | Full-width hr or border-bottom dividers used between every section | Origin | 3 |
| `no_pointer_cards` | Clickable cards don't change cursor to pointer on hover | Polish | 3 |
| `muted_social_proof` | "Trusted by" logos are all grayscale at ~40% opacity in a horizontal row | Origin | 3 |
| `generic_footer` | Footer is four identical columns of bullet links — Product/Company/Resources/Legal | Origin | 3 |
| `no_shadow_system` | Site uses either zero shadows or one dramatic glow — no layered elevation system | Origin | 3 |
| `card_padding_inconsistent` | Internal card padding varies (e.g. 16px vs 24px) across cards of the same type | Polish | 3 |
| `no_semantic_html` | Interactive elements use div or span instead of button/nav/main/section tags | Origin | 3 |
| `z_index_magic_numbers` | Computed stacking contexts use arbitrary magic numbers like 9999 or 99999 | Origin | 3 |
| `no_reduced_motion` | Transitions and animations exist with no prefers-reduced-motion media query | Polish | 3 |
| `image_no_aspect_ratio` | Images have no aspect-ratio set — causes layout shift on load | Polish | 3 |
| `stock_image_pattern` | Images match Unsplash/stock photo patterns — generic people-at-laptops or abstract shapes | Polish | 3 |
| `color_contrast_borderline` | Text contrast passes AA minimum but sits uncomfortably close to the threshold | Polish | 3 |

---

## Tier 4 — Deep Polish Gaps (Polish, 2pts each)

| Signal ID | Brief | Bucket | Tier |
|---|---|---|---|
| `missing_focus_states` | Input and interactive elements have no visible focus ring on keyboard focus | Polish | 4 |
| `placeholder_only_labels` | Form placeholders disappear on focus with no persistent or floating label replacement | Polish | 4 |
| `missing_active_state` | Buttons show no visual feedback on :active — no scale, brightness, or color change | Polish | 4 |
| `tight_body_line_height` | Body copy line-height is below 1.55 — text blocks feel dense and hard to skim | Polish | 4 |
| `uppercase_no_tracking` | Uppercase section labels (FEATURES, PRICING) have no letter-spacing applied | Polish | 4 |
| `bad_reading_width` | Body paragraphs exceed ~75 characters per line or are narrower than ~45 characters | Polish | 4 |
| `missing_transitions` | Interactive state changes (dropdowns, tabs, theme toggle) snap with no transition | Polish | 4 |
| `no_nav_active` | Navigation links have no active/current page indicator — all links look identical | Polish | 4 |
| `linear_animations` | CSS transitions use linear or bare ease timing — no custom cubic-bezier easing | Polish | 4 |
| `default_scrollbar` | Browser default scrollbar is visible with no custom styling on dark/branded surfaces | Polish | 4 |
| `default_selection` | Text selection color is browser-default blue — no ::selection override | Polish | 4 |
| `no_empty_states` | Data-driven components show blank space when empty — no empty state design | Polish | 4 |
| `native_title_tooltips` | Tooltips use the raw HTML title attribute — renders as native browser tooltip | Polish | 4 |
| `no_fluid_type` | Heading font sizes are fixed px values with no clamp() or fluid responsive scaling | Polish | 4 |
| `weak_disabled_states` | Disabled elements are only opacity:0.5 — no cursor:not-allowed or visual system | Polish | 4 |
| `poor_alt_text` | Images use empty alt="" or filename as alt text — no meaningful description | Polish | 4 |
| `link_underline_static` | Body links have underline always on or always off — no hover transition | Polish | 4 |
| `no_custom_404` | Missing route renders framework default error page — no branded 404 | Polish | 4 |
| `unconstrained_paragraphs` | Paragraph elements stretch to full container width — no max-width constraint | Polish | 4 |
| `no_favicon` | Browser tab shows framework default or blank favicon — no branded icon | Polish | 4 |
| `modal_focus_not_trapped` | Opening a modal allows Tab key to move focus to background elements | Polish | 4 |
| `no_page_transition` | Route changes are instant snaps — no fade, slide, or continuity animation | Polish | 4 |
| `autofill_dark_breaks` | Browser autofill applies yellow background that breaks dark mode surfaces | Polish | 4 |
| `no_word_break` | Long words and URLs overflow their container on narrow viewports — no word-break/overflow-wrap | Polish | 4 |
| `border_color_hardcoded` | Border colors are hardcoded hex values — not using CSS variables or opacity tokens | Polish | 4 |
| `body_bg_not_extended` | Background color stops at content end — shows white/browser default on short pages | Polish | 4 |
| `no_print_styles` | No @media print styles — page prints as broken dark background with white text | Polish | 4 |
| `missing_lang_attribute` | HTML element missing lang attribute — screen readers can't determine language | Polish | 4 |
| `no_skip_link` | No skip-to-content link for keyboard users — Tab from address bar lands in nav | Polish | 4 |
| `icon_only_buttons_no_label` | Icon-only buttons have no aria-label — screen readers announce nothing useful | Polish | 4 |
| `input_no_autocomplete` | Form inputs missing autocomplete attribute — browser can't assist users | Polish | 4 |
| `missing_error_states` | Form fields show no error state on invalid input — no color, icon, or message | Polish | 4 |
| `no_success_feedback` | Form submission shows no success confirmation — user can't tell if it worked | Polish | 4 |
| `animation_no_will_change` | Animated elements missing will-change hint — causes jank on low-end devices | Polish | 4 |
| `tap_target_too_small` | Interactive elements under 44×44px on mobile — below recommended touch target size | Polish | 4 |
| `overflow_hidden_clipping` | overflow:hidden clips content unexpectedly on mobile — likely desktop-first build | Polish | 4 |
| `hardcoded_px_spacing` | Spacing uses hardcoded px throughout with no rem or spacing scale system | Polish | 4 |
| `no_focus_visible_distinction` | :focus and :focus-visible not distinguished — focus rings show on mouse click too | Polish | 4 |
| `image_lazy_load_missing` | Below-fold images have no loading="lazy" — unnecessary bandwidth on page load | Polish | 4 |
| `no_viewport_meta` | Missing or incomplete viewport meta tag — page doesn't scale correctly on mobile | Polish | 4 |
| `color_scheme_meta_missing` | No color-scheme meta tag — browser UI (scrollbars, inputs) doesn't match dark theme | Polish | 4 |
| `heading_hierarchy_skipped` | Heading levels skip (e.g. h1 → h3) — broken document outline | Polish | 4 |

---

## Signal Count Summary

| Tier | Count | Bucket breakdown |
|---|---|---|
| Tier 1 | 5 | 5 Origin, 0 Polish |
| Tier 2 | 12 | 12 Origin, 0 Polish |
| Tier 3 | 20 | 13 Origin, 7 Polish |
| Tier 4 | 42 | 1 Origin, 41 Polish |
| **Total** | **91** | **31 Origin, 60 Polish** |

---

## Maximum Raw Score (for normalisation)

| Tier | Count | Base pts | Bucket mult | Max per signal | Tier total |
|---|---|---|---|---|---|
| Tier 1 Origin | 5 | 10 | 1.5× | 15 | 75 |
| Tier 2 Origin | 12 | 6 | 1.5× | 9 | 108 |
| Tier 3 Origin | 13 | 3 | 1.5× | 4.5 | 58.5 |
| Tier 3 Polish | 7 | 3 | 1.0× | 3 | 21 |
| Tier 4 Origin | 1 | 2 | 1.5× | 3 | 3 |
| Tier 4 Polish | 41 | 2 | 1.0× | 2 | 82 |
| **Total** | **91** | — | — | — | **347.5** |

Normalise: `vibe_score = clamp(0,100, round((raw_score / 347.5) × 100))`

---

## Category Thresholds (recalibrated for 91 signals)

| Category | Normalised Score | What it means |
|---|---|---|
| Fully Vibe Coded | 65–100 | Strong Origin signal cluster + widespread Polish neglect |
| Slight Dev Effort | 40–64 | Some Tier 1/2 fixed, Tier 3/4 largely untouched |
| Decent Dev Effort | 16–39 | Tier 1/2 mostly clean, scattered mid/low signals remain |
| Human Built | 0–15 | Nearly all signals absent, only rare isolated Tier 4 traces |

Groq confirmation window: **38–52 normalised score**

---

## Detection Mode per Signal

| Mode | Meaning |
|---|---|
| `deterministic` | Rule fires based on computed CSS/DOM values — no LLM needed |
| `ambiguous` | Rule uses heuristics with confidence score — LLM confirms if borderline |

Signals marked `ambiguous` in the original list retain that classification. All new signals added in this document are `deterministic` unless noted below.

### New signals marked `ambiguous`
- `wide_color_palette` — color sampling is heuristic, palette intent is ambiguous
- `stock_image_pattern` — image source detection requires LLM visual judgment
- `color_contrast_borderline` — "feels wrong" is perceptual, not purely computed
- `no_shadow_system` — distinguishing intentional flat design from oversight needs context

All other new signals are `deterministic`.

