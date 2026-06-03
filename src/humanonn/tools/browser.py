from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from humanonn.config import Settings
from humanonn.models import AuditSnapshot
from humanonn.runtime import terminal_log


_NOISE_REQUEST_HOST_TOKENS = (
    "sentry.io",
    "sentry-cdn.com",
    "google-analytics.com",
    "googletagmanager.com",
    "growthbook.io",
    "tiktok.com",
    "analytics.google.com",
)


def crawl_page(url: str, settings: Settings) -> AuditSnapshot:
    screenshot_path = _screenshot_path(url, settings.screenshot_dir)
    artifact_root = _artifact_root(url, settings)
    manifest_path = artifact_root / "manifest.json"
    main_image_path = artifact_root / "main.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    terminal_log(f"Starting crawl for {url}", settings.terminal_logs)
    terminal_log(f"Main screenshot target: {main_image_path}", settings.terminal_logs)
    terminal_log(f"Legacy screenshot target: {screenshot_path}", settings.terminal_logs)

    try:
        with sync_playwright() as p:
            terminal_log(f"Launching Chromium (headless={settings.headless})", settings.terminal_logs)
            launch_args = ["--enable-unsafe-swiftshader"]
            if settings.production:
                launch_args.extend([
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--js-flags=--max-old-space-size=256",
                ])
            browser = p.chromium.launch(
                headless=settings.headless,
                args=launch_args,
            )
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1200})
                page.route("**/*", lambda route, request: _handle_routed_request(route, request))
                _attach_page_logs(page, settings)

                terminal_log("Navigating to page with staged readiness checks", settings.terminal_logs)
                _navigate_page(page, url, settings)
                _settle_page(page, settings, "post-navigation", wait_ms=3600)
                _scroll_to_bottom_pass(page, settings)
                terminal_log(f"Navigation complete: {page.url}", settings.terminal_logs)

                sections = _discover_sections(page, settings)
                _overview_scroll_sections(page, sections, settings)

                page.evaluate("window.scrollTo(0, 0)")
                _settle_page(page, settings, "before-main-screenshot")
                if settings.production:
                    scroll_height = page.evaluate("document.body.scrollHeight || document.documentElement.scrollHeight") or 1200
                    clip_height = min(int(scroll_height), 4000)
                    original_viewport = page.viewport_size or {"width": 1440, "height": 1200}
                    page.set_viewport_size({"width": original_viewport["width"], "height": clip_height})
                    page.screenshot(path=str(main_image_path), full_page=False)
                    page.set_viewport_size(original_viewport)
                else:
                    page.screenshot(path=str(main_image_path), full_page=True)
                import shutil
                try:
                    shutil.copyfile(main_image_path, screenshot_path)
                except Exception as copy_err:
                    terminal_log(f"Failed to copy screenshot: {copy_err}", settings.terminal_logs)
                terminal_log("Saved main page overview screenshots", settings.terminal_logs)

                manifest = _capture_section_artifacts(page, sections, artifact_root, settings)
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                terminal_log(f"Artifact manifest written to {manifest_path}", settings.terminal_logs)

                for section in sections:
                    _retarget_section_locator(page, section)
                page.evaluate("window.scrollTo(0, 0)")
                _settle_page(page, settings, "before-snapshot")
                page.evaluate("document.fonts ? document.fonts.ready : Promise.resolve()")
                terminal_log("Extracting computed styles, structure, and content snapshot", settings.terminal_logs)
                data = page.evaluate(_SNAPSHOT_SCRIPT)
                data.setdefault("raw", {})
                data["raw"].update(
                    {
                        "artifact_root": str(artifact_root),
                        "manifest_path": str(manifest_path),
                        "main_image_path": str(main_image_path),
                        "section_artifact_count": len(manifest.get("sections", [])),
                        "needs_vision_override": bool(manifest.get("needs_vision_override")),
                        "scan_metadata": _scan_metadata_from_manifest(manifest),
                    }
                )
                # If the crawler produced synthetic component artifacts (section-level samples),
                # merge their computed style samples into the in-memory snapshot so rule
                # evaluators that operate on `snapshot.sections` can see them.
                try:
                    synthetic_count = 0
                    for sec in manifest.get("sections", []):
                        for comp in sec.get("components", []):
                            if comp.get("synthetic"):
                                synthetic_count += 1
                                # Build a snapshot-like section entry from the synthetic record
                                style = comp.get("before_style", {}) or {}
                                section_entry = {
                                    "text": comp.get("text", ""),
                                    "className": comp.get("className", "synthetic-section"),
                                    "backdropFilter": style.get("backdropFilter") or style.get("backdropFilter", ""),
                                    "boxShadow": style.get("boxShadow"),
                                    "backgroundImage": style.get("backgroundImage"),
                                    "borderTopWidth": style.get("borderTopWidth"),
                                    "borderBottomWidth": style.get("borderBottomWidth"),
                                    "borderRadius": style.get("borderRadius"),
                                    "width": comp.get("width") or style.get("width") or 0,
                                    "height": comp.get("height") or style.get("height") or 0,
                                    "id": comp.get("id"),
                                    "role": comp.get("section_id", ""),
                                    "looksCard": (style.get("borderRadius") and len(str(style.get("borderRadius")) > 0)) or False,
                                    "hasLink": False,
                                    "paragraphMaxWidth": 0,
                                }
                                data.setdefault("sections", []).append(section_entry)
                    if synthetic_count:
                        terminal_log(f"Merged {synthetic_count} synthetic component(s) into in-memory snapshot.sections", settings.terminal_logs)
                except Exception:
                    # best-effort: do not fail the crawl if merging synthetic samples fails
                    terminal_log("Failed to merge synthetic artifacts into snapshot; continuing without merge", settings.terminal_logs)
                _log_snapshot_summary(data, settings)
            finally:
                if browser:
                    try:
                        browser.close()
                        terminal_log("Closed Chromium session", settings.terminal_logs)
                    except Exception as close_err:
                        terminal_log(f"Error closing Chromium browser: {close_err}", settings.terminal_logs)
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
        screenshot_path=str(main_image_path),
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
        "artifact_root": snapshot.raw.get("artifact_root"),
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


def _artifact_root(url: str, settings: Settings) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    report_root = Path(settings.screenshot_dir).parent
    return report_root / "data" / digest


def _attach_page_logs(page: Page, settings: Settings) -> None:
    if not settings.terminal_logs:
        return
    page.on("console", lambda msg: terminal_log(f"browser console [{msg.type}]: {msg.text}", settings.terminal_logs))
    page.on("pageerror", lambda exc: terminal_log(f"browser page error: {exc}", settings.terminal_logs))
    page.on(
        "response",
        lambda response: None if response.status < 400 or _is_noise_request(response.url) else terminal_log(
            f"response error: {response.status} {response.url}",
            settings.terminal_logs,
        ),
    )
    page.on(
        "requestfailed",
        lambda request: None if _is_noise_request(request.url) else terminal_log(
            f"request failed: {request.method} {request.url} ({request.failure})",
            settings.terminal_logs,
        ),
    )


def _handle_routed_request(route: Any, request: Any) -> None:
    if (
        request.resource_type in ("media", "video")
        or request.url.endswith((".webm", ".mp4"))
        or ("storage.googleapis.com" in request.url and "video" in request.url)
        or _is_noise_request(request.url)
    ):
        route.abort()
        return
    route.continue_()


def _is_noise_request(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in _NOISE_REQUEST_HOST_TOKENS)


def _navigate_page(page: Page, url: str, settings: Settings) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=settings.navigation_timeout_ms)
        terminal_log("Navigation reached DOMContentLoaded", settings.terminal_logs)
    except PlaywrightTimeoutError as exc:
        if _page_has_usable_dom(page):
            terminal_log(
                f"DOMContentLoaded timed out but usable DOM is present; continuing: {_summarize_error(exc)}",
                settings.terminal_logs,
            )
        else:
            raise RuntimeError(
                f"Navigation timed out before the page produced usable DOM. Increase HUMANONN_NAVIGATION_TIMEOUT_MS if needed."
            ) from exc

    try:
        page.wait_for_load_state("load", timeout=settings.navigation_timeout_ms)
        terminal_log("Page reached load state", settings.terminal_logs)
    except PlaywrightTimeoutError as exc:
        terminal_log(f"Load state wait timed out; continuing with current DOM: {_summarize_error(exc)}", settings.terminal_logs)

    try:
        page.locator("body").wait_for(state="visible", timeout=min(settings.navigation_timeout_ms, 5000))
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(f"Page body never became visible: {_summarize_error(exc)}") from exc


def _scroll_to_bottom_pass(page: Page, settings: Settings) -> None:
    terminal_log("Performing scroll-to-bottom render pass", settings.terminal_logs)
    page.evaluate(
        """async () => {
            const step = Math.max(320, Math.floor(window.innerHeight * 0.8));
            let position = 0;
            const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
            while (position < maxScroll) {
                window.scrollTo(0, position);
                await new Promise((resolve) => setTimeout(resolve, 120));
                position += step;
            }
            window.scrollTo(0, maxScroll);
            await new Promise((resolve) => setTimeout(resolve, 180));
            window.scrollTo(0, 0);
            await new Promise((resolve) => setTimeout(resolve, 120));
        }"""
    )
    _settle_page(page, settings, "after-scroll-to-bottom", wait_ms=3600)


def _page_has_usable_dom(page: Page) -> bool:
    try:
        return bool(
            page.evaluate(
                """() => {
                    const body = document.body;
                    if (!body) return false;
                    const text = (body.innerText || body.textContent || "").trim();
                    return document.readyState !== "loading" && (text.length > 40 || body.querySelector("main, section, article, nav, footer, img, button, a"));
                }"""
            )
        )
    except PlaywrightError:
        return False


def _discover_sections(page: Page, settings: Settings) -> list[dict[str, Any]]:
    terminal_log("Discovering visible sections", settings.terminal_logs)
    sections = page.evaluate(_DISCOVER_SECTIONS_SCRIPT)
    terminal_log(f"Section candidates found: {len(sections)}", settings.terminal_logs)
    for section in sections[:8]:
        terminal_log(
            f"Section {section['index'] + 1}: {section['label']} ({section['width']}x{section['height']})",
            settings.terminal_logs,
        )
    return sections


def _overview_scroll_sections(page: Page, sections: list[dict[str, Any]], settings: Settings) -> None:
    terminal_log("Overview pass: scrolling section by section to trigger lazy rendering", settings.terminal_logs)
    for section in sections:
        locator = _retarget_section_locator(page, section)
        try:
            _scroll_locator_into_focus(page, locator, settings)
            _safe_locator_evaluate(locator, "(el, index) => el.setAttribute('data-humanonn-scroll-index', String(index))", section["index"])
            terminal_log(f"Overview section {section['index'] + 1}/{len(sections)}: {section['label']}", settings.terminal_logs)
        except (PlaywrightError, RuntimeError) as exc:
            terminal_log(f"Skipped overview section {section['index'] + 1}: {_summarize_error(exc)}", settings.terminal_logs)
    page.evaluate("window.scrollTo(0, 0)")
    _settle_page(page, settings, "after-overview")


def _capture_section_artifacts(
    page: Page,
    sections: list[dict[str, Any]],
    artifact_root: Path,
    settings: Settings,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "artifact_root": str(artifact_root),
        "main_image_path": str(artifact_root / "main.png"),
        "sections": [],
        "needs_vision_override": False,
    }
    for section in sections:
        section_dir = artifact_root / f"section_{section['index'] + 1:03d}_{_slugify(section['label'])}"
        section_dir.mkdir(parents=True, exist_ok=True)
        locator = _retarget_section_locator(page, section)
        try:
            _scroll_locator_into_focus(page, locator, settings)
            section_image = section_dir / "section.png"
            if not settings.production or section["index"] < 2:
                locator.screenshot(path=str(section_image), timeout=5000)
                record_image_path = str(section_image)
            else:
                record_image_path = None
            terminal_log(
                f"Capturing section {section['index'] + 1}/{len(sections)}: {section['label']}",
                settings.terminal_logs,
            )
            components = _discover_components(page, section["id"], settings)
            if not components:
                terminal_log(
                    f"Section {section['index'] + 1} returned 0 components; using synthetic section style sample",
                    settings.terminal_logs,
                )
                component_manifest = [
                    _build_section_synthetic_component(page, locator, section, section_dir, settings)
                ]
                manifest["needs_vision_override"] = True
            else:
                component_manifest = _capture_components(page, section, components, section_dir, settings)
                if any(component.get("needs_vision_override") for component in component_manifest):
                    manifest["needs_vision_override"] = True
            manifest["sections"].append(
                {
                    **section,
                    "section_image_path": record_image_path,
                    "components": component_manifest,
                }
            )
        except (PlaywrightError, RuntimeError) as exc:
            terminal_log(f"Skipped section capture {section['index'] + 1}: {_summarize_error(exc)}", settings.terminal_logs)
            manifest["sections"].append(
                {
                    **section,
                    "error": _summarize_error(exc),
                    "components": [],
                }
            )
    return manifest


def _discover_components(page: Page, section_id: str, settings: Settings) -> list[dict[str, Any]]:
    components = page.evaluate(_DISCOVER_COMPONENTS_SCRIPT, section_id)
    duplicate_total = sum(max(0, int(component.get("duplicateCount", 1)) - 1) for component in components)
    terminal_log(
        f"Components found in {section_id}: {len(components)} representatives, {duplicate_total} duplicates collapsed",
        settings.terminal_logs,
    )
    return components


def _build_section_synthetic_component(
    page: Page,
    locator: Locator,
    section: dict[str, Any],
    section_dir: Path,
    settings: Settings,
) -> dict[str, Any]:
    component_dir = section_dir / f"component_001_section_{_slugify(section['label'])}"
    component_dir.mkdir(parents=True, exist_ok=True)
    style = _safe_locator_evaluate(locator, _SECTION_SYNTHETIC_STYLE_SCRIPT) or {}
    record: dict[str, Any] = {
        "id": f"{section['id']}-synthetic-section",
        "index": 0,
        "kind": "section",
        "label": section["label"],
        "text": section.get("label", ""),
        "width": int(style.get("width") or section.get("width") or 0),
        "height": int(style.get("height") or section.get("height") or 0),
        "className": "synthetic-section",
        "styles": style,
        "patternKey": f"section|{section['id']}|synthetic",
        "duplicateCount": 1,
        "interactableHint": False,
        "synthetic": True,
        "status": "synthetic",
        "verified": False,
        "section_id": section["id"],
        "path": str(component_dir),
        "attempts": [],
        "before_image_path": None,
        "hover_image_path": None,
        "active_image_path": None,
        "focus_image_path": None,
        "unverified_reason": "section had no discoverable child components",
        "needs_vision_override": True,
        "before_style": style,
        "hover_style": style,
        "active_style": style,
        "hover_changed": False,
        "active_changed": False,
        "hover_diff": [],
        "active_diff": [],
    }
    metadata_path = component_dir / "metadata.json"
    metadata_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["metadata_path"] = str(metadata_path)
    return record


def _capture_components(
    page: Page,
    section: dict[str, Any],
    components: list[dict[str, Any]],
    section_dir: Path,
    settings: Settings,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    profiles = _build_pass_profiles(settings)
    consecutive_timeouts = 0
    import os
    max_components = int(os.getenv("HUMANONN_MAX_COMPONENTS_PER_SECTION", "5")) if settings.production else len(components)
    for index, component in enumerate(components):
        if index >= max_components:
            manifest.append(
                _build_unverified_component_record(
                    section,
                    component,
                    section_dir,
                    settings,
                    f"section component limit exceeded (max {max_components})",
                )
            )
            continue
        record, next_timeouts = _capture_component_with_retries(page, section, component, section_dir, settings, profiles, consecutive_timeouts)
        consecutive_timeouts = next_timeouts
        manifest.append(record)
        if consecutive_timeouts >= 2:
            for remaining_component in components[index + 1 :]:
                manifest.append(
                    _build_unverified_component_record(
                        section,
                        remaining_component,
                        section_dir,
                        settings,
                        "consecutive section timeouts",
                    )
                )
            break
    return manifest


def _build_unverified_component_record(
    section: dict[str, Any],
    component: dict[str, Any],
    section_dir: Path,
    settings: Settings,
    reason: str,
    needs_vision_override: bool = False,
) -> dict[str, Any]:
    component_dir = section_dir / f"component_{component['index'] + 1:03d}_{component['kind']}_{_slugify(component['label'])}"
    component_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        **component,
        "section_id": section["id"],
        "path": str(component_dir),
        "status": "unverified",
        "verified": False,
        "attempts": [],
        "before_image_path": None,
        "hover_image_path": None,
        "active_image_path": None,
        "focus_image_path": None,
        "unverified_reason": reason,
    }
    if needs_vision_override:
        record["needs_vision_override"] = True
    metadata_path = component_dir / "metadata.json"
    metadata_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["metadata_path"] = str(metadata_path)
    terminal_log(
        f"Marked component {component['index'] + 1} in {section['label']} as unverified: {reason}",
        settings.terminal_logs,
    )
    return record


def _capture_static_component_style(
    locator: Locator,
    component: dict[str, Any],
    component_dir: Path,
    record: dict[str, Any],
    settings: Settings,
) -> None:
    before_style = _safe_locator_evaluate(locator, _INTERACTION_STYLE_SCRIPT) or {}
    if not settings.production:
        before_path = component_dir / "before.png"
        locator.screenshot(path=str(before_path), timeout=5000)
        record["before_image_path"] = str(before_path)
    else:
        record["before_image_path"] = None
    record["hover_image_path"] = None
    record["active_image_path"] = None
    record["focus_image_path"] = None
    record["status"] = "style_verified"
    record["verified"] = True
    record["style_only"] = True
    record["before_style"] = before_style
    record["hover_style"] = before_style
    record["active_style"] = before_style
    record["hover_changed"] = False
    record["active_changed"] = False
    record["hover_diff"] = []
    record["active_diff"] = []
    _safe_locator_evaluate(
        locator,
        """(el) => {
            el.setAttribute('data-humanonn-hover-effect', 'false');
            el.setAttribute('data-humanonn-hover-diff', '');
            el.setAttribute('data-humanonn-active-effect', 'false');
            el.setAttribute('data-humanonn-active-diff', '');
        }""",
    )


def _capture_component_with_retries(
    page: Page,
    section: dict[str, Any],
    component: dict[str, Any],
    section_dir: Path,
    settings: Settings,
    profiles: list[dict[str, Any]],
    consecutive_timeouts: int = 0,
) -> tuple[dict[str, Any], int]:
    locator = page.locator(f"[data-humanonn-component-id='{component['id']}']").first
    component_dir = section_dir / f"component_{component['index'] + 1:03d}_{component['kind']}_{_slugify(component['label'])}"
    component_dir.mkdir(parents=True, exist_ok=True)
    position = (
        component.get("effectivePosition")
        or component.get("position")
        or (component.get("styles") or {}).get("position")
    )
    if not position:
        position = _component_position(locator)
    use_direct_probe = position in {"fixed", "sticky"}
    if use_direct_probe:
        locator = _retarget_direct_probe_locator(page, component)
    record: dict[str, Any] = {
        **component,
        "section_id": section["id"],
        "path": str(component_dir),
        "status": "pending",
        "verified": False,
        "attempts": [],
        "before_image_path": None,
        "hover_image_path": None,
        "active_image_path": None,
        "focus_image_path": None,
    }
    attempt: dict[str, Any] = {}

    skip_reason = None
    pass_allowed = len(profiles)

    classifier = None
    if not use_direct_probe:
        classifier = _safe_locator_evaluate(
            locator,
            """(el) => {
                const rect = el.getBoundingClientRect();
                const isOffscreen = rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth;
                return {
                    tag: el.tagName.toLowerCase(),
                    disabled: el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true',
                    width: rect.width,
                    height: rect.height,
                    isOffscreen: isOffscreen,
                    hasWebGL: el.tagName.toLowerCase() === 'canvas' && Boolean(el.getContext && (el.getContext('webgl') || el.getContext('experimental-webgl')))
                };
            }""",
            timeout_ms=5000
        )

        if classifier is None:
            terminal_log(
                f"classifier_timeout: component {component['index'] + 1} '{component['label']}'",
                settings.terminal_logs,
            )
            attempt["classifier_timeout"] = True
            attempt["screen"] = {
                "isConnected": True,
                "isVisible": True,
                "sizeOk": True,
                "inViewport": True,
                "covered": False,
                "disabled": False,
                "pointerEvents": "auto",
                "clickable": component["kind"] in {"button", "link", "input"},
                "interactable": True,
                "width": component.get("width", 0),
                "height": component.get("height", 0),
            }
        else:
            if classifier["tag"] in ("video", "canvas", "iframe"):
                skip_reason = f"tag is {classifier['tag']}"
                pass_allowed = 0
            elif classifier.get("hasWebGL"):
                skip_reason = "webgl canvas"
                pass_allowed = 0
            elif classifier["width"] == 0 or classifier["height"] == 0:
                skip_reason = "zero-size element"
                pass_allowed = 0
            elif classifier["disabled"]:
                skip_reason = "element is disabled"
                pass_allowed = 0
            elif consecutive_timeouts >= 2:
                skip_reason = "consecutive section timeouts"
                pass_allowed = 0

    if pass_allowed > 0 and skip_reason is None and not use_direct_probe:
        animation_count = _component_animation_count(locator)
        if animation_count > 0:
            pass_allowed = 1
            page.wait_for_timeout(500)

    if pass_allowed == 0 and skip_reason:
        return (
            _build_unverified_component_record(
                section,
                component,
                section_dir,
                settings,
                skip_reason,
                needs_vision_override=(
                    skip_reason.startswith("tag is canvas")
                    or skip_reason.startswith("tag is video")
                    or skip_reason == "webgl canvas"
                ),
            ),
            consecutive_timeouts,
        )

    new_consecutive_timeouts = consecutive_timeouts


    for i, profile in enumerate(profiles[:pass_allowed]):
        is_pass_1 = (i == 0)
        attempt: dict[str, Any] = {"pass": profile["name"]}
        try:
            try:
                box = locator.bounding_box(timeout=5000) or {}
                terminal_log(f"Probe start: component {component['index'] + 1} '{component['label']}' {box}", settings.terminal_logs)
            except Exception:
                pass
            if use_direct_probe:
                _probe_directly_at_current_position(page, locator, settings, profile)
            else:
                _scroll_locator_into_focus(page, locator, settings, profile)
            screen = _component_screen_state(locator, use_direct_probe=use_direct_probe)
            attempt["screen"] = screen
            readiness_reason = _component_readiness_reason(component, screen)
            if readiness_reason == "element not visible" and use_direct_probe:
                try:
                    box = locator.bounding_box(timeout=2000)
                except Exception:
                    box = None
                if box and box.get("width", 0) >= 20 and box.get("height", 0) >= 20:
                    readiness_reason = None
            if (
                readiness_reason == "element is not interactable"
                and component.get("kind") == "card"
                and not component.get("interactableHint")
            ):
                _capture_static_component_style(locator, component, component_dir, record, settings)
                attempt["status"] = "style_verified"
                attempt["reason"] = "static card style sample"
                record["attempts"].append(attempt)
                new_consecutive_timeouts = 0
                break
            if readiness_reason is not None:
                attempt["status"] = "retry"
                attempt["reason"] = readiness_reason
                record["attempts"].append(attempt)
                continue

            _capture_component_states(
                page,
                locator,
                component,
                component_dir,
                record,
                profile,
                settings,
                use_direct_probe=use_direct_probe,
            )
            attempt["status"] = "verified"
            record["status"] = "verified"
            record["verified"] = True
            record["attempts"].append(attempt)
            new_consecutive_timeouts = 0
            break
        except (PlaywrightError, RuntimeError) as exc:
            err_msg = _summarize_error(exc)
            attempt["status"] = "retry"
            attempt["reason"] = err_msg
            record["attempts"].append(attempt)
            terminal_log(
                f"Retrying component {component['index'] + 1} in {section['label']} after {profile['name']}: {err_msg}",
                settings.terminal_logs,
            )
            
            if is_pass_1 and "Timeout" in err_msg:
                new_consecutive_timeouts += 1
            elif is_pass_1:
                new_consecutive_timeouts = 0
        finally:
            page.wait_for_timeout(40)

    if not record["verified"]:
        final_reason = record["attempts"][-1]["reason"] if record["attempts"] else "component was not checked"
        record["status"] = "unverified"
        record["unverified_reason"] = final_reason
        terminal_log(
            f"Marked component {component['index'] + 1} in {section['label']} as unverified: {final_reason}",
            settings.terminal_logs,
        )

    component_dir = Path(record["path"])
    metadata_path = component_dir / "metadata.json"
    metadata_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record["metadata_path"] = str(metadata_path)
    return record, new_consecutive_timeouts


def _capture_component_states(
    page: Page,
    locator: Locator,
    component: dict[str, Any],
    component_dir: Path,
    record: dict[str, Any],
    profile: dict[str, Any],
    settings: Settings,
    use_direct_probe: bool = False,
) -> None:
    before_style = _safe_locator_evaluate(locator, _INTERACTION_STYLE_SCRIPT) or {}
    if not settings.production:
        before_path = component_dir / "before.png"
        locator.screenshot(path=str(before_path), timeout=5000)
        record["before_image_path"] = str(before_path)
    else:
        record["before_image_path"] = None

    hover_style = before_style
    hover_diff: list[str] = []
    if component["kind"] != "input":
        hover_path = component_dir / "hover.png"
        if use_direct_probe:
            box = locator.bounding_box(timeout=5000)
            if not box:
                raise RuntimeError("element has no bounding box for direct hover probe")
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            locator.hover(timeout=profile["hover_timeout_ms"], force=True)
        page.wait_for_timeout(profile["interaction_wait_ms"])
        hover_style = _safe_locator_evaluate(locator, _INTERACTION_STYLE_SCRIPT) or {}
        hover_diff = _diff_styles(before_style, hover_style)
        if not hover_diff:
            box = locator.bounding_box(timeout=5000)
            if box:
                terminal_log(
                    f"hover_no_diff: component {component['index'] + 1} '{component['label']}' primary hover; retrying with center-point hover",
                    settings.terminal_logs,
                )
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(profile["interaction_wait_ms"])
                hover_style = _safe_locator_evaluate(locator, _INTERACTION_STYLE_SCRIPT) or {}
                hover_diff = _diff_styles(before_style, hover_style)
            if not hover_diff:
                terminal_log(
                    f"hover_no_diff: component {component['index'] + 1} '{component['label']}'",
                    settings.terminal_logs,
                )
        if not settings.production:
            locator.screenshot(path=str(hover_path), timeout=5000)
            record["hover_image_path"] = str(hover_path)
        else:
            record["hover_image_path"] = None

    active_style = hover_style
    active_diff: list[str] = []
    if component["kind"] == "input":
        focus_path = component_dir / "focus.png"
        if use_direct_probe:
            box = locator.bounding_box(timeout=5000)
            if not box:
                raise RuntimeError("element has no bounding box for direct focus probe")
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            locator.click(timeout=profile["hover_timeout_ms"])
        page.wait_for_timeout(profile["interaction_wait_ms"])
        active_style = _safe_locator_evaluate(locator, _INTERACTION_STYLE_SCRIPT) or {}
        active_diff = _diff_styles(before_style, active_style)
        if not settings.production:
            locator.screenshot(path=str(focus_path), timeout=5000)
            record["focus_image_path"] = str(focus_path)
        else:
            record["focus_image_path"] = None
    else:
        active_path = component_dir / "active.png"
        box = locator.bounding_box(timeout=5000)
        if not box:
            raise RuntimeError("element has no bounding box for active probe")
        probe_x = box["x"] + box["width"] / 2
        probe_y = box["y"] + box["height"] / 2
        page.mouse.move(probe_x, probe_y)
        page.mouse.down()
        page.wait_for_timeout(profile["active_wait_ms"])
        active_style = _safe_locator_evaluate(locator, _INTERACTION_STYLE_SCRIPT) or {}
        active_diff = _diff_styles(before_style, active_style)
        if not settings.production:
            locator.screenshot(path=str(active_path), timeout=5000)
            record["active_image_path"] = str(active_path)
        else:
            record["active_image_path"] = None
        # Release away from the element so probing does not complete a real click/navigation.
        page.mouse.move(max(2, probe_x - min(80, box["width"] / 2 + 20)), max(2, probe_y - min(80, box["height"] / 2 + 20)))
        page.mouse.up()
        page.wait_for_timeout(profile["interaction_wait_ms"])
        record["active_image_path"] = str(active_path)

    _safe_locator_evaluate(
        locator,
        """(el, payload) => {
            el.setAttribute('data-humanonn-hover-effect', payload.hoverChanged ? 'true' : 'false');
            el.setAttribute('data-humanonn-hover-diff', payload.hoverDiff.join('|'));
            el.setAttribute('data-humanonn-active-effect', payload.activeChanged ? 'true' : 'false');
            el.setAttribute('data-humanonn-active-diff', payload.activeDiff.join('|'));
        }""",
        {
            "hoverChanged": bool(hover_diff),
            "hoverDiff": hover_diff,
            "activeChanged": bool(active_diff),
            "activeDiff": active_diff,
        },
    )

    record["hover_changed"] = bool(hover_diff)
    record["hover_diff"] = hover_diff
    record["active_changed"] = bool(active_diff)
    record["active_diff"] = active_diff
    record["before_style"] = before_style
    record["hover_style"] = hover_style
    record["active_style"] = active_style


def _scroll_locator_into_focus(page: Page, locator: Locator, settings: Settings, profile: dict[str, Any] | None = None) -> None:
    _safe_locator_evaluate(locator, "(el) => el.scrollIntoView({ block: 'center', inline: 'nearest' })")
    _settle_page(page, settings, "scroll-into-view", wait_ms=(profile or {}).get("render_wait_ms"))
    _wait_for_locator_stable(page, locator, settings, profile)


def _probe_directly_at_current_position(page: Page, locator: Locator, settings: Settings, profile: dict[str, Any] | None = None) -> None:
    # For fixed/sticky elements, avoid scroll-induced layout churn and stability waits.
    _settle_page(page, settings, "direct-probe", wait_ms=(profile or {}).get("render_wait_ms"))


def _retarget_section_locator(page: Page, section: dict[str, Any]) -> Locator:
    page.evaluate(
        """
        (payload) => {
            const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ");
            const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                if (cs.display === "none" || cs.visibility === "hidden") return false;
                if (Number.parseFloat(cs.opacity || "1") < 0.05) return false;
                if (rect.width < 280 || rect.height < 120) return false;
                return true;
            };
            const normalize = (value) => String(value || "").toLowerCase().replace(/\\s+/g, " ").trim();
            const labelTokens = normalize(payload.label).split(" ").filter(Boolean).slice(0, 8);
            const nodes = [...document.querySelectorAll("main section, section, article, [data-section], footer, main > div")];
            const candidates = nodes.filter(isVisible);
            const score = (el) => {
                const rect = el.getBoundingClientRect();
                const absTop = rect.top + window.scrollY;
                const labelNode = el.querySelector("h1,h2,h3,[aria-label],[data-section-title]");
                const label = normalize(
                    el.getAttribute("aria-label") ||
                    el.getAttribute("data-section") ||
                    (labelNode ? textOf(labelNode) : "") ||
                    textOf(el).slice(0, 80)
                );
                let tokenHits = 0;
                for (const token of labelTokens) {
                    if (token && label.includes(token)) tokenHits += 1;
                }
                const topDelta = Math.abs(absTop - payload.top);
                const widthDelta = Math.abs(rect.width - payload.width);
                const heightDelta = Math.abs(rect.height - payload.height);
                return [
                    tokenHits,
                    topDelta <= 200 ? 1 : 0,
                    -topDelta,
                    -widthDelta,
                    -heightDelta
                ];
            };
            candidates.sort((a, b) => {
                const sa = score(a);
                const sb = score(b);
                for (let i = 0; i < sa.length; i += 1) {
                    if (sa[i] !== sb[i]) return sb[i] - sa[i];
                }
                return 0;
            });
            const winner = candidates[0];
            if (!winner) return;
            document.querySelectorAll(`[data-humanonn-section-id="${payload.id}"]`).forEach((node) => {
                if (node !== winner) node.removeAttribute("data-humanonn-section-id");
            });
            winner.setAttribute("data-humanonn-section-id", payload.id);
        }
        """,
        section,
    )
    return page.locator(f"[data-humanonn-section-id='{section['id']}']").first


def _retarget_direct_probe_locator(page: Page, component: dict[str, Any]) -> Locator:
    payload = {
        "id": component["id"],
        "kind": component["kind"],
        "label": component.get("label", ""),
        "href": component.get("href", ""),
    }
    page.evaluate(
        """
        (payload) => {
            const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ");
            const effectivePositionFor = (el) => {
                let node = el;
                while (node && node !== document.documentElement) {
                    const position = getComputedStyle(node).position;
                    if (position === "fixed" || position === "sticky") return position;
                    node = node.parentElement;
                }
                return getComputedStyle(el).position;
            };
            const matchesTarget = (el) => {
                if (payload.kind === "link") {
                    const href = new URL(el.href, location.href).pathname.replace(/\\/+$/, "") || "/";
                    if (payload.href && href !== payload.href) return false;
                }
                const label = (el.getAttribute("aria-label") || textOf(el) || "").trim();
                return label === payload.label;
            };
            const score = (el) => {
                const rect = el.getBoundingClientRect();
                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;
                const inViewport = rect.width >= 20 && rect.height >= 20 && rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
                const centerInside = centerX >= 0 && centerY >= 0 && centerX <= window.innerWidth && centerY <= window.innerHeight;
                const topElement = centerInside ? document.elementFromPoint(centerX, centerY) : null;
                const ownsHit = Boolean(topElement && (topElement === el || el.contains(topElement) || topElement.contains(el)));
                const effectivePosition = effectivePositionFor(el);
                return [
                    ownsHit ? 1 : 0,
                    (effectivePosition === "fixed" || effectivePosition === "sticky") ? 1 : 0,
                    inViewport ? 1 : 0,
                    Math.round(rect.width * rect.height)
                ];
            };
            const selectors = payload.kind === "link" ? "a[href]" : "button,[role='button'],input[type='button'],input[type='submit']";
            const matches = [...document.querySelectorAll(selectors)].filter(matchesTarget);
            matches.sort((a, b) => {
                const sa = score(a);
                const sb = score(b);
                for (let i = 0; i < sa.length; i += 1) {
                    if (sa[i] !== sb[i]) return sb[i] - sa[i];
                }
                return 0;
            });
            const winner = matches[0];
            if (!winner) return;
            document.querySelectorAll(`[data-humanonn-component-id="${payload.id}"]`).forEach((node) => {
                if (node !== winner) node.removeAttribute("data-humanonn-component-id");
            });
            winner.setAttribute("data-humanonn-component-id", payload.id);
        }
        """,
        payload,
    )
    return page.locator(f"[data-humanonn-component-id='{component['id']}']").first


def _component_position(locator: Locator) -> str | None:
    return _safe_locator_evaluate(
        locator,
        """(el) => {
            let node = el;
            while (node && node !== document.documentElement) {
                const position = getComputedStyle(node).position;
                if (position === 'fixed' || position === 'sticky') {
                    return position;
                }
                node = node.parentElement;
            }
            return getComputedStyle(el).position;
        }""",
    )


def _wait_for_locator_stable(
    page: Page,
    locator: Locator,
    settings: Settings,
    profile: dict[str, Any] | None = None,
) -> None:
    previous: dict[str, float] | None = None
    profile = profile or {}
    stability_checks = int(profile.get("stability_checks", settings.stability_checks))
    stability_wait_ms = int(profile.get("stability_wait_ms", settings.stability_wait_ms))
    for _ in range(stability_checks):
        try:
            box = locator.bounding_box(timeout=5000)
        except Exception:
            terminal_log("Locator bounding box stability check failed; proceeding with best-effort probe", settings.terminal_logs)
            return
        if not box:
            terminal_log("Locator bounding box stability check returned no box; proceeding with best-effort probe", settings.terminal_logs)
            return
        current = {key: float(box[key]) for key in ("x", "y", "width", "height")}
        if previous and all(abs(previous[key] - current[key]) <= 1.5 for key in current):
            return
        previous = current
        page.wait_for_timeout(stability_wait_ms)
    terminal_log("Locator did not fully stabilize before probe; proceeding with best-effort probe", settings.terminal_logs)


def _settle_page(page: Page, settings: Settings, phase: str, wait_ms: int | None = None) -> None:
    target_wait = wait_ms or settings.render_wait_ms
    terminal_log(f"Waiting for page settle: {phase}", settings.terminal_logs)
    page.wait_for_timeout(target_wait)


def _component_screen_state(locator: Locator, use_direct_probe: bool = False) -> dict[str, Any]:
    script = _DIRECT_PROBE_SCREEN_SCRIPT if use_direct_probe else _COMPONENT_SCREEN_SCRIPT
    result = _safe_locator_evaluate(locator, script)
    if result is not None:
        return result
    try:
        box = locator.bounding_box(timeout=2000)
    except Exception:
        box = None
    if not box:
        return {"isConnected": True, "isVisible": False}
    return {
        "isConnected": True,
        "isVisible": True,
        "sizeOk": box["width"] >= 20 and box["height"] >= 20,
        "inViewport": True,
        "covered": False,
        "disabled": False,
        "pointerEvents": "auto",
        "clickable": True,
        "interactable": True,
        "width": round(box["width"]),
        "height": round(box["height"]),
    }


def _component_animation_count(locator: Locator) -> int:
    result = _safe_locator_evaluate(locator, "(el) => el.getAnimations().length")
    if isinstance(result, int):
        return result
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return 0


def _scan_metadata_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    sections = manifest.get("sections", []) if isinstance(manifest, dict) else []
    components = [
        component
        for section in sections
        for component in section.get("components", [])
        if isinstance(component, dict)
    ]
    total = len(components)
    verified = sum(1 for component in components if component.get("status") == "verified")
    style_verified = sum(1 for component in components if component.get("status") == "style_verified")
    synthetic = sum(1 for component in components if component.get("status") == "synthetic")
    unverified = sum(1 for component in components if component.get("status") == "unverified")
    checked = verified + style_verified
    interaction_coverage = checked / total if total else 0.0
    full_coverage = (checked + synthetic) / total if total else 0.0
    return {
        "section_count": len(sections),
        "component_count": total,
        "verified_components": verified,
        "style_verified_components": style_verified,
        "synthetic_components": synthetic,
        "unverified_components": unverified,
        "interaction_coverage_ratio": round(interaction_coverage, 4),
        "artifact_coverage_ratio": round(full_coverage, 4),
    }



def _safe_locator_evaluate(locator: Locator, script: str, args: Any = None, timeout_ms: int = 5000) -> Any:
    try:
        wrapped = f"""
        (el, payload) => Promise.race([
            (async () => {{ return ({script})(el, payload); }})(),
            new Promise(resolve => setTimeout(() => resolve(null), {timeout_ms}))
        ])
        """
        return locator.evaluate(wrapped, args, timeout=timeout_ms + 1000)
    except Exception:
        return None


def _component_readiness_reason(component: dict[str, Any], screen: dict[str, Any]) -> str | None:
    if not screen.get("isConnected", True):
        return "element disconnected"
    if not screen.get("isVisible", False):
        return "element not visible"
    if not screen.get("sizeOk", False):
        return "element size is too small"
    if not screen.get("inViewport", False):
        return "element is offscreen"
    if screen.get("disabled", False):
        return "element is disabled"
    if not screen.get("interactable", False):
        return "element is not interactable"
    if screen.get("covered", False):
        return "element is covered by another layer"
    if component["kind"] in {"button", "link", "input"} and not screen.get("clickable", False):
        return "element is not clickable"
    return None


def _build_pass_profiles(settings: Settings) -> list[dict[str, Any]]:
    def scale(multiplier: float, suffix: str) -> dict[str, Any]:
        return {
            "name": suffix,
            "render_wait_ms": int(settings.render_wait_ms * multiplier),
            "interaction_wait_ms": int(settings.interaction_wait_ms * multiplier),
            "active_wait_ms": int(settings.active_wait_ms * multiplier),
            "stability_wait_ms": int(settings.stability_wait_ms * multiplier),
            "hover_timeout_ms": int(settings.hover_timeout_ms * multiplier),
            "stability_checks": max(settings.stability_checks, int(round(settings.stability_checks * multiplier))),
        }

    return [
        scale(1.0, "pass_1"),
        scale(settings.pass2_wait_multiplier, "pass_2"),
        scale(settings.pass3_wait_multiplier, "pass_3"),
    ]


def _diff_styles(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [key for key in before if str(before.get(key)) != str(after.get(key))]


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text[:40] or "item"


def _summarize_error(exc: Exception) -> str:
    return str(exc).splitlines()[0].strip()


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
    active_button_count = sum(1 for item in data.get("buttons", []) if item.get("hasActiveProbeEffect"))
    terminal_log(
        f"Interaction effects recorded: buttons hover {hover_button_count}, links hover {hover_link_count}, "
        f"cards hover {hover_card_count}, buttons active {active_button_count}",
        settings.terminal_logs,
    )
    terminal_log(f"Artifacts stored in: {data.get('raw', {}).get('artifact_root')}", settings.terminal_logs)
    body = data.get("body", {})
    terminal_log(
        f"Body background: {body.get('backgroundColor', 'n/a')} | Hero background image present: "
        f"{bool(body.get('heroBackgroundImage') and body.get('heroBackgroundImage') != 'none')}",
        settings.terminal_logs,
    )


_DISCOVER_SECTIONS_SCRIPT = """
() => {
  const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ");
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    if (Number.parseFloat(cs.opacity || "1") < 0.05) return false;
    if (rect.width < 280 || rect.height < 120) return false;
    return true;
  };
  const nodes = [...document.querySelectorAll("main section, section, article, [data-section], footer, main > div")];
  const isWrapperOnly = (el) => (
    el.matches("main > div") &&
    Boolean(el.querySelector("section, article, [data-section], footer"))
  );
  const seen = new Set();
  const sections = [];
  for (const el of nodes) {
    if (isWrapperOnly(el)) continue;
    if (!isVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    const absTop = Math.round(rect.top + window.scrollY);
    const key = `${absTop}:${Math.round(rect.height)}:${Math.round(rect.width)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const labelNode = el.querySelector("h1,h2,h3,[aria-label],[data-section-title]");
    const label = (
      el.getAttribute("aria-label") ||
      el.getAttribute("data-section") ||
      (labelNode ? textOf(labelNode) : "") ||
      textOf(el).slice(0, 60) ||
      `section-${sections.length + 1}`
    ).slice(0, 80);
    const id = `section-${sections.length + 1}`;
    el.setAttribute("data-humanonn-section-id", id);
    sections.push({
      id,
      index: sections.length,
      label,
      top: absTop,
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    });
  }
  return sections;
}
"""


_DISCOVER_COMPONENTS_SCRIPT = """
(sectionId) => {
  const section = document.querySelector(`[data-humanonn-section-id="${sectionId}"]`);
  if (!section) return [];
  const allowFixedGlobalElements = sectionId === "section-1";

    const sectionRect = section.getBoundingClientRect();
    const intersectsSection = (rect) => (
        rect.right > sectionRect.left &&
        rect.left < sectionRect.right &&
        rect.bottom > sectionRect.top &&
        rect.top < sectionRect.bottom
    );

  const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ");
  const px = (value) => Number.parseFloat(String(value || "0")) || 0;
  const isElementNode = (node) => Boolean(node && node.nodeType === Node.ELEMENT_NODE);
  const visibleTextFromNode = (node) => {
    if (!isElementNode(node)) return "";
    return textOf(node);
  };
  const labelledByText = (el) => {
    const labelledBy = (el.getAttribute("aria-labelledby") || "").trim();
    if (!labelledBy) return "";
    return labelledBy
      .split(/\\s+/)
      .map((id) => {
        const ref = document.getElementById(id);
        return ref ? visibleTextFromNode(ref) : "";
      })
      .filter(Boolean)
      .join(" ")
      .trim();
  };
  const iconText = (el) => {
    const titleNode = el.querySelector("svg title, title");
    if (titleNode) return visibleTextFromNode(titleNode);
    const img = el.querySelector("img[alt]");
    if (img && img.getAttribute("alt")) return img.getAttribute("alt").trim();
    const srOnly = el.querySelector(".sr-only, .visually-hidden, [class*='screen-reader']");
    if (srOnly) return visibleTextFromNode(srOnly);
    return "";
  };
  const accessibleLabel = (el, kind = "") => {
    const ownText = textOf(el);
    const label = (
      el.getAttribute("aria-label") ||
      labelledByText(el) ||
      ownText ||
      el.getAttribute("title") ||
      el.getAttribute("placeholder") ||
      el.getAttribute("name") ||
      iconText(el) ||
      ""
    ).trim();
    if (label) return label.slice(0, 80);
    const href = el.tagName.toLowerCase() === "a" ? new URL(el.href, location.href).pathname.replace(/\\/+$/, "") || "/" : "";
    if (href && href !== "/") {
      const tail = href.split("/").filter(Boolean).pop() || "";
      if (tail) return tail.replace(/[-_]+/g, " ").slice(0, 80);
    }
    if (kind === "button" && el.querySelector("svg,[data-icon],img")) return "icon button";
    return kind || el.tagName.toLowerCase();
  };
  const styleOf = (el) => {
    const cs = getComputedStyle(el);
    return {
            position: cs.position,
      borderRadius: cs.borderRadius,
      boxShadow: cs.boxShadow,
      backdropFilter: cs.backdropFilter || cs.webkitBackdropFilter || "",
      borderTopWidth: cs.borderTopWidth,
      borderBottomWidth: cs.borderBottomWidth,
      cursor: cs.cursor,
      transitionDuration: cs.transitionDuration,
      transitionTimingFunction: cs.transitionTimingFunction
    };
  };
  const intersectsViewport = (rect) => (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < window.innerHeight &&
    rect.left < window.innerWidth
  );
  const isCenterPointOwnedByElement = (el, rect) => {
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    if (centerX < 0 || centerY < 0 || centerX > window.innerWidth || centerY > window.innerHeight) return false;
    const topElement = document.elementFromPoint(centerX, centerY);
    return Boolean(topElement && (topElement === el || el.contains(topElement) || topElement.contains(el)));
  };
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    if (Number.parseFloat(cs.opacity || "1") < 0.05) return false;
    if (rect.width < 20 || rect.height < 20) return false;
    if (el.hasAttribute("hidden") || el.getAttribute("aria-hidden") === "true" || el.hasAttribute("inert")) return false;
    let parent = el.parentElement;
    while (parent) {
      const parentStyle = getComputedStyle(parent);
      if (
        parent.hasAttribute("hidden") ||
        parent.hasAttribute("inert") ||
        parent.getAttribute("aria-hidden") === "true" ||
        parentStyle.display === "none" ||
        parentStyle.visibility === "hidden" ||
        Number.parseFloat(parentStyle.opacity || "1") < 0.05
      ) return false;
      parent = parent.parentElement;
    }
    return true;
  };
  const effectivePositionFor = (el) => {
    let node = el;
    while (node && node !== section) {
      const position = getComputedStyle(node).position;
      if (position === "fixed" || position === "sticky") return position;
      node = node.parentElement;
    }
    return getComputedStyle(el).position;
  };
  const isNavLike = (el) => {
    let node = el;
    while (node) {
      const tag = (node.tagName || "").toLowerCase();
      const role = (node.getAttribute && node.getAttribute("role") || "").toLowerCase();
      if (tag === "nav" || tag === "header" || role === "navigation") return true;
      node = node.parentElement;
    }
    return false;
  };
  const normalizedClass = (el) => String(el.className || "")
    .toLowerCase()
    .split(/\\s+/)
    .filter(Boolean)
    .map((token) => token.replace(/\\d+/g, "#"))
    .slice(0, 6)
    .join(".");
  const patternKeyFor = (kind, el, rect, label) => {
    const href = el.tagName.toLowerCase() === "a" ? new URL(el.href, location.href).pathname.replace(/\\/+$/, "") || "/" : "";
    const textSig = label.toLowerCase().replace(/\\s+/g, " ").slice(0, 32);
    const widthBucket = Math.round(rect.width / 40) * 40;
    const heightBucket = Math.round(rect.height / 20) * 20;
    const effectivePosition = effectivePositionFor(el);
    if (kind === "link" && (isNavLike(el) || effectivePosition === "fixed" || effectivePosition === "sticky")) {
      return ["nav-link", href, textSig].join("|");
    }
    if (kind === "button" && (isNavLike(el) || effectivePosition === "fixed" || effectivePosition === "sticky")) {
      return ["nav-button", textSig, el.getAttribute("aria-label") || "", href].join("|");
    }
    return [kind, el.tagName.toLowerCase(), normalizedClass(el), href, textSig, widthBucket, heightBucket].join("|");
  };
  const isPotentiallyClickable = (el) => {
    const cs = getComputedStyle(el);
    if (cs.pointerEvents === "none") return false;
    if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return false;
    if (["button", "input", "select", "textarea"].includes(el.tagName.toLowerCase())) return true;
    if (el.tagName.toLowerCase() === "a" && !!el.getAttribute("href")) return true;
    if ((el.getAttribute("role") || "").toLowerCase() === "button") return true;
    if (cs.cursor === "pointer") return true;
    if (typeof el.onclick === "function") return true;
    return false;
  };
  const hasActionableDescendant = (el) => Boolean(el.querySelector("a[href],button,[role='button'],input[type='button'],input[type='submit']"));
  const isCardLike = (el) => {
    const style = styleOf(el);
    const classText = `${el.className || ""}`.toLowerCase();
    const hasClass = ["card", "feature", "pricing", "testimonial", "plan", "tier"].some((token) => classText.includes(token));
    const elevated = style.boxShadow !== "none" || style.backdropFilter.includes("blur");
    const rounded = px(style.borderRadius) >= 12;
    const bordered = px(style.borderTopWidth) > 0 || px(style.borderBottomWidth) > 0;
    const hasContent = textOf(el).length >= 12 || Boolean(el.querySelector("img,svg,button,a"));
    return hasContent && (hasClass || elevated || rounded || bordered);
  };
  const hasCardAncestor = (el) => {
    let parent = el.parentElement;
    while (parent && parent !== section) {
      if (isCardLike(parent)) return true;
      parent = parent.parentElement;
    }
    return false;
  };
  const actionableDescendantCount = (el) => el.querySelectorAll("a[href],button,[role='button'],input[type='button'],input[type='submit']").length;
  const shouldSkipCardCandidate = (el) => {
    if (isNavLike(el)) return true;
    const actionableCount = actionableDescendantCount(el);
    if (actionableCount > 1) return true;
    const directActionableChild = [...el.children].some((child) => isElementNode(child) && child.matches("a[href],button,[role='button'],input[type='button'],input[type='submit']"));
    return directActionableChild;
  };
  const candidates = [];
  const seen = new Set();
  const seenPatterns = new Map();
  const push = (kind, el) => {
    if (!isVisible(el)) return;
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const effectivePosition = effectivePositionFor(el);
    if (!allowFixedGlobalElements && (effectivePosition === "fixed" || effectivePosition === "sticky" || isNavLike(el))) return;
    if ((isNavLike(el) || effectivePosition === "fixed" || effectivePosition === "sticky") && !intersectsViewport(rect)) return;
    if ((isNavLike(el) || effectivePosition === "fixed" || effectivePosition === "sticky") && !isCenterPointOwnedByElement(el, rect)) return;
    const label = accessibleLabel(el, kind);
    const key = `${kind}:${label}:${Math.round(rect.top)}:${Math.round(rect.left)}:${Math.round(rect.width)}:${Math.round(rect.height)}`;
    if (seen.has(key)) return;
    seen.add(key);
    const patternKey = patternKeyFor(kind, el, rect, label);
    const existingIndex = seenPatterns.get(patternKey);
    if (existingIndex !== undefined) {
      candidates[existingIndex].duplicateCount += 1;
      return;
    }
    const id = `${sectionId}-component-${candidates.length + 1}`;
    el.setAttribute("data-humanonn-component-id", id);
    el.setAttribute("data-humanonn-component-kind", kind);
    seenPatterns.set(patternKey, candidates.length);
    candidates.push({
      id,
      index: candidates.length,
      kind,
      label,
      text: textOf(el).slice(0, 200),
      href: el.tagName.toLowerCase() === "a" ? (new URL(el.href, location.href).pathname.replace(/\\/+$/, "") || "/") : "",
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      className: String(el.className || ""),
      styles: styleOf(el),
    position: cs.position,
    effectivePosition,
      patternKey,
      duplicateCount: 1,
      interactableHint: isPotentiallyClickable(el)
    });
  };

  [...section.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")].forEach((el) => push("button", el));
  [...section.querySelectorAll("a[href]")].forEach((el) => {
    const rect = el.getBoundingClientRect();
    if (textOf(el) || rect.width >= 32 || rect.height >= 32) push("link", el);
  });
  [...section.querySelectorAll("input, textarea, select")].forEach((el) => push("input", el));
  [...section.querySelectorAll("div, article, li, a")].forEach((el) => {
    if (el.closest("button")) return;
    if (hasCardAncestor(el)) return;
    if (shouldSkipCardCandidate(el)) return;
    if (isCardLike(el)) {
      push("card", el);
      return;
    }
    if (!isPotentiallyClickable(el) && !hasActionableDescendant(el)) return;
    if (isCardLike(el)) push("card", el);
  });

    // Some hero sections render their actionable children outside the section subtree
    // (for example via absolute-positioned overlays or framework portals). If the
    // subtree query returns nothing, do a geometry-based pass across the document and
    // keep only elements that overlap the section bounds.
    if (candidates.length === 0) {
        const fallbackSelectors = [
            "button",
            "[role='button']",
            "input[type='button']",
            "input[type='submit']",
            "a[href]",
            "input",
            "textarea",
            "select",
            "article",
            "li",
            "div"
        ];
        const fallbackNodes = [...new Set(fallbackSelectors.flatMap((selector) => [...document.querySelectorAll(selector)]))];
        for (const el of fallbackNodes) {
            if (section === el || section.contains(el)) continue;
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height) continue;
            if (!intersectsSection(rect)) continue;
            if (!isVisible(el)) continue;
            if (el.closest("button")) continue;
            const kind = el.tagName.toLowerCase() === "a" ? "link" : (el.tagName.toLowerCase() === "input" || el.tagName.toLowerCase() === "textarea" || el.tagName.toLowerCase() === "select") ? "input" : isPotentiallyClickable(el) ? "button" : isCardLike(el) ? "card" : null;
            if (!kind) continue;
            if (kind === "card" && shouldSkipCardCandidate(el)) continue;
            push(kind, el);
        }
    }

  return candidates.slice(0, 120);
}
"""


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
    componentId: el.getAttribute("data-humanonn-component-id") || "",
    componentKind: el.getAttribute("data-humanonn-component-kind") || "",
    sectionId: el.closest("[data-humanonn-section-id]")?.getAttribute("data-humanonn-section-id") || "",
    hasHoverEffect: el.getAttribute("data-humanonn-hover-effect") === "true",
    hoverDiff: (el.getAttribute("data-humanonn-hover-diff") || "").split("|").filter(Boolean),
    hasActiveProbeEffect: el.getAttribute("data-humanonn-active-effect") === "true",
    activeDiff: (el.getAttribute("data-humanonn-active-diff") || "").split("|").filter(Boolean),
    scrollIndex: el.closest("[data-humanonn-section-id]")?.getAttribute("data-humanonn-scroll-index") || ""
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
    const canvasElements = [...document.querySelectorAll("canvas")];
    const canvasRects = canvasElements
        .map((el) => el.getBoundingClientRect())
        .filter((rect) => rect.width > 0 && rect.height > 0);
    const heroCanvasBackground = Boolean(hero && hero.querySelector("canvas"));
    const heroWebGLBackground = heroCanvasBackground && canvasElements.some((el) => {
        try {
            return Boolean(el.getContext && (el.getContext("webgl") || el.getContext("experimental-webgl")));
        } catch (error) {
            return false;
        }
    });
    const canvasRenderedInteractiveCount = [...document.querySelectorAll("button, a, [role='button'], [data-humanonn-component-kind='card'], article")]
        .filter((el) => {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return false;
            return canvasRects.some((canvasRect) => (
                rect.left >= canvasRect.left &&
                rect.top >= canvasRect.top &&
                rect.right <= canvasRect.right &&
                rect.bottom <= canvasRect.bottom
            ));
        })
        .length;
    const dynamicStyleInjectionCount = [...document.querySelectorAll("[style]")]
        .filter((el) => {
            const style = (el.getAttribute("style") || "").toLowerCase();
            return /(gradient|blur|opacity)/.test(style);
        })
        .length;
  const body = {
    ...bodyStyle,
    heroBackgroundImage: hero ? getComputedStyle(hero).backgroundImage : "",
  };
  const buttons = [...document.querySelectorAll("button, a[role='button'], input[type='button'], input[type='submit']")]
    .slice(0, 120)
    .map((el) => ({
      ...compactStyle(el),
      ...humanonnMeta(el),
      disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
      hasActiveFeedback: hasActiveFeedback(el)
    }));
  const inputs = [...document.querySelectorAll("input, textarea, select")]
    .slice(0, 120)
    .map((el) => ({
      ...compactStyle(el),
      ...humanonnMeta(el),
      type: el.getAttribute("type") || el.tagName.toLowerCase(),
      placeholder: el.getAttribute("placeholder") || "",
      label: labelFor(el),
      hasVisibleFocus: hasVisibleFocus(el)
    }));
  const links = [...document.querySelectorAll("a")]
    .slice(0, 160)
    .map((el) => ({
      ...compactStyle(el),
      ...humanonnMeta(el),
      href: el.href,
      inNav: Boolean(el.closest("nav")),
      ariaCurrent: el.getAttribute("aria-current") || ""
    }));
  const headings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6,[class*='badge'],[class*='eyebrow']")]
    .slice(0, 160)
    .map((el) => ({ ...compactStyle(el), ...humanonnMeta(el), tagName: el.tagName.toLowerCase(), fontSizeRaw: el.getAttribute("style") || "" }));
  const sections = [...document.querySelectorAll("[data-humanonn-section-id], [data-humanonn-component-kind='card'], article, footer")]
    .slice(0, 160)
    .map((el) => {
      const s = compactStyle(el);
      const rect = el.getBoundingClientRect();
      const p = el.querySelector("p");
      return {
        ...s,
        ...humanonnMeta(el),
        id: el.getAttribute("data-humanonn-section-id") || "",
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        role: el.getAttribute("data-section") || el.getAttribute("aria-label") || "",
        looksCard: px(s.borderRadius) >= 8 || s.boxShadow !== "none" || s.backdropFilter.includes("blur"),
        hasLink: Boolean(el.querySelector("a,button")),
        paragraphMaxWidth: p ? Math.round(p.getBoundingClientRect().width) : 0
      };
    });
  const images = [...document.querySelectorAll("img")]
    .slice(0, 160)
    .map((el) => ({ src: el.currentSrc || el.src, alt: el.getAttribute("alt") || "", width: el.naturalWidth, height: el.naturalHeight }));
  const colorValues = [];
  [...document.querySelectorAll("body, main, section, button, a, h1, h2, h3, p")]
    .slice(0, 240)
    .forEach((el) => {
      const s = getComputedStyle(el);
      colorValues.push(s.color, s.backgroundColor, s.borderColor);
    });
    const fontSamples = [
        document.body,
        document.querySelector("h1, h2, h3"),
        document.querySelector("button, a, [role='button']")
    ].filter(Boolean);
    const fonts = [...new Set(fontSamples
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
    heroCanvasBackground,
    heroWebGLBackground,
    canvasRenderedInteractiveCount,
    dynamicStyleInjectionCount,
      scrollTriggeredExploration: true
    }
  };
}
"""


_INTERACTION_STYLE_SCRIPT = """
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


_SECTION_SYNTHETIC_STYLE_SCRIPT = """
(el) => {
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        backgroundColor: cs.backgroundColor,
        backgroundImage: cs.backgroundImage,
        borderRadius: cs.borderRadius,
        color: cs.color,
        fontFamily: cs.fontFamily,
        fontSize: cs.fontSize,
        transitionDuration: cs.transitionDuration,
        transitionTimingFunction: cs.transitionTimingFunction,
        boxShadow: cs.boxShadow,
        backdropFilter: cs.backdropFilter || cs.webkitBackdropFilter || ""
    };
}
"""


_COMPONENT_SCREEN_SCRIPT = """
(el) => {
  const rect = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;
  const withinViewport = (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < window.innerHeight &&
    rect.left < window.innerWidth
  );
  const centerInsideViewport = (
    centerX >= 0 &&
    centerY >= 0 &&
    centerX <= window.innerWidth &&
    centerY <= window.innerHeight
  );
  let covered = false;
  if (centerInsideViewport) {
    const topElement = document.elementFromPoint(centerX, centerY);
    covered = Boolean(topElement && topElement !== el && !el.contains(topElement) && !topElement.contains(el));
  }
  const disabled = Boolean(el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true");
  const clickable = !disabled && cs.pointerEvents !== "none" && (
    ["a", "button", "input", "select", "textarea"].includes(el.tagName.toLowerCase()) ||
    (el.getAttribute("role") || "").toLowerCase() === "button" ||
    cs.cursor === "pointer" ||
    typeof el.onclick === "function"
  );
  return {
    isConnected: el.isConnected,
    isVisible: cs.display !== "none" && cs.visibility !== "hidden" && Number.parseFloat(cs.opacity || "1") >= 0.05,
    sizeOk: rect.width >= 20 && rect.height >= 20,
    inViewport: withinViewport,
    covered,
    disabled,
    pointerEvents: cs.pointerEvents,
    clickable,
    interactable: cs.pointerEvents !== "none" && !disabled && withinViewport,
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  };
}
"""


_DIRECT_PROBE_SCREEN_SCRIPT = """
(el) => {
  const rect = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;
  const sizeOk = rect.width >= 20 && rect.height >= 20;
  const withinViewport = (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < window.innerHeight &&
    rect.left < window.innerWidth
  );
  const centerInsideViewport = (
    centerX >= 0 &&
    centerY >= 0 &&
    centerX <= window.innerWidth &&
    centerY <= window.innerHeight
  );
  const topElement = centerInsideViewport ? document.elementFromPoint(centerX, centerY) : null;
  const ownsHitTarget = Boolean(
    topElement && (topElement === el || el.contains(topElement) || topElement.contains(el))
  );
  const disabled = Boolean(el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true");
  const clickable = !disabled && cs.pointerEvents !== "none" && (
    ["a", "button", "input", "select", "textarea"].includes(el.tagName.toLowerCase()) ||
    (el.getAttribute("role") || "").toLowerCase() === "button" ||
    cs.cursor === "pointer" ||
    typeof el.onclick === "function"
  );
  return {
    isConnected: el.isConnected,
    isVisible: sizeOk && withinViewport && centerInsideViewport && ownsHitTarget,
    sizeOk,
    inViewport: withinViewport,
    covered: centerInsideViewport && !ownsHitTarget,
    disabled,
    pointerEvents: cs.pointerEvents,
    clickable,
    interactable: cs.pointerEvents !== "none" && !disabled && withinViewport && ownsHitTarget,
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  };
}
"""


def to_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
