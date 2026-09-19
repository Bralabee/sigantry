"""CHANGELOG.md integrity - Keep a Changelog 1.1.0 shape plus HS2 Phase 0 content."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _read() -> str:
    assert CHANGELOG.is_file(), "CHANGELOG.md is missing from repo root"
    return CHANGELOG.read_text(encoding="utf-8")


def test_has_h1_changelog_heading() -> None:
    text = _read()
    assert any(ln.strip() == "# Changelog" for ln in text.splitlines()), (
        "CHANGELOG.md must have the H1 heading '# Changelog'"
    )


def test_has_unreleased_section() -> None:
    text = _read()
    assert "## [Unreleased]" in text, "CHANGELOG.md must have '## [Unreleased]' heading"


def test_has_100_section() -> None:
    text = _read()
    assert "## [1.0.0]" in text, (
        "CHANGELOG.md must have '## [1.0.0]' heading (initial open-source release)"
    )


def test_references_keep_a_changelog_and_semver() -> None:
    text = _read()
    assert "Keep a Changelog" in text
    assert "Semantic Versioning" in text
