from playwright.sync_api import sync_playwright

from humanonn.tools.browser import _DISCOVER_COMPONENTS_SCRIPT, _is_noise_request


def test_noise_request_blocklist_matches_expected_domains():
    assert _is_noise_request("https://browser.sentry-cdn.com/script.js")
    assert _is_noise_request("https://www.google-analytics.com/g/collect?v=2")
    assert _is_noise_request("https://www.googletagmanager.com/gtag/js?id=G-TEST")
    assert _is_noise_request("https://cdn.growthbook.io/sdk.js")
    assert _is_noise_request("https://analytics.tiktok.com/i18n/pixel/events.js")
    assert not _is_noise_request("https://bolt.new/")


def test_component_discovery_dedupes_nav_and_skips_parent_container():
    html = """
    <html>
      <body>
        <section data-humanonn-section-id="section-1" style="width: 1200px; min-height: 500px;">
          <nav aria-label="Primary" style="position: sticky; top: 0;">
            <div class="desktop-nav">
              <a href="/community">Community</a>
              <a href="/enterprise">Enterprise</a>
            </div>
            <div class="mobile-nav" style="transform: translateX(-9999px); position: absolute;">
              <a href="/community">Community</a>
              <a href="/enterprise">Enterprise</a>
            </div>
            <button aria-label="Open menu"><svg><title>Open menu</title></svg></button>
          </nav>
          <div id="hero-copy" style="margin-top: 80px;">Build something fast</div>
        </section>
      </body>
    </html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(html)
        components = page.evaluate(_DISCOVER_COMPONENTS_SCRIPT, "section-1")
        browser.close()

    link_labels = [component["label"] for component in components if component["kind"] == "link"]
    assert link_labels.count("Community") == 1
    assert link_labels.count("Enterprise") == 1

    community = next(component for component in components if component["kind"] == "link" and component["label"] == "Community")
    enterprise = next(component for component in components if component["kind"] == "link" and component["label"] == "Enterprise")
    assert community["duplicateCount"] == 2
    assert enterprise["duplicateCount"] == 2
    assert community["effectivePosition"] == "sticky"

    button_labels = [component["label"] for component in components if component["kind"] == "button"]
    assert "Open menu" in button_labels
    assert "button" not in button_labels

    card_texts = [component["text"] for component in components if component["kind"] == "card"]
    assert not any("Community Enterprise" in text for text in card_texts)
