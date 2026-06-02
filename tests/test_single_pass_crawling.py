from unittest.mock import patch, MagicMock
from humanonn.config import load_settings
from humanonn.tools.registry import ToolRegistry
from humanonn.models import AuditSnapshot

def test_registry_reuses_existing_crawl_snapshot():
    settings = load_settings()
    registry = ToolRegistry(settings)
    
    dummy_snapshot = AuditSnapshot(
        url="https://example.com/",
        title="Example",
        screenshot_path="/tmp/main.png",
        colors={},
        fonts=[],
        body={},
        buttons=[],
        inputs=[],
        links=[],
        headings=[],
        sections=[],
        images=[],
        text={}
    )
    
    with patch("humanonn.tools.registry.crawl_page") as mock_crawl:
        mock_crawl.return_value = dummy_snapshot
        
        # First crawl
        res1 = registry.execute("crawl_page", {"url": "https://example.com"})
        
        # Second crawl for same URL (with trailing slash/whitespace differences)
        res2 = registry.execute("crawl_page", {"url": " https://example.com/ "})
        
        # Third crawl for different URL
        other_snapshot = AuditSnapshot(
            url="https://other.com",
            title="Other",
            screenshot_path="/tmp/other.png",
            colors={},
            fonts=[],
            body={},
            buttons=[],
            inputs=[],
            links=[],
            headings=[],
            sections=[],
            images=[],
            text={}
        )
        mock_crawl.return_value = other_snapshot
        res3 = registry.execute("crawl_page", {"url": "https://other.com"})
        
        # Verify crawl_page was called exactly twice (first and third, second was reused)
        assert mock_crawl.call_count == 2
        # First call uses target_url as passed
        mock_crawl.assert_any_call("https://example.com", settings)
        mock_crawl.assert_any_call("https://other.com", settings)
