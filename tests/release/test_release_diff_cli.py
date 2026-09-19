"""CliRunner tests for ``sigantry release diff`` (Plan 12-03 / PIPELINE-04).

Includes the load-bearing JSON-schema contract test (Pattern 5 / D-06):
the keyset {release_a, release_b, added, removed, unchanged} with
per-entry {logical_name, item_type, fabric_item_id} is SemVer-committed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

runner = CliRunner()


def _seed(tmp_path: Path, release_id: str, items: list[str]) -> None:
    record = DeployRecord(
        workspace="ws-test",
        release_id=release_id,
        work_items=[],
        fabric_items_changed=items,
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()
    emit_deploy_record(record, audit_dir=tmp_path)


def test_diff_subcommand_listed_in_release_help() -> None:
    result = runner.invoke(app, ["release", "--help"])
    assert result.exit_code == 0
    assert "diff" in result.stdout


def test_diff_added_removed_unchanged_categories(tmp_path: Path) -> None:
    """Happy path: correct bucketing of two recorded releases."""
    _seed(tmp_path, "R-a", ["lh_x.Lakehouse", "nb_y.Notebook"])
    _seed(tmp_path, "R-b", ["nb_y.Notebook", "lh_z.Lakehouse"])
    result = runner.invoke(
        app,
        [
            "release",
            "diff",
            "R-a",
            "R-b",
            "--audit-dir",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    payload = json.loads(result.stdout)
    added_ids = [e["fabric_item_id"] for e in payload["added"]]
    removed_ids = [e["fabric_item_id"] for e in payload["removed"]]
    unchanged_ids = [e["fabric_item_id"] for e in payload["unchanged"]]
    assert added_ids == ["lh_z.Lakehouse"]
    assert removed_ids == ["lh_x.Lakehouse"]
    assert unchanged_ids == ["nb_y.Notebook"]


def test_diff_json_schema(tmp_path: Path) -> None:
    """LOAD-BEARING contract test (Pattern 5 / D-06 SemVer commitment).

    Asserts top-level keyset {"release_a", "release_b", "added", "removed",
    "unchanged"} and per-entry keyset {"logical_name", "item_type",
    "fabric_item_id"}. Adding a new field requires a SemVer-minor bump.
    """
    _seed(tmp_path, "R-x", ["lh_x.Lakehouse"])
    _seed(tmp_path, "R-y", ["nb_y.Notebook"])
    result = runner.invoke(
        app,
        [
            "release",
            "diff",
            "R-x",
            "R-y",
            "--audit-dir",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    # Top-level SemVer-committed keyset.
    assert set(payload.keys()) == {
        "release_a",
        "release_b",
        "added",
        "removed",
        "unchanged",
    }
    # Per-entry SemVer-committed keyset.
    for bucket in ("added", "removed", "unchanged"):
        for entry in payload[bucket]:
            assert set(entry.keys()) == {
                "logical_name",
                "item_type",
                "fabric_item_id",
            }


def test_diff_missing_id_exits_1(tmp_path: Path) -> None:
    """Either id missing -> exit code 1."""
    _seed(tmp_path, "R-only", ["nb_a.Notebook"])
    result = runner.invoke(
        app,
        [
            "release",
            "diff",
            "R-only",
            "R-missing",
            "--audit-dir",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 1


def test_diff_human_mode_color_summary(tmp_path: Path) -> None:
    """Without ``--json``, output contains ``+ N / - M / = K`` summary."""
    _seed(tmp_path, "R-a", ["lh_x.Lakehouse"])
    _seed(tmp_path, "R-b", ["nb_y.Notebook"])
    result = runner.invoke(
        app,
        ["release", "diff", "R-a", "R-b", "--audit-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    # Human-mode summary line -- assert structurally, not on exact ANSI colors.
    assert "+ 1" in result.stdout
    assert "- 1" in result.stdout
    assert "= 0" in result.stdout
