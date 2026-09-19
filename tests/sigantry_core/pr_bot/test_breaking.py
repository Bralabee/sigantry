"""Unit tests for TMDL breaking change impact guard in pr-bot."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.pr_bot.breaking import (
    BreakingChange,
    analyze_tmdl_breaking_changes,
    render_breaking_changes_markdown,
)
from sigantry_core.pr_bot.payload import (
    MeasureChange,
    RelationshipChange,
    TableChange,
    TmdlDiffSection,
)

runner = CliRunner()


def test_analyze_tmdl_breaking_changes_detects_all_categories() -> None:
    """analyze_tmdl_breaking_changes captures dropped tables, columns, measures, and relationships."""
    diff = TmdlDiffSection(
        tables_removed=["LegacySales"],
        columns_removed=[TableChange(table="Customers", name="CreditCardNumber")],
        measures_removed=[MeasureChange(table="Orders", name="DeprecatedRevenue")],
        relationships_removed=[
            RelationshipChange(from_table="Orders", to_table="LegacySales")
        ],
    )

    changes = analyze_tmdl_breaking_changes(diff)

    assert len(changes) == 4
    by_kind = {c.kind: c for c in changes}

    assert "table_removed" in by_kind
    assert by_kind["table_removed"].severity == "CRITICAL"
    assert by_kind["table_removed"].target == "LegacySales"

    assert "column_removed" in by_kind
    assert by_kind["column_removed"].severity == "HIGH"
    assert "CreditCardNumber" in by_kind["column_removed"].target

    assert "measure_removed" in by_kind
    assert by_kind["measure_removed"].severity == "HIGH"
    assert "DeprecatedRevenue" in by_kind["measure_removed"].target

    assert "relationship_removed" in by_kind
    assert by_kind["relationship_removed"].severity == "HIGH"


def test_analyze_tmdl_breaking_changes_empty_on_additions_only() -> None:
    """Non-destructive additions and modifications produce zero breaking changes."""
    diff = TmdlDiffSection(
        tables_added=["NewSales"],
        columns_added=[TableChange(table="Customers", name="LoyaltyTier")],
        measures_added=[MeasureChange(table="Orders", name="GrossMargin")],
        measures_modified=[MeasureChange(table="Orders", name="TaxRate")],
    )

    changes = analyze_tmdl_breaking_changes(diff)
    assert changes == []

    md = render_breaking_changes_markdown(changes)
    assert md == ""


def test_render_breaking_changes_markdown_format() -> None:
    """render_breaking_changes_markdown outputs properly styled markdown alert."""
    changes = [
        BreakingChange(
            kind="table_removed",
            severity="CRITICAL",
            target="DimDate",
            impact="Table dropped.",
        ),
        BreakingChange(
            kind="measure_removed",
            severity="HIGH",
            target="[TotalTax]",
            impact="Measure dropped.",
        ),
    ]

    md = render_breaking_changes_markdown(changes)
    assert "> [!WARNING]" in md
    assert "Breaking Changes Detected in Semantic Model (TMDL)" in md
    assert "🔴 **CRITICAL**" in md
    assert "🟠 **HIGH**" in md
    assert "DimDate" in md
    assert "[TotalTax]" in md


def test_cli_pr_bot_fail_on_breaking(tmp_path: Path, monkeypatch) -> None:
    """pr-bot run --fail-on-breaking exits 1 when breaking changes are detected."""
    base_dir = tmp_path / "base"
    head_dir = tmp_path / "head"
    base_dir.mkdir()
    head_dir.mkdir()

    fake_tmdl_diff = TmdlDiffSection(
        tables_removed=["DroppedTable"],
    )

    monkeypatch.setattr(
        "sigantry_core.pr_bot.cli.diff_tmdl",
        MagicMock(return_value=fake_tmdl_diff),
    )
    monkeypatch.setattr(
        "sigantry_core.pr_bot.cli.diff_lakehouse",
        MagicMock(return_value=MagicMock(is_empty=MagicMock(return_value=True), identity_changes=[], schema_toggle=None, tracked_tables_added=[], tracked_tables_removed=[], shortcuts_added=[], shortcuts_removed=[], shortcuts_modified=[], role_changes=[])),
    )

    mock_pr = MagicMock(base_sha="aaa", head_sha="bbb")
    mock_provider = MagicMock()
    mock_provider.get_pr.return_value = mock_pr
    mock_provider.get_changed_files.return_value = ["model.tmdl"]
    mock_provider.post_comment.return_value = "comment-123"

    monkeypatch.setattr(
        "sigantry_core.pr_bot.cli._build_github_provider",
        MagicMock(return_value=mock_provider),
    )

    # 1. Without --fail-on-breaking: should exit 0
    res_ok = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--pr-id",
            "42",
            "--token",
            "fake-token",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--dry-run",
        ],
    )
    assert res_ok.exit_code == 0
    assert "Breaking Changes Detected in Semantic Model" in res_ok.stdout

    # 2. With --fail-on-breaking: should exit 1
    res_fail = runner.invoke(
        app,
        [
            "pr-bot",
            "run",
            "--provider",
            "github",
            "--pr-id",
            "42",
            "--token",
            "fake-token",
            "--base-dir",
            str(base_dir),
            "--head-dir",
            str(head_dir),
            "--fail-on-breaking",
            "--dry-run",
        ],
    )
    assert res_fail.exit_code == 1
    assert "breaking change(s) detected in semantic model" in res_fail.stderr
