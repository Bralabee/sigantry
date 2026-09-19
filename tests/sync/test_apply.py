"""Tests for ``sigantry_core.sync.apply`` -- Plan 13-04 / SYNC-04, SYNC-06.

Replaces the Wave 0 xfail stubs with real assertions covering D-14 (one
DeployRecord per apply), D-16 (failure record with typed reason), D-17
(tempdir preserved on failure), D-18 (Git-Sync pre-flight), D-19
(--dry-run path), and the SPEC SYNC-04 / Round-4 idempotency gate.

The idempotency test (``test_apply_idempotent_second_run_no_op``) uses a
stateful respx-driven fake of the Fabric workspace REST surface -- NOT a
monkeypatched reconciler -- so a regression in any of the five layers
(manifest validation, packager sidecar persistence, snapshot, reconciler
join-by-folder-id, apply) flips the test red.

The snapshot CLI test originally listed at the bottom of the Wave 0 stub
list moved to ``tests/sync/test_cli_sync.py`` (Task 2 of Plan 13-04) so
the apply test file stays focused on apply-engine correctness.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import FABRIC_AUDIENCE
from sigantry_core.client import FabricRestClient
from sigantry_core.release.record import DeployRecord
from sigantry_core.sync.apply import apply_sync
from sigantry_core.sync.errors import (
    ReconcilerWrapError,
    WorkspacePendingGitUpdateError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_with_mock_token() -> FabricRestClient:
    """Build a FabricRestClient whose token provider is mocked.

    Mirrors the helper in ``tests/sync/test_snapshot.py`` -- the real
    BaseRestClient stays in the loop so respx can intercept httpx, but
    the OAuth chain is short-circuited so no live auth is attempted.
    """
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _write_minimal_ipynb(path: Path) -> None:
    """Write a minimal valid ``.ipynb`` JSON document to ``path``."""
    payload = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print('hi')\n"],
            }
        ],
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


def _write_sync_yml(
    target: Path,
    *,
    items_yaml: str,
) -> None:
    """Write a ``sync.yml`` file with ``schema_version: '1.0.0'`` and the items block."""
    target.write_text(
        f"schema_version: '1.0.0'\nitems:\n{items_yaml}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Test 1 -- execution flow (packager THEN reconciler; pre-flight first)
# ---------------------------------------------------------------------------


def test_apply_calls_packager_then_reconcile_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-17 (W9-resolved): manifest -> normalise -> Get Workspace pre-flight ->
    packager (per item) -> reconcile_folders_from_repo -> emit DeployRecord.

    NO separate snapshot pre-fetch (W9 -- the reconciler does its own
    list_folders/list_items internally).
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    call_log: list[str] = []

    # Wrap NotebookPackager.pack so we capture each call without
    # disturbing the real sidecar persistence behaviour.
    from sigantry_core.sync.packagers.notebook import NotebookPackager

    orig_pack = NotebookPackager.pack

    def _spy_pack(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        call_log.append(f"NotebookPackager.pack:{kwargs.get('display_name')}")
        return orig_pack(self, *args, **kwargs)

    monkeypatch.setattr(NotebookPackager, "pack", _spy_pack)

    from sigantry_core.sync import apply as apply_mod

    def _fake_reconcile(*_args, **_kwargs):
        call_log.append("reconcile_folders_from_repo")
        from sigantry_core.workspace.reconciler import ReconcilePlan, ReconcileReport

        plan = ReconcilePlan(workspace_id=_kwargs.get("workspace_id", "ws-x"))
        return ReconcileReport(plan=plan)

    monkeypatch.setattr(apply_mod, "reconcile_folders_from_repo", _fake_reconcile)

    workspace_id = "ws-test-flow"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        # GET workspace -> no gitConnection (apply path is allowed).
        get_ws = router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": workspace_id,
                    "displayName": "test",
                    "gitConnection": None,
                },
            )
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

        # Pre-flight call must have fired (D-18 invariant).
        assert get_ws.called

    # Order: pre-flight (HTTP, captured by respx) -> packager(s) -> reconciler.
    assert call_log == [
        "NotebookPackager.pack:Nb1",
        "reconcile_folders_from_repo",
    ]


def test_apply_resolves_relative_local_path_against_manifest_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UAT-FOUND-3 regression: ``local_path`` resolves against manifest dir.

    The Phase 13 D-22 round-trip (sync pull -> sync apply) writes the
    manifest into the pulled tree (e.g. /tmp/pulled/sync.yml) with
    ``local_path`` values relative to that pulled tree (e.g.
    ``AIMS/MyNb/notebook-content.ipynb``). When the operator invokes
    ``sigantry sync apply --manifest /tmp/pulled/sync.yml`` from an
    arbitrary CWD (e.g. their project root), the resolution MUST happen
    against the manifest's parent dir, NOT the CWD. Otherwise the
    packager fails with ``source missing`` / ``source must be an .ipynb
    file``.

    This test forces that scenario: create the source under a pulled-
    style tree, then run apply_sync from an unrelated CWD and assert the
    packager receives the correct absolute path.
    """
    pulled_tree = tmp_path / "pulled"
    nb_dir = pulled_tree / "AIMS" / "MyNb"
    nb_dir.mkdir(parents=True)
    nb_file = nb_dir / "notebook-content.ipynb"
    _write_minimal_ipynb(nb_file)

    sync_yml = pulled_tree / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            "  - local_path: 'AIMS/MyNb/notebook-content.ipynb'\n"  # relative
            "    type: Notebook\n"
            "    target_folder: '/AIMS'\n"
            "    display_name: 'MyNb'\n"
        ),
    )

    captured_sources: list[Path] = []
    from sigantry_core.sync.packagers.notebook import NotebookPackager

    orig_pack = NotebookPackager.pack

    def _spy_pack(self, source, **kwargs):  # type: ignore[no-untyped-def]
        captured_sources.append(Path(source))
        return orig_pack(self, source, **kwargs)

    monkeypatch.setattr(NotebookPackager, "pack", _spy_pack)

    from sigantry_core.sync import apply as apply_mod

    def _fake_reconcile(*_args, **_kwargs):
        from sigantry_core.workspace.reconciler import ReconcilePlan, ReconcileReport

        plan = ReconcilePlan(workspace_id=_kwargs.get("workspace_id", "ws-x"))
        return ReconcileReport(plan=plan)

    monkeypatch.setattr(apply_mod, "reconcile_folders_from_repo", _fake_reconcile)

    # CWD intentionally NOT pulled_tree -- prove resolution is manifest-dir-relative.
    foreign_cwd = tmp_path / "other_dir"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    workspace_id = "ws-roundtrip-resolve"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(
                200,
                json={"id": workspace_id, "displayName": "rt", "gitConnection": None},
            )
        )
        with _client_with_mock_token() as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

    assert len(captured_sources) == 1
    resolved = captured_sources[0]
    # The packager must have received the manifest-dir-resolved path,
    # not the CWD-relative one (which would have pointed at
    # foreign_cwd / 'AIMS/MyNb/notebook-content.ipynb' -- nonexistent).
    assert resolved == pulled_tree / "AIMS" / "MyNb" / "notebook-content.ipynb"


# ---------------------------------------------------------------------------
# Test 2 -- success-path DeployRecord (D-14)
# ---------------------------------------------------------------------------


def test_apply_emits_deploy_record_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-14: ONE DeployRecord per apply with provider='sync-engine' and release_id='sync-<TS>'."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    from sigantry_core.sync import apply as apply_mod
    from sigantry_core.workspace.reconciler import ReconcilePlan, ReconcileReport

    monkeypatch.setattr(
        apply_mod,
        "reconcile_folders_from_repo",
        lambda *a, **k: ReconcileReport(
            plan=ReconcilePlan(workspace_id=k.get("workspace_id", "ws-x"))
        ),
    )

    workspace_id = "ws-record-success"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

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
            )

    assert report.outcome == "succeeded"
    jsonl = audit_dir / "deploys.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = DeployRecord(**json.loads(lines[0]))
    assert record.release_id.startswith("sync-")
    assert record.approver == "sync-engine"
    assert record.test_evidence["sync_engine_outcome"] == "succeeded"
    # fabric_items_changed contains "Nb1.Notebook" per the manifest item.
    assert record.fabric_items_changed == ["Nb1.Notebook"]


# ---------------------------------------------------------------------------
# Test 3 -- failure DeployRecord with typed failure_reason (D-16)
# ---------------------------------------------------------------------------


def test_apply_emits_deploy_record_with_failed_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-16: failure path emits DeployRecord with sync_engine_outcome='failed'
    and sync_engine_failure=<typed-class-name>."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    from sigantry_core.sync import apply as apply_mod

    def _boom(*_a, **_k):
        raise RuntimeError("synthetic reconciler boom")

    monkeypatch.setattr(apply_mod, "reconcile_folders_from_repo", _boom)

    workspace_id = "ws-record-failed"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client, pytest.raises(ReconcilerWrapError):
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

    jsonl = audit_dir / "deploys.jsonl"
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = DeployRecord(**json.loads(lines[0]))
    assert record.test_evidence["sync_engine_outcome"] == "failed"
    assert record.test_evidence["sync_engine_failure"] == "ReconcilerWrapError"

    # Cleanup the preserved staging tempdir so /tmp doesn't accumulate
    # cruft across test runs.
    for tmpdir in Path("/tmp").glob("sigantry-sync-*"):
        if tmpdir.is_dir():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 4 -- LOAD-BEARING idempotency (SPEC SYNC-04 / Round-4 falsifiability)
# ---------------------------------------------------------------------------


def test_apply_idempotent_second_run_no_op(tmp_path: Path) -> None:
    """LOAD-BEARING: apply twice against a stateful respx fake; second run no-op.

    Why this test matters: per SPEC SYNC-04, "applying the same sync.yml
    twice against a fresh workspace; second run's plan reports zero
    create_folder / move_item / create_item operations." This test
    falsifies that invariant against real production code -- only the
    HTTP transport is faked.

    The fake's in-memory ``_state`` mutates as the reconciler creates
    folders and (when needed) moves items. The first apply finds an
    empty workspace and creates the ``/raw`` folder; the second apply
    finds the same workspace state plus the now-existing folder and
    correctly identifies that no work is needed.

    Convergence is also load-bearing on the sidecar logical_id
    persistence (D-11): the second packager run reads the same
    UUIDs back, so the manifest items resolve to the same ``.platform``
    logicalId values they did on the first run. A regression in
    sidecar persistence would mint NEW UUIDs and the test flips red.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    nb2 = source_dir / "nb2.ipynb"
    _write_minimal_ipynb(nb1)
    _write_minimal_ipynb(nb2)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
            f"  - local_path: '{nb2}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb2'\n"
        ),
    )

    workspace_id = "ws-idempotent"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    state: dict[str, list[dict]] = {"folders": [], "items": []}
    counter = {"folder": 0, "item": 0}

    def _list_folders(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"value": list(state["folders"]), "continuationToken": None}
        )

    def _list_items(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": list(state["items"]), "continuationToken": None})

    def _get_workspace(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": workspace_id, "displayName": "test", "gitConnection": None},
        )

    def _create_folder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        counter["folder"] += 1
        new_id = f"folder-{counter['folder']}"
        folder = {
            "id": new_id,
            "displayName": body["displayName"],
            "parentFolderId": body.get("parentFolderId"),
            "workspaceId": workspace_id,
        }
        state["folders"].append(folder)
        return httpx.Response(201, json=folder)

    def _move_item(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        match = re.search(r"/items/([^/]+)/move", str(request.url))
        assert match is not None
        item_id = match.group(1)
        for it in state["items"]:
            if it["id"] == item_id:
                it["folderId"] = body.get("targetFolderId")
                break
        return httpx.Response(200, json={})

    # ``assert_all_called=False`` -- the move endpoint is registered
    # for completeness (the reconciler would call it if any of the
    # manifest items already existed in the workspace state) but it
    # is not exercised in the empty-then-empty workspace scenario;
    # the reconciler treats both notebooks as ``unresolved_items``
    # (absent from workspace yet) on both runs. The idempotency
    # invariant lives in ``plan.create_folders`` + ``plan.move_items``,
    # both of which converge to empty after the first apply.
    with respx.mock(base_url=FABRIC_AUDIENCE, assert_all_called=False) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(side_effect=_get_workspace)
        router.get(f"/v1/workspaces/{workspace_id}/folders").mock(side_effect=_list_folders)
        router.get(f"/v1/workspaces/{workspace_id}/items").mock(side_effect=_list_items)
        router.post(f"/v1/workspaces/{workspace_id}/folders").mock(side_effect=_create_folder)
        router.post(
            url__regex=re.escape(f"/v1/workspaces/{workspace_id}/items/") + r"[^/]+/move",
        ).mock(side_effect=_move_item)

        # First apply: empty workspace; reconciler must create ``/raw``.
        # The two notebooks themselves are not pre-existing items in
        # the fake, so the reconciler treats them as "unresolved" --
        # ``apply_sync`` does not call publishing because that's
        # fabric-cicd's job; reconcile_folders_from_repo only ensures
        # folders exist + items already in workspace are routed
        # correctly. The idempotency invariant lives in
        # ``plan.create_folders`` + ``plan.move_items`` -- both of
        # which must be empty on the second run.
        with _client_with_mock_token() as client:
            report1 = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )
        # First apply staged 2 notebooks; folder /raw was created.
        assert report1.items_packaged == 2
        assert report1.folders_created >= 1, (
            "First apply should plan at least one folder create (/raw); "
            f"got plan with folders_created={report1.folders_created}"
        )

        # Verify the sidecar persisted both ids.
        sidecar = source_dir / ".sigantry" / "notebook-ids.json"
        assert sidecar.exists()
        sidecar_payload_a = sidecar.read_bytes()
        ids_a = json.loads(sidecar_payload_a.decode("utf-8"))["ids"]
        assert sorted(ids_a.keys()) == ["nb1.ipynb", "nb2.ipynb"]
        id1, id2 = ids_a["nb1.ipynb"], ids_a["nb2.ipynb"]

        # Workspace fake should now have the /raw folder.
        assert len(state["folders"]) >= 1

        # Second apply: same manifest, same fake state.
        with _client_with_mock_token() as client:
            report2 = apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )
        assert report2.items_packaged == 2
        # SECOND-RUN INVARIANT: zero create_folder, zero move_item.
        assert report2.folders_created == 0, (
            "Idempotency violated: second apply re-created folders. "
            f"plan.folders_created={report2.folders_created}"
        )
        assert report2.items_moved == 0, (
            "Idempotency violated: second apply re-moved items. "
            f"plan.items_moved={report2.items_moved}"
        )

        # Sidecar bytes must be byte-identical (no UUID re-mint).
        sidecar_payload_b = sidecar.read_bytes()
        assert sidecar_payload_b == sidecar_payload_a
        ids_b = json.loads(sidecar_payload_b.decode("utf-8"))["ids"]
        assert ids_b["nb1.ipynb"] == id1
        assert ids_b["nb2.ipynb"] == id2


# ---------------------------------------------------------------------------
# Test 5 -- D-18 Git-Sync pre-flight refusal + failure DeployRecord
# ---------------------------------------------------------------------------


def test_apply_refuses_when_workspace_has_pending_git_sync(
    tmp_path: Path,
) -> None:
    """D-18 + W3: pending Git Sync raises WorkspacePendingGitUpdateError;
    the failure DeployRecord IS emitted (audit invariant per D-16)."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    workspace_id = "ws-pending-git"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": workspace_id,
                    "gitConnection": {"sync_state": "Pending"},
                },
            )
        )
        with (
            _client_with_mock_token() as client,
            pytest.raises(WorkspacePendingGitUpdateError) as exc_info,
        ):
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

    assert exc_info.value.sync_state == "Pending"

    # W3: failure DeployRecord IS emitted (manifest was bound before
    # the pre-flight check fired, so the audit invariant per D-16
    # applies -- every failed apply that reached past load_manifest
    # gets a failure DeployRecord).
    jsonl = audit_dir / "deploys.jsonl"
    assert jsonl.exists()
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = DeployRecord(**json.loads(lines[0]))
    assert record.test_evidence["sync_engine_outcome"] == "failed"
    assert record.test_evidence["sync_engine_failure"] == ("WorkspacePendingGitUpdateError")

    # Cleanup the preserved tempdir.
    for tmpdir in Path("/tmp").glob("sigantry-sync-*"):
        if tmpdir.is_dir():
            shutil.rmtree(tmpdir, ignore_errors=True)


def test_apply_refuses_when_workspace_pending_git_sync_camelcase(
    tmp_path: Path,
) -> None:
    """WR-03: Fabric REST returns ``syncState`` (camelCase); pre-flight refusal MUST fire.

    REVIEW.md WR-03: the existing test exercises the snake_case
    ``sync_state`` fallback path; the canonical Fabric REST surface
    returns camelCase per Microsoft Learn. This test asserts the
    pre-flight gate also catches the camelCase path so a refactor that
    drops the canonical branch flips this test red.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    workspace_id = "ws-pending-git-camel"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": workspace_id,
                    # Canonical Microsoft Learn shape -- camelCase.
                    "gitConnection": {"syncState": "Pending"},
                },
            )
        )
        with (
            _client_with_mock_token() as client,
            pytest.raises(WorkspacePendingGitUpdateError) as exc_info,
        ):
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

    assert exc_info.value.sync_state == "Pending"

    # Cleanup the preserved tempdir.
    for tmpdir in Path("/tmp").glob("sigantry-sync-*"):
        if tmpdir.is_dir():
            shutil.rmtree(tmpdir, ignore_errors=True)


def test_apply_fails_closed_when_git_bound_workspace_has_no_sync_state(
    tmp_path: Path,
) -> None:
    """WR-06: a Git-bound workspace lacking syncState MUST raise (fail closed).

    REVIEW.md WR-06: pre-fix the check returned silently when
    ``sync_state`` was ``None``, treating "no field" as "safe to
    apply". The Fabric API contract is that a Git-bound workspace
    reports sync state, so absence is a malformed response or
    mid-transition -- not a green light. Fail-closed: raise
    ``WorkspacePendingGitUpdateError`` with ``sync_state=None`` so the
    operator investigates before retrying.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    workspace_id = "ws-git-no-syncstate"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": workspace_id,
                    # Git-bound workspace whose payload omits the
                    # syncState field -- pre-fix this returned silently
                    # and the apply proceeded against an indeterminate
                    # state. Post-fix it must refuse with sync_state=None.
                    "gitConnection": {
                        "gitProviderDetails": {
                            "organizationName": "test-org",
                            "projectName": "test-proj",
                            "repositoryName": "test-repo",
                            "branchName": "main",
                        }
                    },
                },
            )
        )
        with (
            _client_with_mock_token() as client,
            pytest.raises(WorkspacePendingGitUpdateError) as exc_info,
        ):
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

    assert exc_info.value.sync_state is None
    assert "indeterminate" in str(exc_info.value).lower() or (
        "no syncState" in str(exc_info.value) or "sync_state field" in str(exc_info.value)
    )

    # Cleanup the preserved tempdir.
    for tmpdir in Path("/tmp").glob("sigantry-sync-*"):
        if tmpdir.is_dir():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Test 6 -- SYNC-06 folder-less type override
# ---------------------------------------------------------------------------


def test_apply_warns_on_folderless_item_types_and_routes_to_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SYNC-06: dataflow items route to workspace root regardless of declared target_folder.

    The Plan 13-01 manifest validator emits the
    ``folder_less_type_override`` log warning at validate time;
    :meth:`SyncManifest.normalised` rewrites ``target_folder`` to
    ``/`` for those items. ``apply_sync`` calls ``.normalised()`` so
    the staging tree reflects the override even when the operator
    wrote ``target_folder: '/x'`` in ``sync.yml``.

    The PACKAGER_REGISTRY does NOT ship a ``Dataflow`` packager today
    (Plan 13-03 limited the v3.0 registry to Notebook + the four
    GenericPackager-keyed types). For the apply-engine SYNC-06
    invariant, this test injects a stub Dataflow packager into the
    registry so the staging tree can be inspected; the routing logic
    under test (``manifest.normalised()`` consumed by ``apply_sync``)
    is identical to what a future Dataflow packager would see.
    """
    from sigantry_core.sync.packagers import PACKAGER_REGISTRY

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)
    df_dir = source_dir / "MyDataflow"
    df_dir.mkdir()

    sync_yml = tmp_path / "sync.yml"
    # Use upstream PascalCase 'Dataflow' (the canonicalised
    # ItemType.value for "dataflow_gen2" per Plan 13-01). A target
    # folder of '/x' is overridden to '/' by SyncManifest.normalised().
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{df_dir}'\n"
            "    type: Dataflow\n"
            "    target_folder: '/x'\n"
            "    display_name: 'MyDataflow'\n"
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'MyNb'\n"
        ),
    )

    # Stub Dataflow packager: capture call kwargs + materialise a
    # minimal ``<displayName>.<Type>/.platform`` so the staging tree
    # has something to assert against.
    packager_calls: list[dict] = []

    class _StubDataflowPackager:
        def pack(
            self,
            source: Path,
            *,
            display_name: str,
            target_folder: str,
            logical_id: str | None,
            staging_dir: Path,
        ) -> Path:
            packager_calls.append(
                {
                    "display_name": display_name,
                    "target_folder": target_folder,
                    "logical_id": logical_id,
                }
            )
            sub = target_folder.lstrip("/")
            target_dir = (
                staging_dir / sub / f"{display_name}.Dataflow"
                if sub
                else staging_dir / f"{display_name}.Dataflow"
            )
            target_dir.mkdir(parents=True, exist_ok=False)
            (target_dir / ".platform").write_text("{}", encoding="utf-8")
            return target_dir

    monkeypatch.setitem(PACKAGER_REGISTRY, "Dataflow", _StubDataflowPackager())

    # The staging tempdir is rmtree'd in apply_sync's success-path
    # finally block; we snapshot it inside the reconciler shim before
    # the cleanup fires so the assertions can inspect the layout
    # post-apply.
    captured_staging: dict[str, Path] = {}

    snapshot_dir = tmp_path / "staging_snapshot"

    def _capture_reconcile(*_a, **kwargs):
        repo = Path(kwargs["repository_directory"])
        # Copy the live staging tree into the test's tmp_path so we
        # can assert against it after apply_sync's cleanup runs.
        shutil.copytree(repo, snapshot_dir)
        captured_staging["dir"] = snapshot_dir
        from sigantry_core.workspace.reconciler import (
            ReconcilePlan,
            ReconcileReport,
        )

        return ReconcileReport(plan=ReconcilePlan(workspace_id=kwargs.get("workspace_id", "ws-x")))

    from sigantry_core.sync import apply as apply_mod

    monkeypatch.setattr(apply_mod, "reconcile_folders_from_repo", _capture_reconcile)

    workspace_id = "ws-folderless"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

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
            )

    # Stub Dataflow packager saw target_folder='/' (override applied
    # by SyncManifest.normalised()) -- proving SYNC-06 routing.
    df_call = next(c for c in packager_calls if c["display_name"] == "MyDataflow")
    assert df_call["target_folder"] == "/", (
        f"SYNC-06 violated: Dataflow item still has target_folder="
        f"{df_call['target_folder']!r}; expected '/'"
    )

    staging = captured_staging["dir"]
    # Notebook routed to /raw -- staged at staging/raw/MyNb.Notebook/.
    assert (staging / "raw" / "MyNb.Notebook" / ".platform").exists()
    # Dataflow routed to / (workspace root) per SYNC-06 -- the staged
    # path is staging/MyDataflow.Dataflow/, NOT staging/x/MyDataflow.Dataflow/.
    assert (staging / "MyDataflow.Dataflow" / ".platform").exists()
    assert not (staging / "x" / "MyDataflow.Dataflow").exists()


# ---------------------------------------------------------------------------
# Test 7 -- D-19 --dry-run path
# ---------------------------------------------------------------------------


def test_apply_dry_run_prints_plan_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-19: --dry-run runs packagers + reconciler.plan but writes no audit."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    from sigantry_core.sync import apply as apply_mod
    from sigantry_core.workspace.reconciler import (
        PlannedFolderCreate,
        PlannedItemMove,
        ReconcilePlan,
        ReconcileReport,
    )

    planned_folder = PlannedFolderCreate(path=("raw",))
    planned_move = PlannedItemMove(
        item_id="i-existing",
        display_name="Nb1",
        item_type="Notebook",
        from_folder_id=None,
        to_folder_path=("raw",),
    )
    fake_plan = ReconcilePlan(
        workspace_id="ws-dry",
        create_folders=[planned_folder],
        move_items=[planned_move],
    )
    fake_report = ReconcileReport(plan=fake_plan)

    monkeypatch.setattr(apply_mod, "reconcile_folders_from_repo", lambda *a, **k: fake_report)

    workspace_id = "ws-dry"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

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
            )

    assert report.outcome == "dry_run"
    assert report.deploy_record_release_id == ""
    # Audit dir empty (no DeployRecord written).
    assert not (audit_dir / "deploys.jsonl").exists()
    # W1: report.folders_created/items_moved derived from report.plan.*
    assert report.folders_created == 1
    assert report.items_moved == 1

    # W5 / D-11 LOAD-BEARING: sidecar persisted even on --dry-run so a
    # subsequent real apply uses the same logical_ids.
    sidecar = source_dir / ".sigantry" / "notebook-ids.json"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "nb1.ipynb" in payload["ids"]


# ---------------------------------------------------------------------------
# Test 8 -- D-17 tempdir preservation on failure
# ---------------------------------------------------------------------------


def test_apply_preserves_tempdir_on_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-17: failure preserves the tempdir; path is printed to stderr."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    nb1 = source_dir / "nb1.ipynb"
    _write_minimal_ipynb(nb1)

    sync_yml = tmp_path / "sync.yml"
    _write_sync_yml(
        sync_yml,
        items_yaml=(
            f"  - local_path: '{nb1}'\n"
            "    type: Notebook\n"
            "    target_folder: '/raw'\n"
            "    display_name: 'Nb1'\n"
        ),
    )

    from sigantry_core.sync import apply as apply_mod

    def _boom(*_a, **_k):
        raise RuntimeError("synthetic boom for tempdir-preservation test")

    monkeypatch.setattr(apply_mod, "reconcile_folders_from_repo", _boom)

    workspace_id = "ws-tempdir-preserve"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(f"/v1/workspaces/{workspace_id}").mock(
            return_value=httpx.Response(200, json={"id": workspace_id, "gitConnection": None})
        )
        with _client_with_mock_token() as client, pytest.raises(ReconcilerWrapError):
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=workspace_id,
                client=client,
                audit_dir=audit_dir,
            )

    captured = capsys.readouterr()
    match = re.search(
        r"sync apply failed; staging dir preserved at (\S+)",
        captured.err,
    )
    assert match is not None, (
        f"stderr did not contain the preservation banner; got: {captured.err!r}"
    )
    preserved = Path(match.group(1))
    assert preserved.exists(), (
        f"tempdir {preserved} should still exist after failure; was cleaned up"
    )
    # Cleanup ourselves to avoid /tmp pollution.
    shutil.rmtree(preserved, ignore_errors=True)
