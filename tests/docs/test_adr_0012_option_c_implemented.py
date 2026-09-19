"""Phase 17 docs-shape test: ADR-0012 Option C marked Implemented.

Pins the Phase 17 amendment to ADR-0012:
- The Alternatives table Option C row carries the substring
  "Implemented in Phase 17" (closes the original "Defer-to-v3.x"
  language).
- The amendment cross-references ADR-0013 (the new ADR codifying
  the parameters.yml resolution rule).
- The original ADR-0012 Status row remains "Accepted" -- Phase 17
  amends, it does not supersede.

A regression where ADR-0012 reverts the amendment fails CI immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADR_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "decisions"
    / "ADR-0012-sync-apply-vs-deploy-run-boundary.md"
)


@pytest.fixture(scope="module")
def adr_text() -> str:
    assert _ADR_PATH.is_file(), f"ADR-0012 missing at {_ADR_PATH}"
    return _ADR_PATH.read_text(encoding="utf-8")


def test_adr_0012_marks_option_c_implemented(adr_text: str) -> None:
    assert "Implemented in Phase 17" in adr_text, (
        "ADR-0012 must mark Option C 'Implemented in Phase 17' (Phase 17 "
        "amendment per .planning/phases/17-.../17-03-PLAN.md). The original "
        "'Defer-to-v3.x candidate' language was rewritten in place."
    )


def test_adr_0012_cross_references_adr_0013(adr_text: str) -> None:
    assert "ADR-0013" in adr_text, (
        "ADR-0012 amendment must cross-reference ADR-0013 (the new ADR "
        "codifying the parameters.yml resolution rule)."
    )


def test_adr_0012_status_row_still_accepted(adr_text: str) -> None:
    """Phase 17 amends ADR-0012, it does not re-decide it.

    Allow either '**Status:** Accepted' (markdown bold) or 'Status: Accepted'
    (plain). The locked invariant is that ADR-0012 stays Accepted.
    """
    assert re.search(r"Status:\**\s*Accepted", adr_text), (
        "ADR-0012 Status row must still read 'Accepted' -- Phase 17 amends "
        "the Alternatives table cell + Consequences bullets, it does not "
        "supersede the decision."
    )
