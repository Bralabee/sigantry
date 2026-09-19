"""Plan 14-01 STARTER-03: starter BRANCHING.md presence + content invariants.

Five tests against ``templates/starter/docs/BRANCHING.md``:

1. <= 200 lines (D-18 size budget).
2. References ADR-0010 commercial-model.
3. References ADR-0011 rename-to-sigantry.
4. Uses 'trunk-based' wording (case-insensitive).
5. Documents `main -> DEV` / tag -> PREPROD / tag -> PROD env mapping.
"""

from __future__ import annotations

from pathlib import Path

_STARTER_DIR = Path(__file__).resolve().parents[2] / "templates" / "starter"
_BRANCHING = _STARTER_DIR / "docs" / "BRANCHING.md"


def test_branching_doc_at_most_200_lines() -> None:
    """templates/starter/docs/BRANCHING.md has at most 200 lines (D-18 size budget)."""
    body = _BRANCHING.read_text(encoding="utf-8")
    line_count = len(body.splitlines())
    assert line_count <= 200, (
        f"BRANCHING.md is {line_count} lines; D-18 caps at 200 to keep adopters reading it"
    )


def test_branching_doc_references_adr_0010() -> None:
    """BRANCHING.md cites ADR-0010 commercial-model (D-18)."""
    body = _BRANCHING.read_text(encoding="utf-8")
    assert "ADR-0010" in body, "BRANCHING.md must cite ADR-0010 commercial-model"


def test_branching_doc_references_adr_0011() -> None:
    """BRANCHING.md cites ADR-0011 rename-to-sigantry (D-18)."""
    body = _BRANCHING.read_text(encoding="utf-8")
    assert "ADR-0011" in body, "BRANCHING.md must cite ADR-0011 rename-to-sigantry"


def test_branching_doc_describes_trunk_based() -> None:
    """BRANCHING.md uses 'trunk-based' wording (D-18)."""
    body = _BRANCHING.read_text(encoding="utf-8").lower()
    assert "trunk-based" in body, "BRANCHING.md must describe a trunk-based flow"


def test_branching_doc_describes_env_to_branch_mapping() -> None:
    """BRANCHING.md documents main->dev / tag->preprod / tag->prod env mapping (D-18)."""
    body = _BRANCHING.read_text(encoding="utf-8")
    for env in ("DEV", "PREPROD", "PROD"):
        assert env in body, f"BRANCHING.md must mention env {env!r}"
