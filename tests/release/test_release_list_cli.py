"""CliRunner tests for ``sigantry release list`` (Plan 12-03 / PIPELINE-04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

runner = CliRunner()


def _seed(
    tmp_path: Path,
    *,
    release_id: str,
    workspace: str = "ws-test",
    offset_seconds: int = 0,
) -> None:
    record = DeployRecord(
        workspace=workspace,
        release_id=release_id,
        work_items=[],
        fabric_items_changed=["nb_a.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC) + timedelta(seconds=offset_seconds),
    ).with_hash()
    emit_deploy_record(record, audit_dir=tmp_path)


def test_list_subcommand_listed_in_release_help() -> None:
    """``sigantry release --help`` lists ``list``."""
    result = runner.invoke(app, ["release", "--help"])
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    assert "list" in result.stdout


def test_list_table_mode(tmp_path: Path) -> None:
    """Empty ledger, single record, two records (newest-first ordering)."""
    # Empty ledger is not an error.
    result = runner.invoke(app, ["release", "list", "--audit-dir", str(tmp_path)])
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    # Single record.
    _seed(tmp_path, release_id="R1")
    result = runner.invoke(app, ["release", "list", "--audit-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "R1" in result.stdout
    # Two records -- most-recent first.
    _seed(tmp_path, release_id="R2", offset_seconds=10)
    result = runner.invoke(app, ["release", "list", "--audit-dir", str(tmp_path)])
    assert result.exit_code == 0
    # Newest-first ordering: R2 appears before R1 in the table.
    assert result.stdout.index("R2") < result.stdout.index("R1")


def test_list_json_mode(tmp_path: Path) -> None:
    """``--json`` emits JSON array; each entry round-trips DeployRecord."""
    _seed(tmp_path, release_id="R-json")
    result = runner.invoke(app, ["release", "list", "--audit-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    # Each entry round-trips via DeployRecord.
    for entry in payload:
        DeployRecord(**entry)


def test_list_limit_caps_results(tmp_path: Path) -> None:
    """WR-06 review fix: ``--limit`` MUST be applied after the newest-first
    sort, not before. Seed five records with strictly increasing
    ``created_at`` offsets and assert the surfaced two records are the
    NEWEST two (R-limit-4 and R-limit-3) in that order. The previous
    test only asserted ``len(payload) == 2``, which would also pass if a
    future contributor moved the ``[:limit]`` slice BEFORE the sort
    (a plausible perf change because slicing is cheaper than sorting
    a large file). Asserting order explicitly is the load-bearing
    invariant.
    """
    for i in range(5):
        _seed(tmp_path, release_id=f"R-limit-{i}", offset_seconds=i)
    result = runner.invoke(
        app,
        [
            "release",
            "list",
            "--audit-dir",
            str(tmp_path),
            "--limit",
            "2",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 2
    # Newest-first ordering: R-limit-4 (offset=4) then R-limit-3 (offset=3).
    # If a refactor moves the slice before the sort, this assertion trips
    # immediately.
    assert [entry["release_id"] for entry in payload] == [
        "R-limit-4",
        "R-limit-3",
    ]


def test_list_env_filter_substring_match(tmp_path: Path) -> None:
    _seed(tmp_path, release_id="R-dev-a", workspace="dev-a")
    _seed(tmp_path, release_id="R-dev-b", workspace="dev-b")
    _seed(tmp_path, release_id="R-prod-c", workspace="prod-c")
    result = runner.invoke(
        app,
        [
            "release",
            "list",
            "--audit-dir",
            str(tmp_path),
            "--env",
            "dev",
            "--json",
        ],
    )
    assert result.exit_code == 0
    ids = {entry["release_id"] for entry in json.loads(result.stdout)}
    assert ids == {"R-dev-a", "R-dev-b"}


def test_list_workspace_filter_canonical_spelling(tmp_path: Path) -> None:
    """WR-04 review fix: ``--workspace`` is the canonical spelling of the
    workspace-substring filter; ``--env`` is a deprecated alias preserved
    for backwards compatibility. Both flags MUST drive the same filter
    against ``DeployRecord.workspace``.
    """
    _seed(tmp_path, release_id="R-ws1", workspace="ws-aaa-1")
    _seed(tmp_path, release_id="R-ws2", workspace="ws-aaa-2")
    _seed(tmp_path, release_id="R-other", workspace="ws-bbb-1")
    result = runner.invoke(
        app,
        [
            "release",
            "list",
            "--audit-dir",
            str(tmp_path),
            "--workspace",
            "aaa",
            "--json",
        ],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    ids = {entry["release_id"] for entry in json.loads(result.stdout)}
    assert ids == {"R-ws1", "R-ws2"}
