"""Plan 15-04 / DEMO-04 -- docs/demo/QUICKSTART.md size + structure invariants.

Plan 15-04 ships docs/demo/QUICKSTART.md (≤300 lines per CONTEXT D-15)
with 6 numbered Steps + Troubleshooting + sigantry CLI references.
Wave 0 xfail stubs flipped to real assertions here.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_QUICKSTART = _REPO / "docs" / "demo" / "QUICKSTART.md"


def test_quickstart_at_most_300_lines() -> None:
    """docs/demo/QUICKSTART.md is at most 300 lines (CONTEXT D-15)."""
    assert _QUICKSTART.is_file(), _QUICKSTART
    line_count = len(_QUICKSTART.read_text(encoding="utf-8").splitlines())
    assert line_count <= 300, f"QUICKSTART is {line_count} lines (CONTEXT D-15 caps at 300)"


def test_quickstart_has_required_section_headings() -> None:
    """docs/demo/QUICKSTART.md contains all required top-level headings."""
    text = _QUICKSTART.read_text(encoding="utf-8")
    required = [
        "Prerequisites",
        "Step 1",
        "Step 2",
        "Step 3",
        "Step 4",
        "Step 5",
        "Step 6",
        "Troubleshooting",
    ]
    for heading in required:
        assert heading in text, f"QUICKSTART missing heading {heading!r}"


def test_quickstart_references_sigantry_deploy_command() -> None:
    """docs/demo/QUICKSTART.md references the sigantry CLI commands the demo CI runs."""
    text = _QUICKSTART.read_text(encoding="utf-8")
    required = [
        "sigantry config validate",
        "sigantry sync apply",
        "sigantry diff",
        "pip install",  # the install instruction
        "sigantry",  # the distribution name (NOT sigantry-core: that 404s on PyPI)
        "SIGANTRY_DEMO_TENANT_ID",
        "SIGANTRY_DEMO_WORKSPACE_ID",
        "SIGANTRY_DEMO_CAPACITY_ID",
        "SIGANTRY_DEMO_FABRIC_TOKEN",
    ]
    for token in required:
        assert token in text, f"QUICKSTART missing reference {token!r}"
