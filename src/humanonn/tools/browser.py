from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

from humanonn.config import Settings
from humanonn.models import AuditSnapshot
from humanonn.runtime import terminal_log


def crawl_page(url: str, settings: Settings) -> AuditSnapshot:
    screenshot_path = _screenshot_path(url, settings.screenshot_dir)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    terminal_log(f"Starting crawl for {url}", settings.terminal_logs)
    terminal_log(f"Screenshot target: {screenshot_path}", settings.terminal_logs)

    try:
        with sync_playwright() as p:
            terminal_log(f"Launching Chromium (headless={settings.headless})", settings.terminal_logs)
            browser = p.chromium.launch(headless=settings.headless)
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            _attach_page_logs(page, settings)
            terminal_log("Navigating to page and waiting for network idle", settings.terminal_logs)
            page.goto(url, wait_until="networkidle", timeout=settings.timeout_ms)
            terminal_log(f"Navigation complete: {page.url}", settings.terminal_logs)
            _explore_page(page, settings)
            page.screenshot(path=str(screenshot_path), full_page=True)
            terminal_log("Saved full-page screenshot", settings.terminal_logs)
            terminal_log("Extracting computed styles, structure, and content snapshot", settings.terminal_logs)
            data = page.evaluate(_SNAPSHOT_SCRIPT)
            _log_snapshot_summary(data, settings)
            browser.close()
            terminal_log("Closed Chromium session", settings.terminal_logs)
    except PlaywrightError as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise RuntimeError("Playwright Chromium is not installed. Run: python -m playwright install chromium") from exc
        raise
    except PermissionError as exc:
        raise RuntimeError("Playwright could not launch Chromium in this environment because process creation was denied.") from exc

    return AuditSnapshot(
        url=url,
        title=data.get("title", ""),
        screenshot_path=str(screenshot_path),
        colors=data.get("colors", {}),
        fonts=data.get("fonts", []),
        body=data.get("body", {}),
        buttons=data.get("buttons", []),
        inputs=data.get("inputs", []),
        links=data.get("links", []),
        headings=data.get("headings", []),
        sections=data.get("sections", []),
        images=data.get("images", []),
        text=data.get("text", {}),
        raw=data.get("raw", {}),
    )


def inspect_elements(snapshot: AuditSnapshot, selectors: list[str]) -> dict[str, Any]:
    return {
        "selectors": selectors,
        "buttons": snapshot.buttons if any("button" in s for s in selectors) else [],
        "inputs": snapshot.inputs if any("input" in s for s in selectors) else [],
        "links": snapshot.links if any("a" == s or "link" in s for s in selectors) else [],
    }


def analyze_layout(snapshot: AuditSnapshot) -> dict[str, Any]:
    section_roles = [section.get("role") for section in snapshot.sections]
    heading_alignments = [heading.get("textAlign") for heading in snapshot.headings]
    return {
        "section_count": len(snapshot.sections),
        "section_roles": section_roles,
        "heading_alignments": heading_alignments,
        "card_like_sections": sum(1 for s in snapshot.sections if s.get("looksCard")),
        "grid_like_sections": sum(1 for s in snapshot.sections if s.get("display") == "grid"),
    }


def check_accessibility(snapshot: AuditSnapshot) -> dict[str, Any]:
    return {
        "inputs": [
            {
                "type": item.get("type"),
                "placeholder": item.get("placeholder"),
                "label": item.get("label"),
                "hasVisibleFocus": item.get("hasVisibleFocus"),
            }
            for item in snapshot.inputs
        ],
        "images": [{"src": item.get("src"), "alt": item.get("alt")} for item in snapshot.images],
        "titleAttributeCount": snapshot.raw.get("titleAttributeCount", 0),
    }


def analyze_copy(snapshot: AuditSnapshot) -> dict[str, Any]:
    return {
        "button_texts": [button.get("text") for button in snapshot.buttons if button.get("text")],
        "link_texts": [link.get("text") for link in snapshot.links if link.get("text")],
        "headings": [heading.get("text") for heading in snapshot.headings if heading.get("text")],
        "visible_text_sample": snapshot.text.get("visible", "")[:3000],
    }


def _screenshot_path(url: str, screenshot_dir: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return Path(screenshot_dir) / f"{digest}.png"


def _attach_page_logs(page: Any, settings: Settings) -> None:
    if not settings.terminal_logs:
        return
    page.on("console", lambda msg: terminal_log(f"browser console [{msg.type}]: {msg.text}", settings.terminal_logs))
    page.on("pageerror", lambda exc: terminal_log(f"browser page error: {exc}", settings.terminal_logs))
    page.on(
        "requestfailed",
        lambda request: terminal_log(
            f"request failed: {request.method} {request.url} ({request.failure})",
            settings.terminal_logs,
        ),
    )


def _explore_page(page: Page, settings: Settings) -> None:
    terminal_log("Exploring page sections to trigger lazy rendering", settings.terminal_logs)
    _scroll_through_sections(page, settings)
    _sweep_full_page(page, settings)
    terminal_log("Probing hover states on interactive elements", settings.terminal_logs)
    _probe_hover_states(page, settings)
    page.mouse.move(0, 0)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    terminal_log("Exploration pass complete", settings.terminal_logs)


def _scroll_through_sections(page: Page, settings: Settings) -> None:
    selector = "main section, section, article, [data-section], footer, [class*='feature'], [class*='pricing'], [class*='testimonial']"
    locator = page.locator(selector)
    count = min(locator.count(), 120)
    terminal_log(f"Section candidates found: {count}", settings.terminal_logs)
    for index in range(count):
        item = locator.nth(index)
        try:
            item.scroll_into_view_if_needed(timeout=2000)
            item.evaluate("(el, i) => el.setAttribute('data-humanonn-scroll-index', String(i))", index)
            terminal_log(f"Scrolled section {index + 1}/{count}", settings.terminal_logs)
            page.wait_for_timeout(180)
        except PlaywrightError as exc:
            terminal_log(f"Skipped section {index + 1}: {exc}", settings.terminal_logs)


def _sweep_full_page(page: Page, settings: Settings) -> None:
    previous_height = 0
    for pass_index in range(2):
        metrics = page.evaluate(
            """() => ({
                viewport: window.innerHeight,
                height: Math.max(
                    document.body.scrollHeight,
                    document.documentElement.scrollHeight
                )
            })"""
        )
        viewport = int(metrics["viewport"] or 800)
        height = int(metrics["height"] or viewport)
        terminal_log(f"Scroll sweep {pass_index + 1}: page height {height}px", settings.terminal_logs)
        step = max(300, viewport - 160)
        y = 0
        while y < height:
            page.evaluate("(value) => window.scrollTo(0, value)", y)
            page.wait_for_timeout(140)
            y += step
        page.evaluate("(value) => window.scrollTo(0, value)", height)
        page.wait_for_timeout(220)
        if height <= previous_height:
            break
        previous_height = height


def _probe_hover_states(page: Page, settings: Settings) -> None:
    hover_targets = [
        ("button", "button, a[role='button'], input[type='button'], input[type='submit']", 80),
        ("link", "a", 120),
        ("card", "[class*='card'], [class*='feature'], [class*='pricing'], [class*='testimonial'], section > div", 120),
    ]
    for label, selector, limit in hover_targets:
        locator = page.locator(selector)
        count = min(locator.count(), limit)
        changed = 0
        terminal_log(f"Hover targets for {label}: {count}", settings.terminal_logs)
        for index in range(count):
            item = locator.nth(index)
            try:
                item.scroll_into_view_if_needed(timeout=2000)
                before = item.evaluate(_HOVER_STYLE_SCRIPT)
                item.hover(timeout=2000, force=True)
                page.wait_for_timeout(90)
                after = item.evaluate(_HOVER_STYLE_SCRIPT)
                diff = _diff_hover_styles(before, after)
                if diff:
                    changed += 1
                item.evaluate(
                    """(el, payload) => {
                        el.setAttribute('data-humanonn-hover-effect', payload.changed ? 'true' : 'false');
                        el.setAttribute('data-humanonn-hover-diff', payload.diff.join('|'));
                    }""",
                    {"changed": bool(diff), "diff": diff},
                )
            except PlaywrightError as exc:
                terminal_log(f"Skipped {label} hover {index + 1}: {exc}", settings.terminal_logs)
        page.mouse.move(0, 0)
        terminal_log(f"Hover effects detected for {label}: {changed}/{count}", settings.terminal_logs)


def _diff_hover_styles(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    for key in before:
        if str(before.get(key)) != str(after.get(key)):
            changes.append(key)
    return changes


def _log_snapshot_summary(data: dict[str, Any], settings: Settings) -> None:
    terminal_log(f"Page title: {data.get('title') or '(untitled)'}", settings.terminal_logs)
    terminal_log(
        "Found "
        f"{len(data.get('buttons', []))} buttons, "
        f"{len(data.get('inputs', []))} inputs, "
        f"{len(data.get('links', []))} links, "
        f"{len(data.get('headings', []))} headings, "
        f"{len(data.get('sections', []))} sections, "
        f"{len(data.get('images', []))} images",
        settings.terminal_logs,
    )
    fonts = data.get("fonts", [])[:5]
    if fonts:
        terminal_log(f"Fonts detected: {', '.join(fonts)}", settings.terminal_logs)
    colors = data.get("colors", {}).get("all", [])[:5]
    if colors:
        terminal_log(f"Sample colors: {', '.join(colors)}", settings.terminal_logs)
    button_texts = [item.get("text", "").strip() for item in data.get("buttons", []) if item.get("text", "").strip()][:5]
    if button_texts:
        terminal_log(f"Sample button text: {', '.join(button_texts)}", settings.terminal_logs)
    heading_texts = [item.get("text", "").strip() for item in data.get("headings", []) if item.get("text", "").strip()][:5]
    if heading_texts:
        terminal_log(f"Sample headings: {', '.join(heading_texts)}", settings.terminal_logs)
    input_types = [item.get("type", "") for item in data.get("inputs", [])][:5]
    if input_types:
        terminal_log(f"Input types: {', '.join(input_types)}", settings.terminal_logs)
    hover_button_count = sum(1 for item in data.get("buttons", []) if item.get("hasHoverEffect"))
    hover_link_count = sum(1 for item in data.get("links", []) if item.get("hasHoverEffect"))
    hover_card_count = sum(1 for item in data.get("sections", []) if item.get("hasHoverEffect"))
    terminal_log(
        f"Hover effects recorded: buttons {hover_button_count}, links {hover_link_count}, cards/sections {hover_card_count}",
        settings.terminal_logs,
    )
    body = data.get("body", {})
    terminal_log(
        f"Body background: {body.get('backgroundColor', 'n/a')} | Hero background image present: "
        f"{bool(body.get('heroBackgroundImage') and body.get('heroBackgroundImage') != 'none')}",
        settings.terminal_logs,
    )


_SNAPSHOT_SCRIPT = """
() => {
  const px = (value) => Number.parseFloat(String(value || "0")) || 0;
  const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ");
  const styleOf = (el) => {
    const cs = getComputedStyle(el);
    return {
      color: cs.color,
      backgroundColor: cs.backgroundColor,
      backgroundImage: cs.backgroundImage,
      borderRadius: cs.borderRadius,
      borderTopWidth: cs.borderTopWidth,
      borderBottomWidth: cs.borderBottomWidth,
      boxShadow: cs.boxShadow,
      backdropFilter: cs.backdropFilter || cs.webkitBackdropFilter || "",
      fontFamily: cs.fontFamily,
      fontSize: cs.fontSize,
      lineHeight: cs.lineHeight,
      letterSpacing: cs.letterSpacing,
      textAlign: cs.textAlign,
      textDecorationLine: cs.textDecorationLine,
      transitionDuration: cs.transitionDuration,
      transitionTimingFunction: cs.transitionTimingFunction,
      outlineColor: cs.outlineColor,
      outlineStyle: cs.outlineStyle,
      outlineWidth: cs.outlineWidth,
      cursor: cs.cursor,
      opacity: cs.opacity,
      display: cs.display,
      gridTemplateColumns: cs.gridTemplateColumns,
      paddingTop: cs.paddingTop,
      paddingBottom: cs.paddingBottom,
      maxWidth: cs.maxWidth,
      backgroundClip: cs.backgroundClip || cs.webkitBackgroundClip || "",
      textFillColor: cs.webkitTextFillColor || cs.color
    };
  };
  const compactStyle = (el) => ({ text: textOf(el), className: el.className || "", ...styleOf(el) });
  const humanonnMeta = (el) => ({
    hasHoverEffect: el.getAttribute("data-humanonn-hover-effect") === "true",
    hoverDiff: (el.getAttribute("data-humanonn-hover-diff") || "").split("|").filter(Boolean),
    scrollIndex: el.getAttribute("data-humanonn-scroll-index") || ""
  });
  const hasVisibleFocus = (el) => {
    const before = styleOf(el);
    el.focus({ preventScroll: true });
    const after = styleOf(el);
    el.blur();
    return before.boxShadow !== after.boxShadow ||
      before.borderTopWidth !== after.borderTopWidth ||
      before.backgroundColor !== after.backgroundColor ||
      before.outlineColor !== after.outlineColor ||
      before.outlineStyle !== after.outlineStyle ||
      before.outlineWidth !== after.outlineWidth;
  };
  const hasActiveFeedback = (el) => {
    const transition = getComputedStyle(el).transitionDuration;
    const transform = getComputedStyle(el).transform;
    return transition !== "0s" || transform !== "none";
  };
  const labelFor = (el) => {
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return textOf(label);
    }
    const parentLabel = el.closest("label");
    return parentLabel ? textOf(parentLabel) : "";
  };
  const bodyStyle = styleOf(document.body);
  const hero = document.querySelector("main section, section, header");
  const body = {
    ...bodyStyle,
    heroBackgroundImage: hero ? getComputedStyle(hero).backgroundImage : "",
  };
  const buttons = [...document.querySelectorAll("button, a[role='button'], input[type='button'], input[type='submit']")]
    .slice(0, 80)
    .map((el) => ({
      ...compactStyle(el),
      ...humanonnMeta(el),
      disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
      hasActiveFeedback: hasActiveFeedback(el)
    }));
  const inputs = [...document.querySelectorAll("input, textarea, select")]
    .slice(0, 80)
    .map((el) => ({
      ...compactStyle(el),
      ...humanonnMeta(el),
      type: el.getAttribute("type") || el.tagName.toLowerCase(),
      placeholder: el.getAttribute("placeholder") || "",
      label: labelFor(el),
      hasVisibleFocus: hasVisibleFocus(el)
    }));
  const links = [...document.querySelectorAll("a")]
    .slice(0, 120)
    .map((el) => ({
      ...compactStyle(el),
      ...humanonnMeta(el),
      href: el.href,
      inNav: Boolean(el.closest("nav")),
      ariaCurrent: el.getAttribute("aria-current") || ""
    }));
  const headings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6,[class*='badge'],[class*='eyebrow']")]
    .slice(0, 120)
    .map((el) => ({ ...compactStyle(el), ...humanonnMeta(el), tagName: el.tagName.toLowerCase(), fontSizeRaw: el.getAttribute("style") || "" }));
  const sections = [...document.querySelectorAll("section, main > div, article, [class*='card'], [class*='feature']")]
    .slice(0, 120)
    .map((el) => {
      const s = compactStyle(el);
      const rect = el.getBoundingClientRect();
      const p = el.querySelector("p");
      return {
        ...s,
        ...humanonnMeta(el),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        role: el.getAttribute("data-section") || el.getAttribute("aria-label") || "",
        looksCard: px(s.borderRadius) >= 8 || s.boxShadow !== "none" || s.backdropFilter.includes("blur"),
        hasLink: Boolean(el.querySelector("a,button")),
        paragraphMaxWidth: p ? Math.round(p.getBoundingClientRect().width) : 0
      };
    });
  const images = [...document.querySelectorAll("img")]
    .slice(0, 120)
    .map((el) => ({ src: el.currentSrc || el.src, alt: el.getAttribute("alt") || "", width: el.naturalWidth, height: el.naturalHeight }));
  const colorValues = [];
  [...document.querySelectorAll("body, main, section, button, a, h1, h2, h3, p")]
    .slice(0, 200)
    .forEach((el) => {
      const s = getComputedStyle(el);
      colorValues.push(s.color, s.backgroundColor, s.borderColor);
    });
  const fonts = [...new Set([...document.querySelectorAll("body, h1, h2, h3, p, button, a")]
    .map((el) => getComputedStyle(el).fontFamily.split(",")[0].replace(/[\"']/g, "").trim())
    .filter(Boolean))];
  return {
    title: document.title || "",
    colors: { all: [...new Set(colorValues)].filter((c) => c && c !== "rgba(0, 0, 0, 0)") },
    fonts,
    body,
    buttons,
    inputs,
    links,
    headings,
    sections,
    images,
    text: { visible: textOf(document.body) },
    raw: {
      hasFavicon: Boolean(document.querySelector("link[rel~='icon'], link[rel='shortcut icon']")),
      titleAttributeCount: document.querySelectorAll("[title]").length,
      scrollTriggeredExploration: true
    }
  };
}
"""


_HOVER_STYLE_SCRIPT = """
(el) => {
  const cs = getComputedStyle(el);
  return {
    color: cs.color,
    backgroundColor: cs.backgroundColor,
    borderColor: cs.borderColor,
    boxShadow: cs.boxShadow,
    transform: cs.transform,
    opacity: cs.opacity,
    filter: cs.filter,
    textDecorationLine: cs.textDecorationLine,
    textDecorationColor: cs.textDecorationColor,
    outlineColor: cs.outlineColor
  };
}
"""


def to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
