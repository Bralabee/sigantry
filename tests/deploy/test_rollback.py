"""Unit tests for sigantry_core.deploy.rollback (Plan 12-04 / PIPELINE-03).

Replaces the seven Wave 0 xfail stubs with real assertions. Adds an
eighth defence-in-depth test (`test_rollback_rejects_tampered_record_via_monkeypatch`)
that bypasses the ledger filter to exercise the rollback module's own
`verify_hash()` guard so the branch survives future refactors.

Two CLI guard tests are also appended (`test_cli_rollback_*`) — they
isolate the rollback flag-validation by supplying the forward-path
required flags so the surfaced BadParameter mentions the rollback flag
rather than an unrelated missing flag (the load-bearing ordering note
in Task 2 / Step 2).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.deploy.core import DeployResult
from sigantry_core.deploy.rollback import rollback_to_release
from sigantry_core.governance.audit import emit_deploy_record
from sigantry_core.release.record import DeployRecord

runner = CliRunner()


def _record(
    release_id: str,
    workspace: str,
    items: list[str],
) -> DeployRecord:
    return DeployRecord(
        workspace=workspace,
        release_id=release_id,
        work_items=[],
        fabric_items_changed=items,
        test_evidence={"smoke": "passed"},
        approver="test@example.invalid",
        audit_hash="",
        created_at=datetime.now(UTC),
    ).with_hash()


def test_feature_flags_appended() -> None:
    """LOAD-BEARING Pitfall 1 regression-catcher.

    Both feature flags MUST be in fabric_cicd.constants.FEATURE_FLAG
    after `import sigantry_core.deploy.rollback`. A future refactor
    that moves the append_feature_flag calls below the
    deploy_workspace import (or removes them) trips this test
    immediately.
    """
    from fabric_cicd.constants import FEATURE_FLAG

    import sigantry_core.deploy.rollback  # noqa: F401  -- triggers append_feature_flag

    assert "enable_experimental_features" in FEATURE_FLAG
    assert "enable_items_to_include" in FEATURE_FLAG


def test_rejects_missing_release(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No release"):
        rollback_to_release(
            "R-not-there",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )


def test_rejects_tampered_record(tmp_path: Path) -> None:
    """Mutate audit_hash byte -> the rollback names TAMPERING, not absence.

    `iter_records` (Plan 12-03) defensively SKIPS tampered records, so by the
    time `rollback_to_release` calls `find_by_release_id` the tampered record is
    already filtered out and `find_by_release_id` returns None. The old code
    therefore surfaced the misleading "No release ..." message -- sending an
    operator to hunt a mistyped release id when the real condition was a
    corrupt/hand-edited ledger line. The not-found branch now does a strict
    re-read that detects the present-but-tamper-failed record and says so (S-T5).
    """
    emit_deploy_record(
        _record("R-tamper", "ws-A", ["nb_a.Notebook"]),
        audit_dir=tmp_path,
    )
    jsonl = tmp_path / "deploys.jsonl"
    parsed = json.loads(jsonl.read_text("utf-8").strip())
    parsed["audit_hash"] = parsed["audit_hash"][:-1] + (
        "0" if parsed["audit_hash"][-1] != "0" else "1"
    )
    jsonl.write_text(json.dumps(parsed) + "\n", "utf-8")
    # find_by_release_id returns None (iter_records skipped the tampered line);
    # the strict re-read in _raise_missing_or_tampered then names the tampering
    # rather than reporting the target absent.
    with pytest.raises(ValueError, match="FAILS audit_hash verification"):
        rollback_to_release(
            "R-tamper",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )


def test_rejects_cross_workspace(tmp_path: Path) -> None:
    """Record recorded against ws-A; rollback to ws-B -> ValueError."""
    emit_deploy_record(
        _record("R-xws", "ws-A", ["nb_a.Notebook"]),
        audit_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="Cross-workspace rollback is not supported"):
        rollback_to_release(
            "R-xws",
            workspace="ws-B",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )


def test_dry_run_returns_zero_counts(tmp_path: Path) -> None:
    emit_deploy_record(
        _record("R-dry", "ws-A", ["nb_a.Notebook", "lh_b.Lakehouse"]),
        audit_dir=tmp_path,
    )
    with patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_deploy:
        result = rollback_to_release(
            "R-dry",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook", "Lakehouse"],
            dry_run=True,
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )
    mock_deploy.assert_not_called()
    assert result.items_published == 0
    assert result.items_failed == 0


def test_empty_items_logs_warning_returns_zero(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty fabric_items_changed -> logged no-op, NOT an error."""
    emit_deploy_record(
        _record("R-empty", "ws-A", []),
        audit_dir=tmp_path,
    )
    with (
        patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_deploy,
        caplog.at_level("WARNING", logger="sigantry_core.deploy.rollback"),
    ):
        result = rollback_to_release(
            "R-empty",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )
    mock_deploy.assert_not_called()
    assert result.items_published == 0
    assert any("rollback no-op" in m for m in caplog.messages)


def test_happy_path_calls_deploy_workspace_with_items_to_include(
    tmp_path: Path,
) -> None:
    """Mocked deploy_workspace; assert items_to_include == record.fabric_items_changed."""
    items = ["nb_a.Notebook", "lh_b.Lakehouse"]
    emit_deploy_record(
        _record("R-happy", "ws-A", items),
        audit_dir=tmp_path,
    )
    with patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_deploy:
        mock_deploy.return_value = DeployResult(
            workspace_id="ws-A",
            environment="dev",
            items_published=2,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )
        result = rollback_to_release(
            "R-happy",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook", "Lakehouse"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )
    assert result.items_published == 2
    mock_deploy.assert_called_once()
    kwargs = mock_deploy.call_args.kwargs
    assert kwargs["items_to_include"] == items
    assert kwargs["workspace_id"] == "ws-A"


def test_rollback_rejects_tampered_record_via_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence-in-depth: verify_hash() branch in rollback_to_release.

    Under normal use, `iter_records` in Plan 12-03 SKIPS tampered
    records before they reach `rollback_to_release`, so the
    `verify_hash()` guard in rollback.py looks like dead code. This
    test monkeypatches `find_by_release_id` to bypass the ledger
    layer and return a tampered DeployRecord DIRECTLY -- exercising
    the rollback module's own `verify_hash()` guard so the
    defence-in-depth branch is exercised at unit-test time and
    future refactors that delete the guard fail loudly.

    Asserts the surfaced ValueError carries the
    `failed audit_hash verification` message from rollback.py
    (NOT the `No release ...` message that fires when the ledger
    layer filters first -- see test_rejects_tampered_record).
    """
    tampered = _record("R-monkey", "ws-A", ["nb_a.Notebook"])
    # Mutate the audit_hash byte AFTER with_hash() has run so the
    # record's payload no longer matches its stored audit_hash.
    tampered_dict = tampered.model_dump()
    tampered_dict["audit_hash"] = tampered.audit_hash[:-1] + (
        "0" if tampered.audit_hash[-1] != "0" else "1"
    )
    bad = type(tampered).model_validate(tampered_dict)
    assert not bad.verify_hash(), (
        "test fixture must produce a hash-failing record; check _record + mutation"
    )

    # Bypass the ledger filter -- return the tampered record directly
    # to rollback_to_release so its own verify_hash() guard fires.
    monkeypatch.setattr(
        "sigantry_core.deploy.rollback.find_by_release_id",
        lambda release_id, audit_dir=None: bad,
    )
    with pytest.raises(ValueError, match="failed audit_hash verification"):
        rollback_to_release(
            "R-monkey",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )


# ---------------------------------------------------------------------------
# Task 2: CLI guard tests for `--rollback` / `--to-release` flag pair.
#
# These supplement the Task 1 unit tests above. They run via CliRunner
# against the top-level `sigantry deploy run` command and assert the
# Typer-level BadParameter behaviour (exit code 2).
# ---------------------------------------------------------------------------


def test_cli_rollback_without_to_release_raises_bad_parameter() -> None:
    """`--rollback` without `--to-release` exits 2 with a `--to-release` hint.

    The fixture supplies all three forward-path required flags
    (`--source`, `--workspace-id`, `--environment`) so the test
    ISOLATES the `--rollback`/`--to-release` validation. This
    confirms the rollback guard runs BEFORE the unrelated forward-path
    required-flag checks (per the load-bearing ordering note in
    Task 2 / Step 2): the surfaced BadParameter mentions
    `--to-release`, not `--source`/`--workspace-id`/`--environment`.
    """
    result = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            ".",
            "--workspace-id",
            "ws-A",
            "--environment",
            "dev",
            "--rollback",
        ],
    )
    # typer.BadParameter exits with code 2.
    assert result.exit_code == 2
    assert "--to-release" in (result.stdout + (result.stderr or ""))


def test_cli_rollback_conflicts_with_unpublish_orphans() -> None:
    """`--rollback --to-release X --unpublish-orphans` exits 2 (mutex).

    Fixture again supplies all three required forward-path flags so
    the test isolates the rollback/orphan-unpublish mutex; the guard
    must fire on the documented mutex rather than on a generic
    required-flag complaint.
    """
    result = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            ".",
            "--workspace-id",
            "ws-A",
            "--environment",
            "dev",
            "--rollback",
            "--to-release",
            "R-x",
            "--unpublish-orphans",
            "--unpublish-force",
        ],
    )
    assert result.exit_code == 2
    combined = result.stdout + (result.stderr or "")
    assert "--rollback" in combined
    assert "--unpublish-orphans" in combined
