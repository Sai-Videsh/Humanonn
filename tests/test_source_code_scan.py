from humanonn import source_code


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