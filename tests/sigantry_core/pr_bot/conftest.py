"""Shared fixtures for tests/sigantry_core/pr_bot/.

Wave 0 (Plan 14-00) stamps the directory + the FIXTURES_DIR helper.
Plan 14-02 adds deterministic-payload factories for the four
byte-snapshot scenarios (tmdl_only / lakehouse_only / both / none).
Plans 14-03..14-04 may add fake-Provider doubles here as they need them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from sigantry_core.pr_bot.payload import (
    LakehouseDiffSection,
    MeasureChange,
    PrCommentPayload,
    PrSummary,
    ShortcutChange,
    TableChange,
    TmdlDiffSection,
)

FIXTURES_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "fixtures" / "pr-bot"
"""Directory containing pr-bot test fixtures (TMDL pair, Lakehouse pair, snapshots)."""


# ---------------------------------------------------------------------------
# Deterministic payload factories
#
# Each factory builds a frozen pydantic v2 PrCommentPayload, calls
# .with_hash() to populate audit_hash, and returns the result. The
# values mirror the head-vs-base pair under tests/fixtures/pr-bot/tmdl/
# and tests/fixtures/pr-bot/lakehouse/ so that Plans 14-03 and 14-04 can
# round-trip their parser output through the same renderer assertions.
# ---------------------------------------------------------------------------


def deterministic_pr_summary() -> PrSummary:
    """Canonical PR summary used in all four scenario payloads."""
    return PrSummary(
        pr_id="42",
        provider="github",
        base_sha="aaaa1111",
        head_sha="bbbb2222",
        changed_files_count=3,
    )


def deterministic_tmdl_diff() -> TmdlDiffSection:
    """Canonical TMDL diff -- mirrors tests/fixtures/pr-bot/tmdl/{base,head}/Sales.tmdl.

    head adds 'Discount Amount' column + 'Avg Order Value' measure;
    modifies 'Total Sales' DAX expression.
    """
    return TmdlDiffSection(
        tables_modified=["Sales"],
        columns_added=[TableChange(table="Sales", name="'Discount Amount'")],
        measures_added=[MeasureChange(table="Sales", name="'Avg Order Value'")],
        measures_modified=[MeasureChange(table="Sales", name="'Total Sales'")],
    )


def deterministic_lakehouse_diff() -> LakehouseDiffSection:
    """Canonical Lakehouse diff -- mirrors the head-vs-base shortcuts pair.

    head adds a 'raw_customers' shortcut alongside base 'raw_orders'. The
    short-form column-warning string is included in ``warnings``; the
    renderer additionally emits the long-form footer at the very bottom
    of the Lakehouse section per RESEARCH §Critical Finding.
    """
    return LakehouseDiffSection(
        shortcuts_added=[
            ShortcutChange(
                name="raw_customers",
                path="Tables/raw_customers",
                target_path="Tables/customers",
            ),
        ],
        warnings=[
            "Note: Microsoft Fabric does not track Lakehouse table column types in Git.",
        ],
    )


def deterministic_tmdl_only_payload() -> PrCommentPayload:
    """TMDL diff present, lakehouse_diff=None -- snapshot: tmdl-only.md."""
    return PrCommentPayload(
        summary=deterministic_pr_summary(),
        tmdl_diff=deterministic_tmdl_diff(),
        lakehouse_diff=None,
    ).with_hash()


def deterministic_lakehouse_only_payload() -> PrCommentPayload:
    """Lakehouse diff present, tmdl_diff=None -- snapshot: lakehouse-only.md."""
    return PrCommentPayload(
        summary=deterministic_pr_summary(),
        tmdl_diff=None,
        lakehouse_diff=deterministic_lakehouse_diff(),
    ).with_hash()


def deterministic_both_payload() -> PrCommentPayload:
    """Both diff sections present -- snapshot: both.md."""
    return PrCommentPayload(
        summary=deterministic_pr_summary(),
        tmdl_diff=deterministic_tmdl_diff(),
        lakehouse_diff=deterministic_lakehouse_diff(),
    ).with_hash()


def deterministic_no_changes_payload() -> PrCommentPayload:
    """Both diff sections absent -- snapshot: none.md (D-07 'no silent' path)."""
    return PrCommentPayload(
        summary=deterministic_pr_summary(),
        tmdl_diff=None,
        lakehouse_diff=None,
    ).with_hash()
