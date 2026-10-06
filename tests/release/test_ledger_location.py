"""Tests for ledger location resolution from config/env and @destructive_op audit_dir routing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.deploy.core import DeployResult
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.governance.destructive import destructive_op
from sigantry_core.release.record import DeployRecord

runner = CliRunner()


def _seed(audit_dir: Path, *, release_id: str = "R1") -> None:
    record = DeployRecord(
        workspace="ws-test",
        release_id=release_id,
        work_items=[],
        fabric_items_changed=["nb_a.Notebook"],
        test_evidence={"smoke": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()
    emit_deploy_record(record, audit_dir=audit_dir)


def test_release_verify_reads_config_audit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    custom_audit = tmp_path / "custom_audit"
    custom_audit.mkdir()
    _seed(custom_audit, release_id="R-config")

    toml_path = work_dir / ".sigantry.toml"
    toml_path.write_text(f'[release]\naudit_dir = "{custom_audit.as_posix()}"\n', encoding="utf-8")

    result = runner.invoke(app, ["release", "verify"])
    assert result.exit_code == 0
    assert "1 record(s) verified" in result.stdout
    assert str(custom_audit) in result.stdout.replace("\r", "").replace("\n", "")
    # Ensure home ledger was never touched/created
    assert not (fake_home / ".sigantry" / "audit" / "deploys.jsonl").exists()


def test_release_verify_reads_env_audit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    custom_audit = tmp_path / "env_audit"
    custom_audit.mkdir()
    _seed(custom_audit, release_id="R-env")

    monkeypatch.setenv("SIGANTRY_RELEASE__AUDIT_DIR", str(custom_audit))

    result = runner.invoke(app, ["release", "verify"])
    assert result.exit_code == 0
    assert "1 record(s) verified" in result.stdout
    assert str(custom_audit) in result.stdout.replace("\r", "").replace("\n", "")
    assert not (fake_home / ".sigantry" / "audit" / "deploys.jsonl").exists()


def test_release_verify_cli_flag_wins_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    config_audit = tmp_path / "config_audit"
    config_audit.mkdir()
    # Empty in config_audit -> if read, would report NOTHING TO VERIFY (0 records)

    flag_audit = tmp_path / "flag_audit"
    flag_audit.mkdir()
    _seed(flag_audit, release_id="R-flag")

    toml_path = work_dir / ".sigantry.toml"
    toml_path.write_text(f'[release]\naudit_dir = "{config_audit.as_posix()}"\n', encoding="utf-8")

    result = runner.invoke(app, ["release", "verify", "--audit-dir", str(flag_audit)])
    assert result.exit_code == 0
    assert "1 record(s) verified" in result.stdout
    assert str(flag_audit) in result.stdout.replace("\r", "").replace("\n", "")


def test_destructive_op_honours_audit_dir_kwarg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    custom_audit = tmp_path / "custom_audit"
    custom_audit.mkdir()

    @destructive_op("test_res", "test_delete")
    def sample_op(*, force: bool = False, audit_dir: Path | None = None) -> str:
        return "deleted"

    res = sample_op(force=True, audit_dir=custom_audit)
    assert res == "deleted"

    ledger = custom_audit / "destructive_ops.jsonl"
    assert ledger.exists(), "destructive_ops.jsonl was not written to custom_audit"
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["resource_kind"] == "test_res"
    assert entries[0]["action"] == "test_delete"
    assert entries[0]["outcome"] == "succeeded"

    assert not (fake_home / ".sigantry" / "audit" / "destructive_ops.jsonl").exists()


def test_destructive_op_honours_config_audit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    custom_audit = tmp_path / "configured_destructive_audit"
    custom_audit.mkdir()

    toml_path = work_dir / ".sigantry.toml"
    toml_path.write_text(f'[release]\naudit_dir = "{custom_audit.as_posix()}"\n', encoding="utf-8")

    @destructive_op("test_res", "test_delete")
    def sample_op(*, force: bool = False) -> str:
        return "deleted"

    res = sample_op(force=True)
    assert res == "deleted"

    ledger = custom_audit / "destructive_ops.jsonl"
    assert ledger.exists(), "destructive_ops.jsonl was not written to custom_audit via config"
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["action"] == "test_delete"

    assert not (fake_home / ".sigantry" / "audit" / "destructive_ops.jsonl").exists()


def test_destructive_op_exception_path_honours_audit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    custom_audit = tmp_path / "custom_audit"
    custom_audit.mkdir()

    @destructive_op("test_res", "test_delete")
    def failing_op(*, force: bool = False, audit_dir: Path | None = None) -> str:
        raise RuntimeError("simulated boom")

    with pytest.raises(RuntimeError, match="simulated boom"):
        failing_op(force=True, audit_dir=custom_audit)

    ledger = custom_audit / "destructive_ops.jsonl"
    assert ledger.exists(), "destructive_ops.jsonl was not written on exception"
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["resource_kind"] == "test_res"
    assert entries[0]["action"] == "test_delete"
    assert entries[0]["outcome"] == "failed"
    assert entries[0]["exc_type"] == "RuntimeError"

    assert not (fake_home / ".sigantry" / "audit" / "destructive_ops.jsonl").exists()


def test_release_list_show_diff_honour_config_audit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    custom_audit = tmp_path / "custom_audit"
    custom_audit.mkdir()
    _seed(custom_audit, release_id="R-101")
    _seed(custom_audit, release_id="R-102")

    toml_path = work_dir / ".sigantry.toml"
    toml_path.write_text(f'[release]\naudit_dir = "{custom_audit.as_posix()}"\n', encoding="utf-8")

    # 1. list
    res_list = runner.invoke(app, ["release", "list"])
    assert res_list.exit_code == 0
    assert "R-101" in res_list.stdout
    assert "R-102" in res_list.stdout

    # 2. show
    res_show = runner.invoke(app, ["release", "show", "R-101"])
    assert res_show.exit_code == 0
    assert "R-101" in res_show.stdout

    # 3. diff
    res_diff = runner.invoke(app, ["release", "diff", "R-101", "R-102"])
    assert res_diff.exit_code == 0
    assert "nb_a.Notebook" in res_diff.stdout

    # Ensure fake_home was untouched
    assert not (fake_home / ".sigantry").exists()


def test_deploy_rollback_emits_to_configured_audit_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)

    custom_audit = tmp_path / "custom_audit"
    custom_audit.mkdir()
    _seed(custom_audit, release_id="R-roll")

    toml_path = work_dir / ".sigantry.toml"
    toml_path.write_text(f'[release]\naudit_dir = "{custom_audit.as_posix()}"\n', encoding="utf-8")

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    with patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_deploy:
        mock_deploy.return_value = DeployResult(
            workspace_id="ws-test",
            environment="dev",
            items_published=1,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )
        res = runner.invoke(
            app,
            [
                "deploy",
                "run",
                "--source",
                str(repo_dir),
                "--workspace-id",
                "ws-test",
                "--environment",
                "dev",
                "--rollback",
                "--to-release",
                "R-roll",
                "--rollback-force",
            ],
        )

    assert res.exit_code == 0

    # Verify destructive_ops.jsonl in custom_audit
    d_ledger = custom_audit / "destructive_ops.jsonl"
    assert d_ledger.exists()
    d_entries = [json.loads(line) for line in d_ledger.read_text(encoding="utf-8").splitlines()]
    assert len(d_entries) == 1
    assert d_entries[0]["action"] == "rollback"
    assert d_entries[0]["outcome"] == "succeeded"

    # Verify deploys.jsonl in custom_audit has the rollback record
    deploy_ledger = custom_audit / "deploys.jsonl"
    assert deploy_ledger.exists()
    deploy_entries = [
        json.loads(line) for line in deploy_ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(deploy_entries) == 2
    assert deploy_entries[0]["release_id"] == "R-roll"
    assert deploy_entries[1]["release_id"].startswith("rollback-of-R-roll-")

    # Ensure fake_home was untouched
    assert not (fake_home / ".sigantry").exists()
