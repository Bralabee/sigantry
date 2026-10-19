"""``sync apply --unpublish-orphans`` needs ``--force``, and a dry run lists the deletes.

``docs/runbooks/sync/apply.md`` promised both flags, but ``apply_sync`` passed
``force=unpublish_orphans`` to the reconciler, so ``--unpublish-orphans`` alone
deleted every workspace item absent from ``sync.yml``: items of any type,
Lakehouses and Warehouses included, which the packagers never stage and so
never appear in a manifest. ``--dry-run`` computed that plan and printed none of
it. ``deploy run`` already asks for ``--unpublish-force``; ``sync pull`` already
asks for ``--force``.

Since #101/#102 the cleanup deletes only items of a type in ``PACKAGER_REGISTRY``
that sit outside every ``folders[]`` path; the rest are reported as kept.
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
from sigantry_core.sync import apply as apply_mod
from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.apply import SyncApplyReport, apply_sync
from sigantry_core.sync.cli import sync_app
from sigantry_core.sync.errors import OrphanUnpublishNotForcedError, SyncEngineError
from sigantry_core.sync.packagers import PACKAGER_REGISTRY
from sigantry_core.workspace.reconciler import (
    KEPT_PRESERVED_FOLDER,
    KEPT_UNMANAGED_TYPE,
    KeptOrphanItem,
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
                item_type="Notebook",
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
    # Issue #102: the deletable types are the packager registry, read from it.
    assert calls[0]["managed_item_types"] == frozenset(PACKAGER_REGISTRY)


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
    assert report.orphan_items == ("Notebook: Sales",)
    assert report.orphan_folders == ("raw/old",)
    assert list(audit_dir.iterdir()) == []


def test_orphan_label_names_the_folder_the_item_sits_in(tmp_path: Path) -> None:
    plan = ReconcilePlan(
        workspace_id=WORKSPACE_ID,
        unpublish_items=[
            PlannedItemUnpublish(
                item_id="i-nb",
                display_name="Adhoc",
                item_type="Notebook",
                current_folder_id="f-keep",
                current_folder_path=("curated", "keep"),
            ),
            PlannedItemUnpublish(
                item_id="i-root",
                display_name="Sales",
                item_type="Notebook",
                current_folder_id=None,
            ),
        ],
    )

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            report = apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=tmp_path,
                dry_run=True,
                unpublish_orphans=True,
                _reconcile_fn=_recording_reconcile([], plan),
            )

    assert report.orphan_items == ("Notebook: Adhoc (in curated/keep)", "Notebook: Sales")


def test_refusal_comes_before_a_client_or_a_staging_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard sits above the client and the staging tempdir: a refused call leaks neither."""

    def _must_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("ran before the --force guard")

    monkeypatch.setattr(apply_mod.tempfile, "mkdtemp", _must_not_run)
    monkeypatch.setattr(FabricRestClient, "from_defaults", classmethod(_must_not_run))

    with pytest.raises(OrphanUnpublishNotForcedError):
        apply_sync(
            manifest_path=_manifest(tmp_path),
            workspace_id=WORKSPACE_ID,
            audit_dir=tmp_path,
            unpublish_orphans=True,
        )


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
    assert report.orphan_items == ("Notebook: Sales",)
    assert report.orphan_folders == ("raw/old",)


def test_kept_orphans_are_reported_with_their_reason(tmp_path: Path) -> None:
    plan = ReconcilePlan(
        workspace_id=WORKSPACE_ID,
        kept_items=[
            KeptOrphanItem("i-lh", "Sales", "Lakehouse", (), KEPT_UNMANAGED_TYPE),
            KeptOrphanItem("i-nb", "Adhoc", "Notebook", ("keep-me",), KEPT_PRESERVED_FOLDER),
        ],
    )

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _mock_workspace(router)
        with _client() as client:
            report = apply_sync(
                manifest_path=_manifest(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=tmp_path,
                dry_run=True,
                unpublish_orphans=True,
                _reconcile_fn=_recording_reconcile([], plan),
            )

    assert report.orphan_items == ()
    assert report.kept_orphan_items == (
        "Lakehouse: Sales -- kept: sync cannot create this type",
        "Notebook: Adhoc (in keep-me) -- kept: in a folder that folders[] preserves",
    )


# A workspace as it is after an analyst used it: a Lakehouse inside a folder
# the manifest never mentions, a Warehouse at the root, a notebook in a folder
# ``folders[]`` keeps, a stale notebook, and an empty leftover folder.
_LIVE_FOLDERS = [
    {"id": "f-old", "displayName": "old", "workspaceId": WORKSPACE_ID},
    {"id": "f-keep", "displayName": "keep-me", "workspaceId": WORKSPACE_ID},
    {"id": "f-arch", "displayName": "archive", "workspaceId": WORKSPACE_ID},
]
_LIVE_ITEMS = [
    {"id": i, "displayName": n, "type": t, "workspaceId": WORKSPACE_ID, "folderId": f}
    for i, n, t, f in (
        ("i-lh", "Sales", "Lakehouse", "f-old"),
        ("i-wh", "Dw", "Warehouse", None),
        ("i-adhoc", "Adhoc", "Notebook", "f-keep"),
        ("i-stale", "Stale", "Notebook", None),
    )
]


def _live_workspace(router: respx.MockRouter) -> None:
    _mock_workspace(router)
    router.get(f"/v1/workspaces/{WORKSPACE_ID}/folders").mock(
        return_value=httpx.Response(200, json={"value": _LIVE_FOLDERS})
    )
    router.get(f"/v1/workspaces/{WORKSPACE_ID}/items").mock(
        return_value=httpx.Response(200, json={"value": _LIVE_ITEMS})
    )


def _manifest_keeping(tmp_path: Path) -> Path:
    path = tmp_path / "sync.yml"
    path.write_text(
        "schema_version: '1.0.0'\nitems: []\nfolders:\n  - /keep-me\n", encoding="utf-8"
    )
    return path


def test_dry_run_through_the_real_reconciler_keeps_what_sync_cannot_own(
    tmp_path: Path,
) -> None:
    """#101 + #102 end to end: only HTTP is faked."""
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _live_workspace(router)
        with _client() as client:
            report = apply_sync(
                manifest_path=_manifest_keeping(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=tmp_path,
                dry_run=True,
                unpublish_orphans=True,
            )

    assert report.orphan_items == ("Notebook: Stale",)
    assert report.orphan_folders == ("archive",)
    assert sorted(report.kept_orphan_items) == [
        "Lakehouse: Sales (in old) -- kept: sync cannot create this type",
        "Notebook: Adhoc (in keep-me) -- kept: in a folder that folders[] preserves",
        "Warehouse: Dw -- kept: sync cannot create this type",
    ]


def test_forced_apply_through_the_real_reconciler_deletes_only_the_managed_orphans(
    tmp_path: Path,
) -> None:
    """The DELETE requests a forced run sends are exactly the stale notebook and
    the empty folder: no Lakehouse, no Warehouse, nothing in ``keep-me``."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _live_workspace(router)
        deletes = router.delete(url__regex=rf"/v1/workspaces/{WORKSPACE_ID}/.*").mock(
            return_value=httpx.Response(200, json={})
        )
        with _client() as client:
            report = apply_sync(
                manifest_path=_manifest_keeping(tmp_path),
                workspace_id=WORKSPACE_ID,
                client=client,
                audit_dir=audit_dir,
                unpublish_orphans=True,
                force=True,
            )

    assert report.outcome == "succeeded"
    assert [c.request.url.path for c in deletes.calls] == [
        f"/v1/workspaces/{WORKSPACE_ID}/items/i-stale",
        f"/v1/workspaces/{WORKSPACE_ID}/folders/f-arch",
    ]


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


def test_cli_applied_run_prints_the_orphans_it_deleted(
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
            deploy_record_release_id="sync-x",
            outcome="succeeded",
            failure_reason=None,
            staging_dir=None,
            orphan_items=("Notebook: Sales",),
            orphan_folders=("raw/old",),
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)

    result = runner.invoke(
        sync_app, _apply_args(_manifest(tmp_path), "--unpublish-orphans", "--force")
    )

    assert result.exit_code == 0, result.output
    assert "sync apply succeeded" in result.output
    assert "unpublished" in result.output
    assert "Notebook: Sales" in result.output
    assert "raw/old" in result.output


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
            orphan_items=("Notebook: Sales", "[bold]Report[/bold]: Q3"),
            orphan_folders=("raw/old",),
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)

    result = runner.invoke(
        sync_app, _apply_args(_manifest(tmp_path), "--dry-run", "--unpublish-orphans")
    )

    assert result.exit_code == 0, result.output
    assert "Notebook: Sales" in result.output
    # Names are data, not Rich markup (issue #80): the brackets survive verbatim.
    assert "[bold]Report[/bold]: Q3" in result.output
    assert "raw/old" in result.output


def test_cli_dry_run_prints_the_kept_orphans_even_when_nothing_is_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bypass_preview_warning(monkeypatch)
    kept = "[red]Lakehouse[/red]: Sales -- kept: sync cannot create this type"

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
            kept_orphan_items=(kept,),
        )

    monkeypatch.setattr(sync_cli_mod, "apply_sync", fake_apply)

    result = runner.invoke(
        sync_app, _apply_args(_manifest(tmp_path), "--dry-run", "--unpublish-orphans")
    )

    assert result.exit_code == 0, result.output
    assert "kept 1 item(s) absent from the manifest" in result.output
    assert kept in result.output  # verbatim: data, not markup
    assert "would unpublish" not in result.output
