"""Unit tests for Sigantry standalone interactive HTML reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.release.record import DeployRecord
from sigantry_core.reports.html import (
    render_drift_html_report,
    render_release_diff_html_report,
    render_release_html_report,
)
from sigantry_core.sync.diff import DriftReport

runner = CliRunner()


def test_render_drift_html_report_structure() -> None:
    """Drift HTML report renders valid HTML with statistics, tables, and scripts."""
    report = DriftReport(
        added=[
            {
                "logical_id": "Notebook:CleanData",
                "display_name": "CleanData",
                "type": "Notebook",
                "folder_path": "/bronze",
            }
        ],
        removed=[
            {
                "logical_id": "Lakehouse:OldLH",
                "display_name": "OldLH",
                "type": "Lakehouse",
                "folder_path": "/legacy",
            }
        ],
        modified=[
            {
                "logical_id": "DataPipeline:Ingest",
                "fields_changed": ["target_folder"],
            }
        ],
        unchanged=[
            {
                "logical_id": "Notebook:SharedUtils",
            }
        ],
    )

    html_out = render_drift_html_report(
        report,
        environment="DEV",
        workspace_id="ws-test-12345",
    )

    assert "<!DOCTYPE html>" in html_out
    assert "Sigantry Drift Report - DEV" in html_out
    assert "ws-test-12345" in html_out
    assert "CleanData" in html_out
    assert "OldLH" in html_out
    assert "DataPipeline:Ingest" in html_out
    assert "Notebook:SharedUtils" in html_out
    assert "filterItems()" in html_out
    assert "+ added" in html_out
    assert "- removed" in html_out
    assert "~ modified" in html_out
    assert "= unchanged" in html_out


def test_render_release_html_report() -> None:
    """Release HTML report renders record metadata and changed items."""
    now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
    record = DeployRecord(
        workspace="ws-sample-999",
        release_id="rel-2026-09-19-1",
        work_items=["US-101", "BUG-202"],
        fabric_items_changed=["Transform.Notebook", "Gold.Lakehouse"],
        test_evidence={"unit_tests": "passed", "integration": "green"},
        approver="test-approver@domain.com",
        created_at=now,
    ).with_hash()

    payload = record.model_dump(mode="json")
    html_out = render_release_html_report(payload)

    assert "<!DOCTYPE html>" in html_out
    assert "Release Report &bull; rel-2026-09-19-1" in html_out
    assert "ws-sample-999" in html_out
    assert "test-approver@domain.com" in html_out
    assert "Transform.Notebook" in html_out
    assert "Gold.Lakehouse" in html_out
    assert "US-101" in html_out
    assert "BUG-202" in html_out
    assert "unit_tests" in html_out


def test_render_release_diff_html_report() -> None:
    """Release diff HTML report renders items difference."""
    diff_data = {
        "release_a": "rel-1",
        "release_b": "rel-2",
        "added": [
            {
                "fabric_item_id": "NewItem.Notebook",
                "item_type": "Notebook",
                "logical_name": "NewItem",
            }
        ],
        "removed": [
            {
                "fabric_item_id": "OldItem.Notebook",
                "item_type": "Notebook",
                "logical_name": "OldItem",
            }
        ],
        "unchanged": [
            {
                "fabric_item_id": "KeptItem.Lakehouse",
                "item_type": "Lakehouse",
                "logical_name": "KeptItem",
            }
        ],
    }

    html_out = render_release_diff_html_report(diff_data)

    assert "<!DOCTYPE html>" in html_out
    assert "Release Comparison" in html_out
    assert "rel-1" in html_out
    assert "rel-2" in html_out
    assert "NewItem" in html_out
    assert "OldItem" in html_out
    assert "KeptItem" in html_out


def test_cli_diff_html_output(monkeypatch, tmp_path: Path) -> None:
    """sigantry diff --output html emits HTML report and writes to file when --html-out is set."""
    fake_report = DriftReport(
        added=[
            {
                "logical_id": "Notebook:CleanData",
                "display_name": "CleanData",
                "type": "Notebook",
                "folder_path": "/bronze",
            }
        ],
        removed=[],
        modified=[],
        unchanged=[],
    )
    monkeypatch.setattr(
        "sigantry_core.diff_cli.diff_workspace_against_manifest",
        MagicMock(return_value=fake_report),
    )

    manifest_file = tmp_path / "sync.yml"
    manifest_file.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    out_html = tmp_path / "drift.html"

    result = runner.invoke(
        app,
        [
            "diff",
            "--manifest",
            str(manifest_file),
            "--workspace-id",
            "ws-123",
            "--output",
            "html",
            "--html-out",
            str(out_html),
        ],
    )

    assert result.exit_code == 0
    assert out_html.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "CleanData" in content


def test_cli_release_show_and_diff_html(tmp_path: Path) -> None:
    """sigantry release show and diff support --html and --html-out."""
    now = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
    rec1 = DeployRecord(
        workspace="ws-1",
        release_id="rel-1",
        fabric_items_changed=["A.Notebook"],
        approver="lead@domain.com",
        created_at=now,
    ).with_hash()

    rec2 = DeployRecord(
        workspace="ws-1",
        release_id="rel-2",
        fabric_items_changed=["A.Notebook", "B.Lakehouse"],
        approver="lead@domain.com",
        prev_hash=rec1.audit_hash,
        created_at=now,
    ).with_hash()

    from sigantry_core.governance.audit import emit_deploy_record

    audit_dir = tmp_path / "audit"
    emit_deploy_record(rec1, audit_dir=audit_dir)
    emit_deploy_record(rec2, audit_dir=audit_dir)

    # Test show --html
    show_html = tmp_path / "show.html"
    res_show = runner.invoke(
        app,
        [
            "release",
            "show",
            "rel-1",
            "--audit-dir",
            str(audit_dir),
            "--html",
            "--html-out",
            str(show_html),
        ],
    )
    assert res_show.exit_code == 0
    assert show_html.exists()
    assert "rel-1" in show_html.read_text(encoding="utf-8")

    # Test diff --html
    diff_html = tmp_path / "diff.html"
    res_diff = runner.invoke(
        app,
        [
            "release",
            "diff",
            "rel-1",
            "rel-2",
            "--audit-dir",
            str(audit_dir),
            "--html",
            "--html-out",
            str(diff_html),
        ],
    )
    assert res_diff.exit_code == 0
    assert diff_html.exists()
    assert "B.Lakehouse" in diff_html.read_text(encoding="utf-8")
