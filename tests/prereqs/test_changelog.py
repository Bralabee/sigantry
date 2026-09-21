"""CHANGELOG.md integrity - Keep a Changelog 1.1.0 shape plus HS2 Phase 0 content."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
VERSION_FILE = REPO_ROOT / "sigantry_core" / "_version.py"

_VERSION_RE = re.compile(r'^__version__\s*=\s*"(\d+\.\d+\.\d+[^"]*)"$', re.MULTILINE)


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


def test_has_section_for_current_version() -> None:
    """The CHANGELOG carries a section for whatever version _version.py declares.

    Derived from _version.py rather than restating a literal: a hardcoded
    version here is a second source of truth that goes red on a legitimate
    release bump instead of on a real defect.
    """
    assert VERSION_FILE.is_file(), f"_version.py is missing: {VERSION_FILE}"
    match = _VERSION_RE.search(VERSION_FILE.read_text(encoding="utf-8"))
    assert match is not None, "_version.py must declare __version__ as a quoted SemVer string"
    version = match.group(1)
    text = _read()
    assert f"## [{version}]" in text, (
        f"CHANGELOG.md must have a '## [{version}]' heading matching _version.py"
    )


def test_references_keep_a_changelog_and_semver() -> None:
    text = _read()
    assert "Keep a Changelog" in text
    assert "Semantic Versioning" in text
