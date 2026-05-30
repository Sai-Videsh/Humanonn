from humanonn import source_code
from humanonn.model_routing import route_for


def test_ats_review_route_has_free_tier_fallbacks() -> None:
    candidates = route_for("ats_review")

    assert [candidate.provider for candidate in candidates[:4]] == ["groq", "groq", "groq", "groq"]
    assert [candidate.provider for candidate in candidates[4:7]] == ["openrouter", "openrouter", "openrouter"]
    assert [candidate.provider for candidate in candidates[7:]] == ["huggingface", "huggingface", "huggingface"]


def test_select_repo_files_for_scan_keeps_repo_wide_files() -> None:
    entries = [
        {"type": "blob", "path": "README.md", "sha": "1", "size": 1200},
        {"type": "blob", "path": "src/app.tsx", "sha": "2", "size": 2400},
        {"type": "blob", "path": "assets/diagram.png", "sha": "3", "size": 2_000_000},
        {"type": "blob", "path": "assets/trailer.mp4", "sha": "4", "size": 900 * 1024 * 1024},
        {"type": "blob", "path": "archives/full-backup.zip", "sha": "5", "size": 2_000_000_000},
        {"type": "tree", "path": "src", "sha": "6"},
    ]

    file_entries, skipped_entries = source_code._select_repo_files_for_scan(
        owner="example",
        repo="repo",
        default_branch="main",
        entries=entries,
    )

    assert [entry["path"] for entry in file_entries] == ["README.md", "src/app.tsx", "assets/diagram.png"]
    assert [entry["path"] for entry in skipped_entries] == ["assets/trailer.mp4", "archives/full-backup.zip"]


def test_raw_github_file_url_preserves_nested_paths() -> None:
    url = source_code._raw_github_file_url("owner", "repo", "main", "docs/Release Notes/overview.md")

    assert url == "https://raw.githubusercontent.com/owner/repo/main/docs/Release%20Notes/overview.md"


def test_format_repo_file_structure_renders_tree_once() -> None:
    structure = source_code._format_repo_file_structure([
        "README.md",
        "src/app.tsx",
        "src/components/button.tsx",
        "docs/guides/setup.md",
    ])

    assert "README.md" in structure
    assert "src/" in structure
    assert "components/" in structure
    assert "button.tsx" in structure
    assert structure.count("README.md") == 1


def test_apply_source_ats_reviews_updates_borderline_source_findings() -> None:
    findings = [
        {
            "id": "missing_readme",
            "flagged": True,
            "confidence": 0.6,
            "points": 6,
            "reason": "No README.md found in repository.",
            "fix": "Add a project README.",
            "evidence": {},
        },
        {
            "id": "missing_gitignore",
            "flagged": True,
            "confidence": 0.95,
            "points": 3,
            "reason": "No .gitignore file found in repository.",
            "fix": "Add a .gitignore file.",
            "evidence": {},
        },
    ]
    source_report = {
        "repo_url": "https://github.com/example/repo",
        "owner": "example",
        "repo": "repo",
        "branch": "main",
        "files_scanned": 1,
        "files_skipped": 0,
        "fetched_file_structure": "`-- README.md",
        "scan_log": [],
        "findings": findings,
    }

    class DummySettings:
        force_no_llm = False

        def api_keys_for(self, provider: str) -> list[str]:
            return []

    notes = source_code._apply_source_ats_review(source_report, DummySettings())

    assert notes == []
    assert source_report["findings"][0]["flagged"] is True
    assert source_report["findings"][0]["confidence"] == 0.6