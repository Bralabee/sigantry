"""Version alignment invariant across _version.py, CHANGELOG, and docs.

Research D-14 / Phase 7 Plan 07-03 DOCS-05:
    The fabric-dataops version string lives in exactly three places that
    must stay aligned at all times:

      1. sigantry_core/_version.py :: __version__
      2. CHANGELOG.md :: most-recent dated release heading
         ('## [X.Y.Z] - YYYY-MM-DD')
      3. docs/index.md :: version banner ('vX.Y.Z')

    pyproject.toml carries dynamic=["version"] (Hatchling resolves from
    _version.py at build time) so there is no literal version string in
    pyproject.toml to check.

This test is the Phase 7 Plan 07-03 closure gate; the release-process
runbook points at this test as the regression gate for every future
version bump.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "sigantry_core" / "_version.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
DOCS_INDEX = REPO_ROOT / "docs" / "index.md"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[a-z]+\d*)?$")


def _read_package_version() -> str:
    assert VERSION_FILE.is_file(), f"_version.py is missing: {VERSION_FILE}"
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    assert match is not None, "_version.py must define __version__ as a quoted string"
    return match.group(1)


def _read_latest_changelog_version() -> str:
    assert CHANGELOG.is_file(), f"CHANGELOG.md is missing: {CHANGELOG}"
    text = CHANGELOG.read_text(encoding="utf-8")
    # Find the FIRST dated release heading below `## [Unreleased]`. The
    # dated-heading shape is `## [X.Y.Z] - YYYY-MM-DD`. The regex is
    # anchored to the line start to avoid false matches inside bullets.
    pattern = re.compile(
        r"^##\s*\[(\d+\.\d+\.\d+(?:[a-z]+\d*)?)\]\s*-\s*\d{4}-\d{2}-\d{2}\s*$",
        re.MULTILINE,
    )
    matches = pattern.findall(text)
    assert matches, (
        "CHANGELOG.md must have at least one dated release heading "
        "(shape: '## [X.Y.Z] - YYYY-MM-DD')"
    )
    return matches[0]


def _read_docs_index_version() -> str:
    assert DOCS_INDEX.is_file(), f"docs/index.md is missing: {DOCS_INDEX}"
    text = DOCS_INDEX.read_text(encoding="utf-8")
    # Version banner shape: 'vX.Y.Z (...)' at the start of the
    # `## Current release` body line. Use a liberal regex so minor
    # typographic edits to the banner do not break the test.
    match = re.search(r"v(\d+\.\d+\.\d+(?:[a-z]+\d*)?)\b", text)
    assert match is not None, "docs/index.md must include a 'vX.Y.Z' version banner"
    return match.group(1)


def test_package_version_is_valid_semver() -> None:
    version = _read_package_version()
    assert SEMVER_RE.match(version), (
        f"sigantry_core/_version.py __version__={version!r} is not MAJOR.MINOR.PATCH SemVer"
    )


def test_changelog_latest_version_is_valid_semver() -> None:
    version = _read_latest_changelog_version()
    assert SEMVER_RE.match(version), (
        f"CHANGELOG.md most-recent dated heading version {version!r} is not valid SemVer"
    )


def test_package_version_matches_changelog_latest_heading() -> None:
    pkg = _read_package_version()
    changelog = _read_latest_changelog_version()
    assert pkg == changelog, (
        f"Version drift: sigantry_core/_version.py reports {pkg!r} but CHANGELOG.md "
        f"most-recent dated release heading is [{changelog}]. "
        "See docs/release-process.md for the bump checklist."
    )


def test_package_version_matches_docs_index_banner() -> None:
    pkg = _read_package_version()
    docs = _read_docs_index_version()
    assert pkg == docs, (
        f"Version drift: sigantry_core/_version.py reports {pkg!r} but docs/index.md "
        f"version banner is 'v{docs}'. "
        "See docs/release-process.md for the bump checklist."
    )


def test_changelog_has_unreleased_section_above_latest_dated_heading() -> None:
    """Keep-a-Changelog shape: `## [Unreleased]` MUST precede the newest dated heading."""
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased_idx = text.find("## [Unreleased]")
    assert unreleased_idx != -1, "CHANGELOG.md is missing the '## [Unreleased]' heading"
    latest = _read_latest_changelog_version()
    dated_marker = f"## [{latest}]"
    dated_idx = text.find(dated_marker)
    assert dated_idx != -1, f"Could not locate CHANGELOG dated heading for latest={latest}"
    assert unreleased_idx < dated_idx, (
        "CHANGELOG.md: '## [Unreleased]' must appear BEFORE the most-recent "
        f"dated release heading '{dated_marker}'"
    )
