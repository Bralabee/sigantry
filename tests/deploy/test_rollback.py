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


def test_rollback_rejects_empty_fabric_items_changed(
    tmp_path: Path,
) -> None:
    """Issue #71: Empty fabric_items_changed raises ValueError -- cannot roll back."""
    emit_deploy_record(
        _record("R-empty", "ws-A", []),
        audit_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="recorded 0 items"):
        rollback_to_release(
            "R-empty",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )


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


def test_rollback_rejects_zero_items_in_scope(tmp_path: Path) -> None:
    """Issue #71: If recorded items exist but 0 match --item-types, raise ValueError."""
    emit_deploy_record(_record("R-oos", "ws-A", ["rpt_1.Report"]), audit_dir=tmp_path)
    with pytest.raises(ValueError, match="0 items matching"):
        rollback_to_release(
            "R-oos",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            runbook_id="ROLL-INC-TEST",
        )


def test_cli_rollback_empty_items_exits_1(tmp_path: Path) -> None:
    """Issue #71: CLI rollback against release with 0 items exits 1 with error."""
    emit_deploy_record(_record("R-empty-cli", "ws-A", []), audit_dir=tmp_path)
    res = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--rollback",
            "--to-release",
            "R-empty-cli",
            "--rollback-force",
            "--source",
            str(tmp_path),
            "--workspace-id",
            "ws-A",
            "--environment",
            "dev",
            "--audit-dir",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 1
    assert "rollback failed" in res.stdout
    assert "0 items" in res.stdout


def test_cli_rollback_catches_all_exceptions_cleanly(tmp_path: Path) -> None:
    """Issue #77: Non-ValueError exceptions print 'rollback failed: <msg>' without traceback and exit 1."""
    emit_deploy_record(_record("R-err", "ws-A", ["nb_1.Notebook"]), audit_dir=tmp_path)
    with patch(
        "sigantry_core.deploy.rollback.deploy_workspace",
        side_effect=RuntimeError("publish explosion"),
    ):
        res = runner.invoke(
            app,
            [
                "deploy",
                "run",
                "--rollback",
                "--to-release",
                "R-err",
                "--rollback-force",
                "--source",
                str(tmp_path),
                "--workspace-id",
                "ws-A",
                "--environment",
                "dev",
                "--audit-dir",
                str(tmp_path),
            ],
        )
    assert res.exit_code == 1
    assert "rollback failed: publish explosion" in res.stdout
    assert "Traceback" not in (res.stdout + (res.stderr or ""))


def test_cli_rollback_forwards_tenant_id(tmp_path: Path) -> None:
    """Issue #78: CLI rollback passes --tenant-id to TokenProvider.from_defaults."""
    from sigantry_core.auth import TokenProvider

    emit_deploy_record(_record("R-tenant", "ws-A", ["nb_1.Notebook"]), audit_dir=tmp_path)
    seen_tenants: list[str | None] = []
    orig_from_defaults = TokenProvider.from_defaults

    def fake_from_defaults(**kwargs: object) -> object:
        seen_tenants.append(kwargs.get("tenant_id"))  # type: ignore[arg-type]
        return orig_from_defaults(**kwargs)  # type: ignore[arg-type]

    with (
        patch.object(TokenProvider, "from_defaults", side_effect=fake_from_defaults),
        patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_dw,
    ):
        mock_dw.return_value = DeployResult("ws-A", "dev", 1, 0, 0, None)
        res = runner.invoke(
            app,
            [
                "deploy",
                "run",
                "--rollback",
                "--to-release",
                "R-tenant",
                "--rollback-force",
                "--source",
                str(tmp_path),
                "--workspace-id",
                "ws-A",
                "--environment",
                "dev",
                "--audit-dir",
                str(tmp_path),
                "--tenant-id",
                "custom-tenant-xyz",
            ],
        )
    assert res.exit_code == 0
    assert "custom-tenant-xyz" in seen_tenants
    assert mock_dw.call_args.kwargs.get("token_provider") is not None


def test_rollback_filters_items_to_scope_and_emits_in_scope_record(
    tmp_path: Path,
) -> None:
    """Issue #79: Rollback only deploys in-scope items, reports skipped, and emits filtered DeployRecord."""
    from sigantry_core.release.ledger import iter_records

    emit_deploy_record(
        _record("R-mixed", "ws-A", ["nb_1.Notebook", "lh_1.Lakehouse", "rpt_1.Report"]),
        audit_dir=tmp_path,
    )
    with patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_dw:
        mock_dw.return_value = DeployResult("ws-A", "dev", 1, 0, 0, None)
        res = runner.invoke(
            app,
            [
                "deploy",
                "run",
                "--rollback",
                "--to-release",
                "R-mixed",
                "--rollback-force",
                "--source",
                str(tmp_path),
                "--workspace-id",
                "ws-A",
                "--environment",
                "dev",
                "--audit-dir",
                str(tmp_path),
                "--item-types",
                "Notebook",
            ],
        )
    assert res.exit_code == 0
    assert mock_dw.call_args.kwargs["items_to_include"] == ["nb_1.Notebook"]
    records = list(iter_records(audit_dir=tmp_path))
    rb_record = next(r for r in records if r.release_id.startswith("rollback-of-R-mixed"))
    assert rb_record.fabric_items_changed == ["nb_1.Notebook"]
    out = res.stdout + (res.stderr or "")
    assert "Skipp" in out
    assert "lh_1.Lakehouse" in out


# ---------------------------------------------------------------------------
# Review round 2 on PR #88: the raw-GUID flags must reach a rollback too.
# ---------------------------------------------------------------------------


def test_rollback_passes_the_raw_guid_opt_in_to_deploy_workspace(tmp_path: Path) -> None:
    emit_deploy_record(_record("R-optin", "ws-A", ["nb_1.Notebook"]), audit_dir=tmp_path)
    with patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_dw:
        mock_dw.return_value = DeployResult("ws-A", "dev", 1, 0, 0, None)
        rollback_to_release(
            "R-optin",
            workspace="ws-A",
            repository_directory=str(tmp_path),
            environment="dev",
            item_type_in_scope=["Notebook"],
            audit_dir=tmp_path,
            force=True,
            allow_raw_guids=True,
        )
    assert mock_dw.call_args.kwargs["allow_raw_guids"] is True


@pytest.mark.parametrize(
    ("extra", "expected"),
    [(["--allow-raw-guids"], True), (["--no-allow-raw-guids"], False), ([], None)],
)
def test_cli_rollback_forwards_the_raw_guid_flags(
    tmp_path: Path, extra: list[str], expected: bool | None
) -> None:
    """Reproduced in review: ``--rollback`` dropped both flags, so a release
    deployed from a stock file could not be rolled back with ``--allow-raw-guids``,
    and ``--no-allow-raw-guids`` could not refuse one when CI sets the env var."""
    emit_deploy_record(_record("R-flag", "ws-A", ["nb_1.Notebook"]), audit_dir=tmp_path)
    with patch("sigantry_core.deploy.rollback.deploy_workspace") as mock_dw:
        mock_dw.return_value = DeployResult("ws-A", "dev", 1, 0, 0, None)
        res = runner.invoke(
            app,
            [
                "deploy",
                "run",
                "--rollback",
                "--to-release",
                "R-flag",
                "--rollback-force",
                "--source",
                str(tmp_path),
                "--workspace-id",
                "ws-A",
                "--environment",
                "dev",
                "--audit-dir",
                str(tmp_path),
                *extra,
            ],
        )
    assert res.exit_code == 0, res.output
    assert mock_dw.call_args.kwargs.get("allow_raw_guids") is expected
