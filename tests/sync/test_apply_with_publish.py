"""Tests for ``apply_sync(with_publish=True, ...)`` -- Phase 17 / SYNC-PUBLISH.

Covers PUBLISH-01 SemVer-safety + PUBLISH-02 / PUBLISH-03 / PUBLISH-05 unit-side
falsifiability for the wire-through of the engine helper into apply_sync.

Mocks at the module-import boundary on ``sigantry_core.sync.apply``:

- ``snapshot_workspace`` -- returns a controllable WorkspaceSnapshot stand-in.
- ``publish_absent_items`` -- returns a controllable PublishResult.
- ``reconcile_folders_from_repo`` -- returns a fake ReconcileReport.

This isolates the test from real fabric-cicd / Fabric REST while exercising the
combined-record builder, the SemVer-safe default branch, the partial-failure
folder-reconcile-still-committed invariant, the staging-tempdir lifetime
extension (D-17-07), and the ``release_id = "sync-publish-<TS>"`` prefix
(D-17-04).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import FABRIC_AUDIENCE
from sigantry_core.client import FabricRestClient
from sigantry_core.deploy.sync_publish import PublishResult
from sigantry_core.release.record import DeployRecord
from sigantry_core.sync.apply import apply_sync
from sigantry_core.sync.errors import SyncPublishError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_with_mock_token() -> FabricRestClient:
    """Mirror the helper in tests/sync/test_apply.py -- short-circuits OAuth."""
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _write_minimal_ipynb(path: Path) -> None:
    payload = {
        "cells": [],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_sync_yml(target: Path, *, items_yaml: str) -> None:
    target.write_text(
        f"schema_version: '1.0.0'\nitems:\n{items_yaml}\n",
        encoding="utf-8",
    )


def _write_minimal_parameters_yml(target: Path) -> None:
    """Minimal parameters.yml that passes load_and_validate.

    Empty find_replace / key_value_replace lists keep the validators happy
    with no $ENV references and no GUIDs to reject.
    """
    target.write_text("find_replace: []\nkey_value_replace: []\n", encoding="utf-8")


def _make_item(display_name: str, type_: str):
    """Construct a workspace.items.Item with the minimum fields the
    apply.py snapshot consumer reads.
    """
    from sigantry_core.workspace.items import Item

    return Item(
        id=f"item-{display_name.lower()}",
        display_name=display_name,
        type=type_,
        workspace_id="ws-test",
        description=None,
        sensitivity_label_id=None,
        folder_id=None,
    )


def _make_fake_report():
    """Fake ReconcileReport with empty plan (1 folder, no moves by default)."""
    from sigantry_core.workspace.reconciler import ReconcilePlan, ReconcileReport

    return ReconcileReport(plan=ReconcilePlan(workspace_id="ws-test"))


def _make_fake_report_with_moves(*, folders_created: int, items_moved: int):
    """Fake ReconcileReport with operator-controllable plan counts."""
    from sigantry_core.workspace.reconciler import (
        PlannedFolderCreate,
        PlannedItemMove,
        ReconcilePlan,
        ReconcileReport,
    )

    plan = ReconcilePlan(
        workspace_id="ws-test",
        create_folders=[PlannedFolderCreate(path=(f"f{i}",)) for i in range(folders_created)],
        move_items=[
            PlannedItemMove(
                item_id=f"i{i}",
                display_name=f"Item{i}",
                item_type="Notebook",
                from_folder_id=None,
                to_folder_path=("dest",),
            )
            for i in range(items_moved)
        ],
    )
    return ReconcileReport(plan=plan)


def _make_fake_report_for_moves(moves: list[tuple[str, str]]):
    """Fake ReconcileReport whose plan.move_items matches the given (display_name, type) tuples.

    Used by tests that need ``moved_items`` in the persisted DeployRecord
    to reflect specific manifest entries rather than the synthetic
    ``Item0`` / ``Item1`` names from :func:`_make_fake_report_with_moves`.
    The audit-record correctness fix (apply.py: ``actually_moved_keys``)
    threads ``plan.move_items`` -> ``test_evidence.moved_items``, so the
    fake report controls what the assertion can see.
    """
    from sigantry_core.workspace.reconciler import (
        PlannedItemMove,
        ReconcilePlan,
        ReconcileReport,
    )

    plan = ReconcilePlan(
        workspace_id="ws-test",
        move_items=[
            PlannedItemMove(
                item_id=f"i-{name.lower()}",
                display_name=name,
                item_type=type_,
                from_folder_id=None,
                to_folder_path=("raw",),
            )
            for name, type_ in moves
        ],
    )
    return ReconcileReport(plan=plan)


def _stub_snapshot(items: list):
    """Build a WorkspaceSnapshot-shaped stand-in for the snapshot mock."""
    from sigantry_core.sync.snapshot import WorkspaceSnapshot

    return WorkspaceSnapshot(
        workspace_id="ws-test",
        folders_by_id={},
        items_by_id={it.id: it for it in items},
        folder_path_index={},
        item_to_folder={it.id: None for it in items},
    )


def _patch_publish_path(
    monkeypatch,
    *,
    snapshot_items: list | None = None,
    publish_result: PublishResult | None = None,
    reconcile_report=None,
    publish_call_log: list | None = None,
):
    """Build test doubles for ``apply_sync``'s three internal collaborators.

    Audit-2026-05-07 W4.4 refactor: pre-W4.4 this helper monkey-patched
    ``snapshot_workspace`` / ``publish_absent_items`` /
    ``reconcile_folders_from_repo`` on the ``sigantry_core.sync.apply``
    module. Post-W4.4 the helper returns the same trio of mocks plus
    a ``doubles_kwargs`` dict; callers pass ``**doubles_kwargs`` to
    ``apply_sync`` so the function uses its injected seams instead of
    its module-level imports. The ``monkeypatch`` argument is retained
    for signature compatibility but is no longer used; callers can
    pass ``None`` or any value.

    Returns
    -------
    tuple[MagicMock, MagicMock, MagicMock, dict]
        ``(fake_publish, fake_snapshot, fake_reconcile, doubles_kwargs)``.
        ``doubles_kwargs`` is meant to be splatted into
        ``apply_sync(..., **doubles_kwargs)``.
    """
    fake_snapshot = MagicMock(
        return_value=_stub_snapshot(snapshot_items or []),
    )
    fake_publish = MagicMock(
        return_value=publish_result
        or PublishResult(outcome="succeeded", published_items=[], failed_item=None),
    )
    if publish_call_log is not None:

        def _record_call(**kwargs):
            # D-17-07 invariant probe: capture staging_dir.exists() at the
            # moment publish is invoked so the test can assert the
            # tempdir lived through the call.
            staging_dir = kwargs["staging_dir"]
            publish_call_log.append(
                {
                    "staging_dir": staging_dir,
                    "exists_at_call": staging_dir.is_dir(),
                    "absent_items": list(kwargs["absent_items"]),
                    "parameters_path": kwargs["parameters_path"],
                }
            )
            return fake_publish.return_value

        fake_publish.side_effect = _record_call
    fake_reconcile = MagicMock(return_value=reconcile_report or _make_fake_report())

    doubles_kwargs = {
        "_snapshot_fn": fake_snapshot,
        "_publish_fn": fake_publish,
        "_reconcile_fn": fake_reconcile,
    }
    return fake_publish, fake_snapshot, fake_reconcile, doubles_kwargs


# ---------------------------------------------------------------------------
# Test 1 -- PUBLISH-01 SemVer-safe default path (with_publish=False)
# ---------------------------------------------------------------------------


def test_default_path_byte_identical_with_publish_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUBLISH-01: with_publish=False (default) is byte-identical to v3.0.

    The emitted DeployRecord has ``release_id`` starting ``"sync-"`` but
    NOT ``"sync-publish-"``. ``test_evidence`` carries the v3.0
    ``sync_engine_outcome`` key, NOT the Phase 17 ``provider`` key.
    """
    nb = tmp_path / "nb.ipynb"
    _write_minimal_ipynb(nb)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    # W4.4: inject the reconcile fake via apply_sync's _reconcile_fn
    # kwarg instead of monkey-patching the module.
    fake_reconcile = lambda *a, **k: _make_fake_report()  # noqa: E731

    workspace_id = "ws-default-path"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            report = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                _reconcile_fn=fake_reconcile,
                # with_publish defaults to False
            )

    assert report.outcome == "succeeded"
    assert report.deploy_record_release_id.startswith("sync-")
    assert not report.deploy_record_release_id.startswith("sync-publish-")

    # Verify the on-disk DeployRecord shape.
    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert "sync_engine_outcome" in payload["test_evidence"]
    assert "provider" not in payload["test_evidence"]


# ---------------------------------------------------------------------------
# Test 2 -- PUBLISH-02 first-time N items emits combined record
# ---------------------------------------------------------------------------


def test_with_publish_first_time_n_items_emits_combined_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUBLISH-02: empty workspace + 3 manifest items -> 1 combined DeployRecord."""
    sources = []
    for i in range(3):
        f = tmp_path / f"nb{i}.ipynb"
        _write_minimal_ipynb(f)
        sources.append(f)
    sync_yml = tmp_path / "sync.yml"
    items_yaml = (
        f"  - local_path: '{sources[0]}'\n"
        "    type: Notebook\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'A'\n"
        f"  - local_path: '{sources[1]}'\n"
        "    type: DataPipeline\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'B'\n"
        f"  - local_path: '{sources[2]}'\n"
        "    type: SemanticModel\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'C'\n"
    )
    _write_sync_yml(sync_yml, items_yaml=items_yaml)
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    # Empty workspace snapshot -> all 3 manifest items are absent.
    publish_result = PublishResult(
        outcome="succeeded",
        published_items=["A.Notebook", "B.DataPipeline", "C.SemanticModel"],
        failed_item=None,
    )
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=[],
        publish_result=publish_result,
    )

    workspace_id = "ws-first-time"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            report = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    assert report.outcome == "succeeded"
    assert report.deploy_record_release_id.startswith("sync-publish-")

    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["release_id"].startswith("sync-publish-")
    assert payload["test_evidence"]["provider"] == "sync-engine-publish"
    assert payload["test_evidence"]["outcome"] == "succeeded"
    assert json.loads(payload["test_evidence"]["published_items"]) == [
        "A.Notebook",
        "B.DataPipeline",
        "C.SemanticModel",
    ]
    assert json.loads(payload["test_evidence"]["moved_items"]) == []


# ---------------------------------------------------------------------------
# Test 3 -- PUBLISH-03 mixed state combined record
# ---------------------------------------------------------------------------


def test_with_publish_mixed_state_combined_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUBLISH-03: 2 existing + 3 new manifest items -> 1 combined record both arrays populated."""
    sources = []
    for i in range(5):
        f = tmp_path / f"nb{i}.ipynb"
        _write_minimal_ipynb(f)
        sources.append(f)
    sync_yml = tmp_path / "sync.yml"
    items_yaml = (
        f"  - local_path: '{sources[0]}'\n"
        "    type: Notebook\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'Existing1'\n"
        f"  - local_path: '{sources[1]}'\n"
        "    type: Notebook\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'Existing2'\n"
        f"  - local_path: '{sources[2]}'\n"
        "    type: Notebook\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'New1'\n"
        f"  - local_path: '{sources[3]}'\n"
        "    type: DataPipeline\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'New2'\n"
        f"  - local_path: '{sources[4]}'\n"
        "    type: SemanticModel\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'New3'\n"
    )
    _write_sync_yml(sync_yml, items_yaml=items_yaml)
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    # Workspace already has Existing1.Notebook + Existing2.Notebook.
    snapshot_items = [
        _make_item("Existing1", "Notebook"),
        _make_item("Existing2", "Notebook"),
    ]
    publish_result = PublishResult(
        outcome="succeeded",
        published_items=["New1.Notebook", "New2.DataPipeline", "New3.SemanticModel"],
        failed_item=None,
    )
    # Audit-record correctness: the reconciler ACTUALLY reparents the
    # two existing items into /raw, so plan.move_items contains them.
    # Without this explicit fake_report, plan.move_items would be empty
    # and the corrected `moved_items` assertion below would fail.
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=snapshot_items,
        publish_result=publish_result,
        reconcile_report=_make_fake_report_for_moves(
            [("Existing1", "Notebook"), ("Existing2", "Notebook")]
        ),
    )

    workspace_id = "ws-mixed"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["test_evidence"]["outcome"] == "succeeded"
    moved = json.loads(payload["test_evidence"]["moved_items"])
    published = json.loads(payload["test_evidence"]["published_items"])
    assert len(moved) == 2
    assert len(published) == 3
    assert set(moved) == {"Existing1.Notebook", "Existing2.Notebook"}
    assert set(published) == {
        "New1.Notebook",
        "New2.DataPipeline",
        "New3.SemanticModel",
    }


# ---------------------------------------------------------------------------
# Test 4 -- PUBLISH-05 partial-failure folder-reconcile-still-committed
# ---------------------------------------------------------------------------


def test_with_publish_partial_failure_outcome_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUBLISH-05: partial-failure outcome, folder reconcile committed.

    The SyncApplyReport's outcome stays "succeeded" because the FOLDER
    reconcile completed successfully -- the publish failure is recorded
    in the audit ledger only.
    """
    sources = [tmp_path / f"nb{i}.ipynb" for i in range(2)]
    for f in sources:
        _write_minimal_ipynb(f)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{sources[0]}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
            f"  - local_path: '{sources[1]}'\n"
            "    type: DataPipeline\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'B'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    publish_result = PublishResult(
        outcome="partial-failure",
        published_items=["A.Notebook"],
        failed_item="B.DataPipeline",
    )
    # Folder reconcile reports 2 folders created / 1 item moved -- non-zero
    # so the test can assert "reconcile committed".
    fake_report = _make_fake_report_with_moves(folders_created=2, items_moved=1)
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=[],
        publish_result=publish_result,
        reconcile_report=fake_report,
    )

    workspace_id = "ws-partial"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            report = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    # Reconcile-side outcome is still "succeeded" (PUBLISH-05).
    assert report.outcome == "succeeded"
    assert report.folders_created > 0  # reconcile committed

    # Combined record carries the publish-side outcome.
    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["test_evidence"]["outcome"] == "partial-failure"
    assert payload["test_evidence"]["failed_item"] == "B.DataPipeline"
    assert payload["test_evidence"]["published_items"] == json.dumps(["A.Notebook"])


# ---------------------------------------------------------------------------
# Test 5 -- full failure outcome
# ---------------------------------------------------------------------------


def test_with_publish_full_failure_outcome_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """outcome=failed -> failed_item key absent (None), published_items=[]."""
    nb = tmp_path / "nb.ipynb"
    _write_minimal_ipynb(nb)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    publish_result = PublishResult(outcome="failed", published_items=[], failed_item=None)
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch, snapshot_items=[], publish_result=publish_result
    )

    workspace_id = "ws-full-fail"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["test_evidence"]["outcome"] == "failed"
    assert payload["test_evidence"]["published_items"] == "[]"
    # failed_item absent because PublishResult.failed_item is None.
    assert "failed_item" not in payload["test_evidence"]


# ---------------------------------------------------------------------------
# Test 6 -- D-15 / D-19 dry-run + with_publish skips publish
# ---------------------------------------------------------------------------


def test_with_publish_dry_run_skips_publish_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dry_run=True + with_publish=True -> publish never called, no DeployRecord emitted."""
    nb = tmp_path / "nb.ipynb"
    _write_minimal_ipynb(nb)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    fake_publish, _, _, doubles_kwargs = _patch_publish_path(monkeypatch)

    workspace_id = "ws-dry-publish"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            report = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                dry_run=True,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    fake_publish.assert_not_called()
    assert report.outcome == "dry_run"
    assert not (audit_dir / "deploys.jsonl").exists()


# ---------------------------------------------------------------------------
# Test 7 -- combined record audit hash verifies (T-17-04)
# ---------------------------------------------------------------------------


def test_combined_record_audit_hash_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-17-04: round-trip the combined record through JSONL + verify_hash() == True."""
    nb = tmp_path / "nb.ipynb"
    _write_minimal_ipynb(nb)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    publish_result = PublishResult(
        outcome="succeeded", published_items=["A.Notebook"], failed_item=None
    )
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch, snapshot_items=[], publish_result=publish_result
    )

    workspace_id = "ws-hash"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    record = DeployRecord(**payload)
    assert record.verify_hash() is True


# ---------------------------------------------------------------------------
# Test 8 -- empty absent set still emits combined record
# ---------------------------------------------------------------------------


def test_with_publish_empty_absent_set_emits_record_with_empty_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workspace already has all manifest items -> empty published_items, full moved_items."""
    sources = [tmp_path / f"nb{i}.ipynb" for i in range(2)]
    for f in sources:
        _write_minimal_ipynb(f)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{sources[0]}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
            f"  - local_path: '{sources[1]}'\n"
            "    type: DataPipeline\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'B'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    snapshot_items = [
        _make_item("A", "Notebook"),
        _make_item("B", "DataPipeline"),
    ]
    publish_result = PublishResult(outcome="succeeded", published_items=[], failed_item=None)
    # Audit-record correctness: this test's narrative is "items existed
    # AND got reparented" -- the reconciler's plan must reflect both
    # moves, otherwise the fixed implementation will report
    # moved_items=[] (which is the new dedicated test
    # `test_with_publish_idempotent_rerun_moved_items_empty` below).
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=snapshot_items,
        publish_result=publish_result,
        reconcile_report=_make_fake_report_for_moves([("A", "Notebook"), ("B", "DataPipeline")]),
    )

    workspace_id = "ws-all-existing"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    moved = json.loads(payload["test_evidence"]["moved_items"])
    published = json.loads(payload["test_evidence"]["published_items"])
    assert len(published) == 0
    assert len(moved) == 2
    assert set(moved) == {"A.Notebook", "B.DataPipeline"}


def test_with_publish_idempotent_rerun_moved_items_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotent rerun: items already at target -> moved_items=[] (audit-record correctness).

    Surfaced by the 2026-05-06 live ``--with-publish`` run against
    ``COE_F_SBDEVOPS_POC`` workspace ``effa6941-0717-4578-b8e5-95339152f4b2``.
    The second invocation of the same command produced
    ``items_moved=0`` on the CLI summary line (correct -- no
    reconciler ops needed) but the persisted ``DeployRecord``
    listed all three manifest items in
    ``test_evidence.moved_items``. Audit-ledger consumers
    reconstructing the change set would over-count moves on every
    no-op rerun.

    The bug was at ``sigantry_core/sync/apply.py``: ``moved_items``
    was filtered from ``existing_set`` (membership in the
    pre-apply snapshot), not from ``report.plan.move_items`` (the
    reconciler's actual operations). On idempotent reruns every
    item is in ``existing_set`` even though the plan has zero
    moves. Fix: thread ``plan.move_items`` through as the truth
    source.

    Falsifiability contract: this test FAILS against the
    pre-fix implementation -- the assertion ``moved == []`` would
    instead receive ``["A.Notebook", "B.DataPipeline"]``.
    """
    sources = [tmp_path / f"nb{i}.ipynb" for i in range(2)]
    for f in sources:
        _write_minimal_ipynb(f)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{sources[0]}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
            f"  - local_path: '{sources[1]}'\n"
            "    type: DataPipeline\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'B'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    # Both items already present in the workspace (existing_set hit).
    snapshot_items = [
        _make_item("A", "Notebook"),
        _make_item("B", "DataPipeline"),
    ]
    publish_result = PublishResult(outcome="succeeded", published_items=[], failed_item=None)
    # Reconciler plan is EMPTY -- both items are already at their
    # target folder, no moves required. This is the load-bearing
    # difference from `test_with_publish_empty_absent_set_emits_record_with_empty_published`
    # above, which mocks 2 actual moves.
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=snapshot_items,
        publish_result=publish_result,
        # Default `_make_fake_report()` returns empty plan; passing
        # explicitly to make the contract obvious.
        reconcile_report=_make_fake_report(),
    )

    workspace_id = "ws-idempotent-rerun"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            report = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    # CLI-summary truth signal: zero moves planned.
    assert report.items_moved == 0
    assert report.folders_created == 0

    # Persisted record: moved_items must reflect the empty plan.
    line = (audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    moved = json.loads(payload["test_evidence"]["moved_items"])
    published = json.loads(payload["test_evidence"]["published_items"])
    assert moved == [], (
        f"audit-record bug regression: moved_items should be empty on idempotent "
        f"rerun (no reconciler operations) but got {moved!r}. The implementation "
        f"is using existing_set membership instead of plan.move_items."
    )
    assert published == []
    # `fabric_items_changed` derives from `moved_items | published_items`;
    # both empty means the combined set is empty too -- the record still
    # exists for the audit trail (provider=sync-engine-publish) but
    # accurately reports zero changes.
    assert payload["fabric_items_changed"] == []


# ---------------------------------------------------------------------------
# Test 9 -- D-17-07 staging-tempdir lifetime
# ---------------------------------------------------------------------------


def test_with_publish_staging_tempdir_lives_until_publish_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-17-07: the staging dir MUST exist at the moment publish_absent_items runs.

    Without this, fabric-cicd raises a confusing FileNotFoundError from
    inside its workspace walker. The test instruments the publish mock
    to record ``staging_dir.is_dir()`` at call-time.
    """
    nb = tmp_path / "nb.ipynb"
    _write_minimal_ipynb(nb)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    publish_call_log: list = []
    publish_result = PublishResult(
        outcome="succeeded", published_items=["A.Notebook"], failed_item=None
    )
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=[],
        publish_result=publish_result,
        publish_call_log=publish_call_log,
    )

    workspace_id = "ws-tempdir"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    assert len(publish_call_log) == 1
    call = publish_call_log[0]
    assert call["exists_at_call"] is True, (
        f"staging_dir {call['staging_dir']} did NOT exist when publish was invoked"
    )
    # parameters_path must be the substituted tempfile, not the original.
    assert call["parameters_path"] != params
    assert "sigantry-sync-publish-params-" in str(call["parameters_path"])


# ---------------------------------------------------------------------------
# Test 10 -- D-17-04 release_id prefix
# ---------------------------------------------------------------------------


def test_with_publish_combined_record_release_id_distinct_from_sync_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-17-04: release_id MUST start with 'sync-publish-' followed by a UTC
    timestamp; MUST NOT match the v3.0 'sync-<TS>' pattern.
    """
    nb = tmp_path / "nb.ipynb"
    _write_minimal_ipynb(nb)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    publish_result = PublishResult(
        outcome="succeeded", published_items=["A.Notebook"], failed_item=None
    )
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch, snapshot_items=[], publish_result=publish_result
    )

    workspace_id = "ws-release-id"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            report = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                params_path=params,
                **doubles_kwargs,
            )

    rid = report.deploy_record_release_id
    assert re.match(r"^sync-publish-\d{4}-\d{2}-\d{2}T", rid), rid
    # The 'sync-publish-' prefix is exclusive: the v3.0 'sync-<TS>' regex
    # would match `sync-2026-...` but NOT `sync-publish-2026-...`.
    assert not re.match(r"^sync-\d{4}-\d{2}-\d{2}T", rid), rid


# ---------------------------------------------------------------------------
# Test 12 -- D-17-10 republish_existing: publish set = FULL manifest
# ---------------------------------------------------------------------------


def _republish_setup(tmp_path: Path):
    """Shared fixture for the republish_existing contrast pair.

    Manifest has A + B (already in the workspace) and C (absent). Returns
    ``(sync_yml, params, audit_dir, snapshot_items)``.
    """
    sources = [tmp_path / f"nb{i}.ipynb" for i in range(3)]
    for f in sources:
        _write_minimal_ipynb(f)
    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{sources[0]}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'A'\n"
            f"  - local_path: '{sources[1]}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'B'\n"
            f"  - local_path: '{sources[2]}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'C'\n"
        ),
    )
    params = tmp_path / "parameters.yml"
    _write_minimal_parameters_yml(params)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    snapshot_items = [_make_item("A", "Notebook"), _make_item("B", "Notebook")]
    return sync_yml, params, audit_dir, snapshot_items


def test_republish_existing_publishes_full_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-17-10: ``republish_existing=True`` passes the FULL manifest (present
    A + B AND absent C) to the publish helper, so fabric-cicd
    updateDefinition's the already-present items.

    The capture is on the kwargs ACTUALLY forwarded to the publish helper
    (via ``publish_call_log``) -- this pins the filter behaviour, not just
    the mock's canned return value.
    """
    sync_yml, params, audit_dir, snapshot_items = _republish_setup(tmp_path)
    publish_call_log: list = []
    publish_result = PublishResult(
        outcome="succeeded",
        published_items=["A.Notebook", "B.Notebook", "C.Notebook"],
        failed_item=None,
    )
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=snapshot_items,
        publish_result=publish_result,
        publish_call_log=publish_call_log,
        reconcile_report=_make_fake_report_for_moves([("A", "Notebook"), ("B", "Notebook")]),
    )

    workspace_id = "ws-republish-on"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                republish_existing=True,
                params_path=params,
                **doubles_kwargs,
            )

    assert len(publish_call_log) == 1
    passed_keys = {(it.display_name, it.type) for it in publish_call_log[0]["absent_items"]}
    assert passed_keys == {("A", "Notebook"), ("B", "Notebook"), ("C", "Notebook")}


def test_republish_existing_off_publishes_absent_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-17-10 contrast: default (``republish_existing=False``) still passes
    ONLY the absent subset (C) to the publish helper -- the present items
    A + B are NOT republished. Locks the SemVer-safe default filter.
    """
    sync_yml, params, audit_dir, snapshot_items = _republish_setup(tmp_path)
    publish_call_log: list = []
    publish_result = PublishResult(
        outcome="succeeded",
        published_items=["C.Notebook"],
        failed_item=None,
    )
    _, _, _, doubles_kwargs = _patch_publish_path(
        monkeypatch,
        snapshot_items=snapshot_items,
        publish_result=publish_result,
        publish_call_log=publish_call_log,
        reconcile_report=_make_fake_report_for_moves([("A", "Notebook"), ("B", "Notebook")]),
    )

    workspace_id = "ws-republish-off"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=True,
                # republish_existing defaults to False
                params_path=params,
                **doubles_kwargs,
            )

    assert len(publish_call_log) == 1
    passed_keys = {(it.display_name, it.type) for it in publish_call_log[0]["absent_items"]}
    assert passed_keys == {("C", "Notebook")}


def test_republish_existing_requires_with_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-17-10 guard: ``republish_existing=True`` without ``with_publish``
    raises ``SyncPublishError`` rather than silently no-op'ing.
    """
    sync_yml, params, audit_dir, _ = _republish_setup(tmp_path)
    _, _, _, doubles_kwargs = _patch_publish_path(monkeypatch, snapshot_items=[])

    workspace_id = "ws-republish-guard"
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with (
            _client_with_mock_token() as client,
            pytest.raises(SyncPublishError, match="requires with_publish"),
        ):
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
                with_publish=False,
                republish_existing=True,
                params_path=params,
                **doubles_kwargs,
            )
