"""Lakehouse metadata-file diff tests for STARTER-06 (Plan 14-04).

Drives ``sigantry_core.pr_bot.lakehouse_diff.diff_lakehouse`` over the
six fixture trees under ``tests/fixtures/pr-bot/lakehouse/``:

- ``base/``                 -- canonical base snapshot
- ``head/``                 -- adds ``raw_customers`` shortcut
- ``head_schema_toggle/``   -- flips ``defaultSchema`` dbo -> analytics
- ``head_displayname_change/`` -- renames ``Sales`` -> ``SalesV2``
- ``base_with_roles/``      -- base + 1 data-access role (Reader)
- ``head_with_roles/``      -- base + 2 data-access roles (Reader + Analyst)

Per RESEARCH §Critical Finding -- Microsoft Fabric does NOT track
Lakehouse table column types in Git -- the diff scope is bounded to
metadata-file changes; the column-level warning string is asserted by
``test_lakehouse_diff_includes_column_warning_footer``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sigantry_core.pr_bot.lakehouse_diff import LakehouseDiffError, diff_lakehouse
from sigantry_core.pr_bot.payload import (
    IdentityChange,
    LakehouseDiffSection,
)

# Local Path(__file__) idiom -- repo lacks a top-level tests/__init__.py
# so `from tests.sigantry_core.pr_bot.conftest import FIXTURES_DIR` does
# not resolve as a module path (mirrors test_tmdl_diff.py).
_FIXTURES_DIR: Path = Path(__file__).resolve().parents[2] / "fixtures" / "pr-bot"
LK_DIR: Path = _FIXTURES_DIR / "lakehouse"


def test_lakehouse_diff_detects_added_shortcut() -> None:
    """head adds 'raw_customers' shortcut; diff reports it under shortcuts_added (RESEARCH §Example 2)."""
    result = diff_lakehouse(LK_DIR / "base", LK_DIR / "head")
    assert isinstance(result, LakehouseDiffSection)
    added_names = [s.name for s in result.shortcuts_added]
    assert added_names == ["raw_customers"], result
    assert result.shortcuts_removed == []
    assert result.shortcuts_modified == []
    # Identity unchanged between base and head
    assert result.identity_changes == []
    # Schema toggle absent -- both sides defaultSchema=='dbo'
    assert result.schema_toggle is None


def test_lakehouse_diff_detects_removed_shortcut() -> None:
    """base has shortcut that head omits; diff reports it under shortcuts_removed."""
    # Reverse the directories: 'head' becomes the base, 'base' becomes the head.
    result = diff_lakehouse(LK_DIR / "head", LK_DIR / "base")
    removed_names = [s.name for s in result.shortcuts_removed]
    assert removed_names == ["raw_customers"], result
    assert result.shortcuts_added == []
    assert result.shortcuts_modified == []


def test_lakehouse_diff_detects_default_schema_toggle() -> None:
    """defaultSchema flip in lakehouse.metadata.json surfaces as schema_toggle change (RESEARCH §2)."""
    result = diff_lakehouse(LK_DIR / "base", LK_DIR / "head_schema_toggle")
    assert result.schema_toggle is not None
    assert result.schema_toggle.before == "dbo"
    assert result.schema_toggle.after == "analytics"
    # Schema-toggle fixture isolates the flip: shortcuts byte-identical to base.
    assert result.shortcuts_added == []
    assert result.shortcuts_removed == []


def test_lakehouse_diff_detects_displayname_change_in_platform() -> None:
    """.platform displayName change surfaces as identity_changes entry (RESEARCH §2 / D-11)."""
    result = diff_lakehouse(LK_DIR / "base", LK_DIR / "head_displayname_change")
    assert (
        IdentityChange(field="displayName", before="Sales", after="SalesV2")
        in result.identity_changes
    ), result
    # description + type unchanged so identity_changes carries exactly the displayName entry.
    assert len(result.identity_changes) == 1


def test_lakehouse_diff_includes_column_warning_footer() -> None:
    """LakehouseDiffSection.warnings always includes 'column types not in Git' note (RESEARCH §Critical Finding)."""
    result = diff_lakehouse(LK_DIR / "base", LK_DIR / "head")
    assert any(
        "Microsoft Fabric does not track Lakehouse table column types in Git" in w
        for w in result.warnings
    ), result.warnings
    # The short warning is the FINAL entry per the diff_lakehouse contract.
    assert result.warnings[-1].startswith(
        "Note: Microsoft Fabric does not track Lakehouse table column types in Git."
    )


def test_lakehouse_diff_rejects_oversized_metadata(tmp_path: Path) -> None:
    """Per-file 1MB cap raises LakehouseDiffError (T-14-04-01 DoS mitigation)."""
    base = tmp_path / "base"
    head = tmp_path / "head"
    (base / "Big.Lakehouse").mkdir(parents=True)
    (head / "Big.Lakehouse").mkdir(parents=True)
    # Both sides need a valid .platform so _find_lakehouse_dirs picks
    # the directory up symmetrically.
    (base / "Big.Lakehouse" / ".platform").write_text(
        '{"version":"2.0","metadata":{"type":"Lakehouse","displayName":"Big"}}'
    )
    (head / "Big.Lakehouse" / ".platform").write_text(
        '{"version":"2.0","metadata":{"type":"Lakehouse","displayName":"Big"}}'
    )
    # Synthesize a 2 MB lakehouse.metadata.json on the base side.
    (base / "Big.Lakehouse" / "lakehouse.metadata.json").write_text(
        '{"x":"' + ("a" * 2_000_000) + '"}'
    )
    (head / "Big.Lakehouse" / "lakehouse.metadata.json").write_text("{}")
    (base / "Big.Lakehouse" / "shortcuts.metadata.json").write_text('{"shortcuts":[]}')
    (head / "Big.Lakehouse" / "shortcuts.metadata.json").write_text('{"shortcuts":[]}')

    with pytest.raises(LakehouseDiffError):
        diff_lakehouse(base, head)
