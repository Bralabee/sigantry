"""Phase 17 docs-shape test: ADR-0013 exists with the locked rule.

Pins the new ADR file:
- File exists at docs/decisions/ADR-0013-sync-publish-parameters-resolution.md.
- Status row reads "Accepted".
- Decision section embeds the bare phrase "--with-publish requires --params"
  (the locked rule from D-17-01) so future docs-shape regressions catch a
  silent rewording.
- Decision section embeds the multi-env requirement (D-17-02) referencing
  the `environments_seen` detection rule OR the bare phrase
  "multi-env parameters.yml requires --environment".
- Cross-references ADR-0012 (the deferring decision Phase 17 closes).

A regression where ADR-0013 is deleted, renamed, or has its locked-rule
phrasing rewritten fails CI immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADR_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "decisions"
    / "ADR-0013-sync-publish-parameters-resolution.md"
)


@pytest.fixture(scope="module")
def adr_text() -> str:
    assert _ADR_PATH.is_file(), f"ADR-0013 missing at {_ADR_PATH}"
    return _ADR_PATH.read_text(encoding="utf-8")


def test_adr_0013_exists_with_locked_rule(adr_text: str) -> None:
    # File existence is enforced by the fixture's assertion above; if it
    # is missing, pytest never reaches this test body.
    assert adr_text, "ADR-0013 read produced empty content"


def test_adr_0013_status_accepted(adr_text: str) -> None:
    """Allow either '**Status:** Accepted' (markdown bold) or 'Status: Accepted'."""
    assert re.search(r"Status:\**\s*Accepted", adr_text), (
        "ADR-0013 Status row must read 'Accepted'."
    )


def test_adr_0013_pins_hard_fail_rule(adr_text: str) -> None:
    """D-17-01: bare phrase '--with-publish requires --params' must appear.

    The phrasing is load-bearing: operators searching their shell history /
    CI logs grep for this literal. Future docs drift that rewords it (e.g.
    'requires the --params flag') breaks the search affordance.
    """
    assert "--with-publish requires --params" in adr_text, (
        "ADR-0013 must contain the bare phrase '--with-publish requires "
        "--params' (D-17-01 locked rule -- see 17-CONTEXT.md)."
    )


def test_adr_0013_pins_multi_env_rule(adr_text: str) -> None:
    """D-17-02: multi-env parameters.yml requires --environment.

    Accept either the implementation-side detection name (`environments_seen`)
    or the operator-facing bare phrase. Both are valid pins.
    """
    has_detection_name = "environments_seen" in adr_text
    has_operator_phrase = "multi-env parameters.yml requires --environment" in adr_text
    assert has_detection_name or has_operator_phrase, (
        "ADR-0013 must reference either the `environments_seen` detection "
        "rule or the bare phrase 'multi-env parameters.yml requires "
        "--environment' (D-17-02 locked rule)."
    )


def test_adr_0013_cross_references_adr_0012(adr_text: str) -> None:
    assert "ADR-0012" in adr_text, (
        "ADR-0013 must cross-reference ADR-0012 (the deferring decision "
        "Phase 17 closes by implementing Option C)."
    )
