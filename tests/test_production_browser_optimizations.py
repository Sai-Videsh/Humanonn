from unittest.mock import MagicMock, patch
from pathlib import Path
from dataclasses import replace
from humanonn.config import load_settings
from humanonn.tools.browser import _capture_components

def test_capture_components_caps_in_production():
    # Instantiate base settings and configure for production mode
    settings = replace(load_settings(), production=True)
    section = {"id": "section-1", "label": "Test Section", "index": 0}
    
    # 8 components
    components = [
        {"id": f"comp-{i}", "index": i, "kind": "button", "label": f"Button {i}"}
        for i in range(8)
    ]
    
    section_dir = Path("temp_section_dir")
    
    # Mock _capture_component_with_retries to just return a dummy record
    with patch("humanonn.tools.browser._capture_component_with_retries") as mock_capture:
        mock_capture.side_effect = lambda page, sect, comp, s_dir, sett, profs, timeouts: (
            {"id": comp["id"], "verified": True, "needs_vision_override": False},
            0
        )
        
        # Call the target function
        result = _capture_components(
            page=MagicMock(),
            section=section,
            components=components,
            section_dir=section_dir,
            settings=settings
        )
        
        # Verify first 5 components were processed by mock_capture
        assert mock_capture.call_count == 5
        
        # Verify we got 8 records back
        assert len(result) == 8
        
        # Verify the first 5 are marked verified (from mock)
        for r in result[:5]:
            assert r["verified"] is True
            
        # Verify the remaining 3 are marked with the limit exceeded reason
        for i, r in enumerate(result[5:]):
            assert r["status"] == "unverified"
            assert "limit exceeded" in r["unverified_reason"]


def test_capture_components_no_cap_in_development():
    # Configure for dev mode
    settings = replace(load_settings(), production=False)
    section = {"id": "section-1", "label": "Test Section", "index": 0}
    
    # 8 components
    components = [
        {"id": f"comp-{i}", "index": i, "kind": "button", "label": f"Button {i}"}
        for i in range(8)
    ]
    
    section_dir = Path("temp_section_dir")
    
    with patch("humanonn.tools.browser._capture_component_with_retries") as mock_capture:
        mock_capture.side_effect = lambda page, sect, comp, s_dir, sett, profs, timeouts: (
            {"id": comp["id"], "verified": True, "needs_vision_override": False},
            0
        )
        
        result = _capture_components(
            page=MagicMock(),
            section=section,
            components=components,
            section_dir=section_dir,
            settings=settings
        )
        
        # Verify all 8 components were processed without capping
        assert mock_capture.call_count == 8
        assert len(result) == 8
        for r in result:
            assert r["verified"] is True


def test_section_screenshot_skipping_in_production():
    settings = replace(load_settings(), production=True)
    sections = [
        {"id": f"sec-{i}", "label": f"Section {i}", "index": i}
        for i in range(5)
    ]
    artifact_root = Path("temp_artifact_root")
    
    mock_page = MagicMock()
    mock_locator = MagicMock()
    
    with patch("humanonn.tools.browser._retarget_section_locator", return_value=mock_locator), \
         patch("humanonn.tools.browser._scroll_locator_into_focus"), \
         patch("humanonn.tools.browser._discover_components", return_value=[]), \
         patch("humanonn.tools.browser._build_section_synthetic_component", return_value={"synthetic": True}):
        
        from humanonn.tools.browser import _capture_section_artifacts
        result = _capture_section_artifacts(
            page=mock_page,
            sections=sections,
            artifact_root=artifact_root,
            settings=settings
        )
        
        # In production, screenshot should be called only for index < 2 (which is 2 times)
        assert mock_locator.screenshot.call_count == 2
        
        # Verify section_image_path is set for the first two and None for the rest
        assert result["sections"][0]["section_image_path"] is not None
        assert result["sections"][1]["section_image_path"] is not None
        assert result["sections"][2]["section_image_path"] is None
        assert result["sections"][3]["section_image_path"] is None


def test_component_screenshot_skipping_in_production():
    settings = replace(load_settings(), production=True)
    mock_locator = MagicMock()
    component_dir = Path("temp_component_dir")
    record = {}
    
    from humanonn.tools.browser import _capture_static_component_style
    _capture_static_component_style(
        locator=mock_locator,
        component={"kind": "card", "label": "Card", "index": 0},
        component_dir=component_dir,
        record=record,
        settings=settings
    )
    
    # In production, element screenshot should not be taken
    assert mock_locator.screenshot.call_count == 0
    assert record["before_image_path"] is None

