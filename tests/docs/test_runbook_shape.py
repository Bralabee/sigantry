"""Phase 19 / DOCS-H-07 runbook-shape contract test.

Pins the section-0 decision matrix + section-1.1 boundary fence shape
across the four locked operator runbooks (D-19-01). A future runbook
added to ``LOCKED_RUNBOOKS`` without those sections fails CI; an
existing runbook that has its section-0 / section-1.1 deleted, moved,
or stripped of cross-references also fails CI.

Falsifiability invariants:

- Each LOCKED_RUNBOOKS entry contains the ``## 0. Decision matrix`` heading.
- Each entry contains a ``### 1.1.`` heading whose text contains
  ``does NOT do``.
- Each section-0 contains a markdown table with at least four data rows
  (D-19-02 minimum: apply / pull / diff / snapshot verbs).
- Each section-0 cross-references ADR-0012.
- The LOCKED_RUNBOOKS set itself is pinned (DOCS-H-07 meta-falsifier --
  count==4 + every path exists on disk).

Adding a fifth runbook to LOCKED_RUNBOOKS without first adding the
section-0 + section-1.1 content to that runbook is the canonical
failure mode the meta-test exists to catch.

References: D-19-01 (locked set), D-19-02 (section-0 shape), D-19-03
(section-1.1 boundary fence), D-19-06 (test location).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# D-19-01 locked set. To extend: add a Path entry here AFTER the runbook
# itself has been edited to carry section-0 + section-1.1. CI will fail
# otherwise.
LOCKED_RUNBOOKS: tuple[Path, ...] = (
    _REPO_ROOT / "docs" / "runbooks" / "sync" / "pull.md",
    _REPO_ROOT / "docs" / "runbooks" / "drift-detection" / "scheduled-drift.md",
    _REPO_ROOT / "docs" / "runbooks" / "workspace-bootstrap-operator.md",
    _REPO_ROOT / "docs" / "runbooks" / "pipeline-orchestration" / "deploy-with-tests.md",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"runbook missing at {path}"
    return path.read_text(encoding="utf-8")


def _section_0_body(text: str) -> str:
    """Slice the section-0 body: from the section-0 heading up to the
    next second-level (``## ``) heading. Returns the slice including
    the heading line."""
    match = re.search(r"^## 0\. Decision matrix.*?$", text, flags=re.MULTILINE)
    assert match is not None
    after_heading_line = match.end()
    tail_match = re.search(r"^## (?!#)", text[after_heading_line:], flags=re.MULTILINE)
    if tail_match is None:
        return text[match.start() :]
    return text[match.start() : after_heading_line + tail_match.start()]


@pytest.mark.parametrize("runbook", LOCKED_RUNBOOKS, ids=lambda p: p.name)
def test_runbook_has_section_0_decision_matrix(runbook: Path) -> None:
    text = _read(runbook)
    assert re.search(r"^## 0\. Decision matrix", text, flags=re.MULTILINE), (
        f"{runbook.name} must contain `## 0. Decision matrix` heading "
        f"(D-19-02 / Phase 19 shape contract)"
    )


@pytest.mark.parametrize("runbook", LOCKED_RUNBOOKS, ids=lambda p: p.name)
def test_runbook_has_section_1_1_boundary_fence(runbook: Path) -> None:
    text = _read(runbook)
    match = re.search(r"^### 1\.1\.[^\n]*", text, flags=re.MULTILINE)
    assert match is not None, (
        f"{runbook.name} must contain a `### 1.1.` heading (D-19-03 boundary fence)"
    )
    assert "does NOT do" in match.group(0), (
        f"{runbook.name} section-1.1 heading must contain 'does NOT do'; got: {match.group(0)!r}"
    )


@pytest.mark.parametrize("runbook", LOCKED_RUNBOOKS, ids=lambda p: p.name)
def test_runbook_section_0_cross_references_adr_0012(runbook: Path) -> None:
    text = _read(runbook)
    body = _section_0_body(text)
    assert "ADR-0012" in body, f"{runbook.name} section-0 must cross-reference ADR-0012 (D-19-02)"


@pytest.mark.parametrize("runbook", LOCKED_RUNBOOKS, ids=lambda p: p.name)
def test_runbook_section_0_table_has_min_four_rows(runbook: Path) -> None:
    """Section-0's markdown table must have >= 4 data rows (D-19-02 minimum)."""
    text = _read(runbook)
    body = _section_0_body(text)
    # Lines starting with `|` are either header / separator / data rows.
    # The separator row matches `^\|[\s:-]+\|`; exclude those. The first
    # remaining `|`-prefixed line is the header; subtract 1 to get data
    # row count.
    rows = [
        ln for ln in body.splitlines() if ln.startswith("|") and not re.match(r"^\|[\s:-]+\|", ln)
    ]
    data_rows = max(0, len(rows) - 1)
    assert data_rows >= 4, (
        f"{runbook.name} section-0 table must have >= 4 data rows "
        f"(D-19-02 minimum); counted {data_rows}"
    )


def test_locked_runbook_set_pinned() -> None:
    """DOCS-H-07 meta-falsifier: the LOCKED_RUNBOOKS tuple is the
    load-bearing surface. Adding/removing entries without coordinated
    runbook edits fails CI."""
    assert len(LOCKED_RUNBOOKS) == 4, (
        "Phase 19 locks exactly 4 runbooks (D-19-01). Extending "
        "LOCKED_RUNBOOKS requires (a) the new runbook to carry "
        "section-0 + section-1.1, AND (b) a follow-up phase plan that "
        "updates this assertion."
    )
    for p in LOCKED_RUNBOOKS:
        assert p.is_file(), f"locked runbook missing on disk: {p}"
