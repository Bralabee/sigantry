"""CliRunner tests for ``sigantry release show`` (Plan 12-03 / PIPELINE-04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

runner = CliRunner()


def _seed(tmp_path: Path, release_id: str = "R-show") -> DeployRecord:
    record = DeployRecord(
        workspace="ws-test",
        release_id=release_id,
        work_items=[],
        fabric_items_changed=["nb_x.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()
    emit_deploy_record(record, audit_dir=tmp_path)
    return record


def test_show_subcommand_listed_in_release_help() -> None:
    result = runner.invoke(app, ["release", "--help"])
    assert result.exit_code == 0
    assert "show" in result.stdout


def test_show_emits_full_record_json(tmp_path: Path) -> None:
    """Happy path: exit 0 + JSON keyset matches DeployRecord.model_dump."""
    record = _seed(tmp_path, release_id="R-show")
    result = runner.invoke(
        app,
        ["release", "show", "R-show", "--audit-dir", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, f"stdout={result.stdout!r}"
    payload = json.loads(result.stdout)
    assert payload["release_id"] == "R-show"
    # Keyset matches DeployRecord.model_dump.
    assert set(payload.keys()) == set(record.model_dump(mode="json").keys())


def test_show_missing_id_exits_1(tmp_path: Path) -> None:
    """release_id not in ledger -> exit code 1 + clear message on stdout."""
    _seed(tmp_path)
    result = runner.invoke(
        app,
        ["release", "show", "R-not-there", "--audit-dir", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "R-not-there" in result.stdout


def test_show_human_mode_default(tmp_path: Path) -> None:
    """Without ``--json``, output is pretty-printed JSON via _console.print_json."""
    _seed(tmp_path, release_id="R-human")
    result = runner.invoke(
        app,
        ["release", "show", "R-human", "--audit-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    # Human mode is pretty-printed JSON via _console.print_json -- contains the id.
    assert "R-human" in result.stdout
