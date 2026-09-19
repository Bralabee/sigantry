"""Plan 15-02 / DEMO-03 / D-10 -- scripts/remotion/script.md size + structure.

Wave 0 stamped three xfail stubs; Plan 15-02 lands script.md (<=200
lines per CONTEXT D-10) covering the WI -> deploy -> tests -> audit
-> rollback round-trip narration with 5 H2 scene headings.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "remotion" / "script.md"


def test_script_md_at_most_200_lines() -> None:
    """scripts/remotion/script.md is at most 200 lines (CONTEXT D-10)."""
    text = _SCRIPT.read_text()
    line_count = text.count("\n")
    assert line_count <= 200, f"script.md is {line_count} lines (CONTEXT D-10 caps at 200)"


def test_script_md_has_5_scene_headings() -> None:
    """script.md has 5 scene headings: WI link / Deploy / Tests / Audit / Rollback."""
    text = _SCRIPT.read_text()
    for n in (1, 2, 3, 4, 5):
        assert f"## Scene {n}" in text, f"missing '## Scene {n}' heading"


def test_script_md_covers_round_trip_steps() -> None:
    """script.md narration references all round-trip steps from ROADMAP success criterion 3."""
    lower = _SCRIPT.read_text().lower()
    for keyword in ("work item", "deploy", "test", "audit", "rollback"):
        assert keyword in lower, f"script.md missing keyword '{keyword}'"
