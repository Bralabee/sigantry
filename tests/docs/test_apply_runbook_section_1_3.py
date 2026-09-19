"""Phase 17 docs-shape test: apply runbook gains §1.3 between §1.2 and §2.

Pins the structural insertion of `### 1.3. First-time publish via
`--with-publish`` between the existing `### 1.2.` (CLI output trailer)
and the existing `## 2.` (Operator setup).

Falsifiability invariants:
- All three headings exist.
- Order: idx(§1.2) < idx(§1.3) < idx(§2).
- §1.3 heading line carries the substring "with-publish".
- §1.3 body cross-references ADR-0013.
- §1.3 body documents the partial-failure surface (PUBLISH-05).

A regression where §1.3 gets deleted, moved out of order, or has its
critical content blocks pruned fails CI immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RUNBOOK_PATH = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "sync" / "apply.md"


@pytest.fixture(scope="module")
def runbook_text() -> str:
    assert _RUNBOOK_PATH.is_file(), f"apply runbook missing at {_RUNBOOK_PATH}"
    return _RUNBOOK_PATH.read_text(encoding="utf-8")


def _heading_offset(text: str, pattern: str) -> int | None:
    """Return character offset of first regex match, or None."""
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.start() if match else None


def test_apply_runbook_has_section_1_3_between_1_2_and_2(runbook_text: str) -> None:
    # Match the actual heading levels used in apply.md: §1.1 / §1.2 / §1.3
    # are third-level (`### 1.x.`) nested under §1; §2 is second-level
    # (`## 2.`). Use anchored multiline regex so we match heading lines
    # specifically, not body prose that happens to contain "1.3".
    idx_1_2 = _heading_offset(runbook_text, r"^### 1\.2\.")
    idx_1_3 = _heading_offset(runbook_text, r"^### 1\.3\.")
    idx_2 = _heading_offset(runbook_text, r"^## 2\.")

    assert idx_1_2 is not None, "apply.md must have a `### 1.2.` heading (existing)"
    assert idx_1_3 is not None, (
        "apply.md must have a `### 1.3.` heading (Phase 17 insertion -- "
        "see 17-03-PLAN.md task 17-03-03)"
    )
    assert idx_2 is not None, "apply.md must have a `## 2.` heading (existing)"

    assert idx_1_2 < idx_1_3 < idx_2, (
        "apply.md headings out of order: §1.3 must sit between §1.2 and §2 "
        f"(found offsets §1.2={idx_1_2}, §1.3={idx_1_3}, §2={idx_2})."
    )


def test_apply_runbook_section_1_3_heading_mentions_with_publish(
    runbook_text: str,
) -> None:
    """Heading line of §1.3 must reference --with-publish (the surface name)."""
    match = re.search(r"^### 1\.3\.[^\n]*", runbook_text, flags=re.MULTILINE)
    assert match is not None, "apply.md missing `### 1.3.` heading"
    assert "with-publish" in match.group(0), (
        f"§1.3 heading should reference 'with-publish'; got: {match.group(0)!r}"
    )


def _section_1_3_body(text: str) -> str:
    """Slice the §1.3 body: from the §1.3 heading up to the next `## ` heading.

    The end-of-section sentinel is a second-level heading (`## ` literally
    -- two hashes + space, NOT three). Searching from the end of the §1.3
    heading line avoids spuriously matching the §1.3 heading's own `### `
    prefix.
    """
    match = re.search(r"^### 1\.3\..*?$", text, flags=re.MULTILINE)
    assert match is not None, "apply.md missing `### 1.3.` heading"
    section_start = match.start()
    # Search for the next second-level heading AFTER the §1.3 heading line ends.
    after_heading_line = match.end()
    tail_match = re.search(r"^## (?!#)", text[after_heading_line:], flags=re.MULTILINE)
    if tail_match is None:
        return text[section_start:]
    return text[section_start : after_heading_line + tail_match.start()]


def test_apply_runbook_section_1_3_cross_references_adr_0013(
    runbook_text: str,
) -> None:
    body = _section_1_3_body(runbook_text)
    assert "ADR-0013" in body, (
        "apply.md §1.3 must cross-reference ADR-0013 (the resolution-rule ADR)."
    )


def test_apply_runbook_section_1_3_documents_partial_failure(
    runbook_text: str,
) -> None:
    """PUBLISH-05 surface: operators reading §1.3 must find partial-failure docs."""
    body = _section_1_3_body(runbook_text)
    assert "partial-failure" in body, (
        "apply.md §1.3 must document the 'partial-failure' outcome (PUBLISH-05 / D-17-05 surface)."
    )
