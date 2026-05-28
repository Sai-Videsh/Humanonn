from humanonn.models import AuditSnapshot
from humanonn import rules
from humanonn.signals import SIGNAL_BY_ID


def test_synthetic_section_triggers_glassmorphism():
    # Build a minimal snapshot with a synthetic section that includes backdropFilter blur
    snapshot = AuditSnapshot(
        url="https://example.test",
        title="Test",
        screenshot_path=None,
        colors={"all": ["rgb(255,255,255)"]},
        fonts=["Inter"],
        body={},
        buttons=[],
        inputs=[],
        links=[],
        headings=[],
        sections=[
            {
                "id": "section-1",
                "text": "Synthetic",
                "className": "synthetic-section",
                "backdropFilter": "blur(6px)",
                "boxShadow": "none",
                "backgroundImage": "",
                "width": 800,
                "height": 400,
                "looksCard": True,
            }
        ],
        images=[],
        text={"visible": ""},
        raw={},
    )

    signal = SIGNAL_BY_ID.get("glassmorphism")
    finding = rules.glassmorphism(snapshot, signal)
    assert finding.flagged is True
    assert finding.confidence >= 0.7
