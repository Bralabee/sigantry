"""``sync apply --unpublish-orphans`` needs ``--force``, and a dry run lists the deletes.

``docs/runbooks/sync/apply.md`` promised both flags, but ``apply_sync`` passed
``force=unpublish_orphans`` to the reconciler, so ``--unpublish-orphans`` alone
deleted every workspace item absent from ``sync.yml``: items of any type,
Lakehouses and Warehouses included, which the packagers never stage and so
never appear in a manifest. ``--dry-run`` computed that plan and printed none of
it. ``deploy run`` already asks for ``--unpublish-force``; ``sync pull`` already
asks for ``--force``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from typer.testing import CliRunner

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import FABRIC_AUDIENCE
from sigantry_core.client import FabricRestClient
from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.apply import SyncApplyReport, apply_sync
from sigantry_core.sync.cli import sync_app
from sigantry_core.sync.errors import SyncEngineError
from sigantry_core.workspace.reconciler import (
    PlannedFolderDelete,
    PlannedItemUnpublish,
    ReconcilePlan,
    ReconcileReport,
)

runner = CliRunner()

WORKSPACE_ID = "ws-orphans"


def _client() -> FabricRestClient:
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "sync.yml"
    path.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")
    return path


def _orphan_plan() -> ReconcilePlan:
    return ReconcilePlan(
        workspace_id=WORKSPACE_ID,
        unpublish_items=[
            PlannedItemUnpublish(
                item_id="i-lh",
                display_name="Sales",
                item_type="Lakehouse",
                current_folder_id=None,
            )
        ],
        delete_folders=[PlannedFolderDelete(folder_id="f-old", path=("raw", "old"))],
    )


def _recording_reconcile(calls: list[dict], plan: ReconcilePlan | None = None):
    """A reconcile seam that records its keyword arguments and returns ``plan``."""

    def _fn(_client: object, workspace_id: str, **kwargs: object) -> ReconcileReport:
        calls.append(kwargs)
        return ReconcileReport(plan=plan or ReconcilePlan(workspace_id=workspace_id))

    return _fn


def _mock_workspace(router: respx.MockRouter) -> None:
    router.get(f"/v1/workspaces/{WORKSPACE_ID}").mock(
        return_value=httpx.Response(200, json={"id": WORKSPACE_ID, "gitConnection": None})
    )


def test_unpublish_orphans_without_force_is_refused_before_anything_runs(tmp_path: Path) -> None:
    calls: list[dict] = []
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:  # no routes: any HTTP call errors
        with _client() as client, pytest.raises(SyncEngineError, match="--force"):
            apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                unpublish_orphans=True,
                _reconcile_fn=_recording_reconcile(calls),
            )
        assert not router.calls, "the refusal must come before the first request"

    assert calls == [], "the reconciler must not run"
    assert list(audit_dir.iterdir()) == [], "a refusal writes no audit record"


def test_force_and_unpublish_orphans_reach_the_reconciler(tmp_path: Path) -> None:
    calls: list[dict] = []
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                unpublish_orphans=True,
                force=True,
                _reconcile_fn=_recording_reconcile(calls),
            )

    assert len(calls) == 1
    assert calls[0]["unpublish_orphans"] is True
    assert calls[0]["force"] is True


def test_default_apply_is_still_additive_and_not_forced(tmp_path: Path) -> None:
    calls: list[dict] = []
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                _reconcile_fn=_recording_reconcile(calls),
            )

    assert calls[0]["unpublish_orphans"] is False
    assert calls[0]["force"] is False


def test_force_alone_does_not_enable_orphan_cleanup(tmp_path: Path) -> None:
    calls: list[dict] = []
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                force=True,
                _reconcile_fn=_recording_reconcile(calls),
            )

    assert calls[0]["unpublish_orphans"] is False


def test_dry_run_needs_no_force_and_lists_what_would_be_deleted(tmp_path: Path) -> None:
    calls: list[dict] = []
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            report = apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                dry_run=True,
                unpublish_orphans=True,
                _reconcile_fn=_recording_reconcile(calls, _orphan_plan()),
            )

    assert report.outcome == "dry_run"
    assert calls[0]["unpublish_orphans"] is True
    assert report.orphan_items == ("Lakehouse: Sales",)
    assert report.orphan_folders == ("raw/old",)
    assert list(audit_dir.iterdir()) == []


def test_forced_apply_reports_the_orphans_it_removed(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            report = apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                unpublish_orphans=True,
                force=True,
                _reconcile_fn=_recording_reconcile([], _orphan_plan()),
            )

    assert report.outcome == "succeeded"
    assert report.orphan_items == ("Lakehouse: Sales",)
    assert report.orphan_folders == ("raw/old",)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _bypass_preview_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDT_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()


def _apply_args(manifest: Path, *extra: str) -> list[str]:
    return ["apply", "--manifest", str(manifest), "--workspace-id", WORKSPACE_ID, *extra]


def test_cli_unpublish_orphans_without_force_exits_1_and_names_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bypass_preview_warning(monkeypatch)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    result = runner.invoke(
        sync_app,
        _apply_args(_manifest(tmp_path), "--unpublish-orphans", "--audit-dir", str(audit_dir)),
    )

    assert result.exit_code == 1, result.output
    assert "--force" in result.output
    assert list(audit_dir.iterdir()) == []


def test_cli_passes_force_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bypass_preview_warning(monkeypatch)
    seen: dict = {}

    def fake_apply(*_a: object, **kwargs: object) -> SyncApplyReport:
        seen.update(kwargs)
        return SyncApplyReport(
            workspace_id=WORKSPACE_ID,
            manifest_path="sync.yml",
            items_packaged=0,
            folders_created=0,
            items_moved=0,
            deploy_record_release_id="sync-x",
            outcome="succeeded",
            failure_reason=None,
            staging_dir=None,
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)

    result = runner.invoke(
        sync_app, _apply_args(_manifest(tmp_path), "--unpublish-orphans", "--force")
    )

    assert result.exit_code == 0, result.output
    assert seen["unpublish_orphans"] is True
    assert seen["force"] is True


def test_cli_dry_run_prints_every_orphan_it_would_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bypass_preview_warning(monkeypatch)

    def fake_apply(*_a: object, **_k: object) -> SyncApplyReport:
        return SyncApplyReport(
            workspace_id=WORKSPACE_ID,
            manifest_path="sync.yml",
            items_packaged=0,
            folders_created=0,
            items_moved=0,
            deploy_record_release_id="",
            outcome="dry_run",
            failure_reason=None,
            staging_dir=None,
            orphan_items=("Lakehouse: Sales", "[bold]Report[/bold]: Q3"),
            orphan_folders=("raw/old",),
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)

    result = runner.invoke(
        sync_app, _apply_args(_manifest(tmp_path), "--dry-run", "--unpublish-orphans")
    )

    assert result.exit_code == 0, result.output
    assert "Lakehouse: Sales" in result.output
    # Names are data, not Rich markup (issue #80): the brackets survive verbatim.
    assert "[bold]Report[/bold]: Q3" in result.output
    assert "raw/old" in result.output
