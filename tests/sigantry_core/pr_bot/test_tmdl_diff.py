"""Plan 14-03 unit tests for sigantry_core.pr_bot.tmdl_diff (STARTER-05).

Replaces the Wave 0 xfail stubs (Plan 14-00) with real assertions
driven by the fixture trees under ``tests/fixtures/pr-bot/tmdl/``:

- ``base/Sales.tmdl``                       (Wave 0)
- ``head/Sales.tmdl``                       (Wave 0)
- ``base/Customers.tmdl``                   (Plan 14-03 Task 1 -- removed-table)
- ``head_quoted_name/Sales.tmdl``           (Plan 14-03 Task 1 -- escaped quote)
- ``head_description_only/Sales.tmdl``      (Plan 14-03 Task 1 -- /// only)

The 7th test (``test_tmdl_parser_rejects_oversized_file``) defends the
1MB per-file cap from RESEARCH §Threat patterns / threat T-14-03-01.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sigantry_core.pr_bot.payload import MeasureChange, TableChange
from sigantry_core.pr_bot.tmdl_diff import (
    TmdlParseError,
    diff_tmdl,
    parse,
)

# Local Path(__file__) idiom -- repo lacks a top-level tests/__init__.py
# so `from tests.sigantry_core.pr_bot.conftest import FIXTURES_DIR` does
# not resolve as a module path (same root cause as 14-00 deviations 1+2
# and 14-02 deviation 1).
_FIXTURES_DIR: Path = Path(__file__).resolve().parents[2] / "fixtures" / "pr-bot"
_TMDL_DIR: Path = _FIXTURES_DIR / "tmdl"


def test_tmdl_diff_detects_added_column() -> None:
    """head adds 'Discount Amount' column under table Sales."""
    result = diff_tmdl(_TMDL_DIR / "base", _TMDL_DIR / "head")
    assert TableChange(table="Sales", name="'Discount Amount'") in result.columns_added


def test_tmdl_diff_detects_added_measure() -> None:
    """head adds 'Avg Order Value' measure under table Sales."""
    result = diff_tmdl(_TMDL_DIR / "base", _TMDL_DIR / "head")
    assert MeasureChange(table="Sales", name="'Avg Order Value'") in result.measures_added


def test_tmdl_diff_detects_modified_measure_dax() -> None:
    """head modifies the DAX of 'Total Sales' (byte-different body)."""
    result = diff_tmdl(_TMDL_DIR / "base", _TMDL_DIR / "head")
    assert MeasureChange(table="Sales", name="'Total Sales'") in result.measures_modified


def test_tmdl_diff_detects_removed_table() -> None:
    """base has Customers.tmdl, head/ does not -- diff reports tables_removed."""
    result = diff_tmdl(_TMDL_DIR / "base", _TMDL_DIR / "head")
    assert "Customers" in result.tables_removed


def test_tmdl_diff_handles_quoted_name_with_escaped_quote() -> None:
    """`'It''s a sale'` parses as ONE name (Pitfall 7).

    The diff reports it under measures_added with the verbatim quoted
    form (including the ``''`` escape) preserved in name; ``parse()``
    also returns exactly ONE block for it (proving ``''`` isn't
    tokenised as two separate names).
    """
    result = diff_tmdl(_TMDL_DIR / "head", _TMDL_DIR / "head_quoted_name")
    assert MeasureChange(table="Sales", name="'It''s a sale'") in result.measures_added
    # Direct parse: count blocks named 'It''s a sale' -- must be exactly 1.
    text = (_TMDL_DIR / "head_quoted_name" / "Sales.tmdl").read_text(encoding="utf-8")
    blocks = parse(text, source="head_quoted_name/Sales.tmdl")
    sale_blocks = [b for b in blocks if b.name == "'It''s a sale'"]
    assert len(sale_blocks) == 1, (
        f"expected exactly 1 block for 'It''s a sale', got {len(sale_blocks)}: "
        f"{[(b.kind, b.name) for b in sale_blocks]}"
    )
    assert sale_blocks[0].kind == "measure"
    assert sale_blocks[0].parent_table == "Sales"


def test_tmdl_diff_treats_description_only_change_as_modified() -> None:
    """`///` description-only edit on a measure surfaces as modified.

    Per RESEARCH Open Q #2: the description block binds to the following
    object (Pitfall 6) and is part of that object's body, so a
    description-only diff is byte-different and reports as modified.
    """
    result = diff_tmdl(_TMDL_DIR / "base", _TMDL_DIR / "head_description_only")
    assert MeasureChange(table="Sales", name="'Total Sales'") in result.measures_modified


def test_tmdl_parser_rejects_oversized_file() -> None:
    """1 MB per-file cap raises TmdlParseError (T-14-03-01 mitigation)."""
    big = "table T\n    column C\n        dataType: int64\n" * 100_000
    # ~3.7 MB -- well over 1 MB cap.
    with pytest.raises(TmdlParseError) as exc_info:
        parse(big, source="oversized.tmdl")
    msg = str(exc_info.value)
    assert "oversized.tmdl" in msg
    assert "1048576" in msg or "exceeds" in msg
