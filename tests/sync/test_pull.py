"""Tests for ``sigantry_core.sync.pull`` -- Plan 13-05 / SYNC-05.

Replaces the 5 Wave 0 xfail stubs with real assertions covering D-15
(no DeployRecord emission), D-20 (Notebook -> ipynb format unwrap),
D-21 (non-empty target refusal), and D-22 (logical_id preservation
for round-trip).

The HTTP transport is faked via ``respx``; ``snapshot_workspace`` runs
against the same respx surface (folders + items endpoints) so the test
exercises real production code in every layer except httpx itself.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import yaml

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import FABRIC_AUDIENCE
from sigantry_core.client import FabricRestClient
from sigantry_core.sync.errors import (
    PullDefinitionDecodeError,
    PullDefinitionPathTraversalError,
    PullTargetNotEmptyError,
)
from sigantry_core.sync.pull import SyncPullReport, pull_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_with_mock_token() -> FabricRestClient:
    """Build a FabricRestClient whose token provider is mocked.

    Mirrors the helper in ``tests/sync/test_apply.py`` -- the real
    BaseRestClient stays in the loop so respx can intercept httpx, but
    the OAuth chain is short-circuited so no live auth is attempted.
    """
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _minimal_ipynb_bytes() -> bytes:
    """Return a minimal valid ``.ipynb`` JSON document as UTF-8 bytes."""
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
    return json.dumps(payload).encode("utf-8")


def _b64(data: bytes) -> str:
    """Base64-encode bytes for the Fabric ``InlineBase64`` payload shape."""
    return base64.b64encode(data).decode("ascii")


def _wire_workspace_snapshot(
    router: respx.MockRouter,
    *,
    workspace_id: str,
    folder_id: str = "f-aims",
    folder_display_name: str = "AIMS",
    item_id: str = "nb-real-guid",
    item_display_name: str = "MyNb",
) -> None:
    """Wire the two paginated snapshot endpoints (folders + items).

    Returns one folder ``{folder_id, folder_display_name}`` at the
    workspace root and one Notebook item ``{item_id, item_display_name}``
    inside it. ``item.folder_id == folder_id`` -> ``target_folder``
    resolves to ``"/<folder_display_name>"``.
    """
    router.get(f"/v1/workspaces/{workspace_id}/folders").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": folder_id,
                        "displayName": folder_display_name,
                        "parentFolderId": None,
                        "workspaceId": workspace_id,
                    }
                ],
                "continuationToken": None,
            },
        )
    )
    router.get(f"/v1/workspaces/{workspace_id}/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": item_id,
                        "displayName": item_display_name,
                        "type": "Notebook",
                        "workspaceId": workspace_id,
                        "description": None,
                        "folderId": folder_id,
                    }
                ],
                "continuationToken": None,
            },
        )
    )


def _wire_notebook_definition(
    router: respx.MockRouter,
    *,
    workspace_id: str,
    item_id: str,
    ipynb_bytes: bytes,
) -> None:
    """Wire the Notebook getDefinition endpoint with one ipynb part.

    Mirrors the canonical Fabric REST shape per Microsoft Learn
    (``learn.microsoft.com/rest/api/fabric/articles/item-management/
    definitions/notebook-definition``):

        POST /v1/workspaces/{ws}/notebooks/{nbId}/getDefinition
            ?format=ipynb
        -> 200 {"definition": {"parts": [
            {"path": "notebook-content.ipynb",
             "payload": "<b64>",
             "payloadType": "InlineBase64"}
        ]}}
    """
    router.post(
        f"/v1/workspaces/{workspace_id}/notebooks/{item_id}/getDefinition",
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "definition": {
                    "parts": [
                        {
                            "path": "notebook-content.ipynb",
                            "payload": _b64(ipynb_bytes),
                            "payloadType": "InlineBase64",
                        }
                    ]
                }
            },
        )
    )


# ---------------------------------------------------------------------------
# Test 1 -- sync.yml emission mirrors workspace topology (D-20 / SYNC-05)
# ---------------------------------------------------------------------------


def test_pull_writes_sync_yml(tmp_path: Path) -> None:
    """sync pull writes a sync.yml mirroring workspace topology (SYNC-05 / D-20).

    The emitted sync.yml carries one item with ``type=Notebook``,
    ``target_folder="/AIMS"``, ``display_name="MyNb"``, and a
    ``logical_id`` matching the snapshot's item id (D-22 round-trip
    invariant).
    """
    workspace_id = "ws-pull-syncyml"
    out = tmp_path / "out"

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            item_display_name="MyNb",
        )
        _wire_notebook_definition(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            ipynb_bytes=_minimal_ipynb_bytes(),
        )
        with _client_with_mock_token() as client:
            report = pull_workspace(workspace_id, into=out, client=client)

    assert isinstance(report, SyncPullReport)
    assert report.items_pulled == 1
    assert report.sync_yml_path == out / "sync.yml"
    assert report.sync_yml_path.exists()

    payload = yaml.safe_load(report.sync_yml_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["type"] == "Notebook"
    assert item["target_folder"] == "/AIMS"
    assert item["display_name"] == "MyNb"
    assert item["logical_id"] == "nb-real-guid"
    # UAT-FOUND-3 regression: ``local_path`` for a Notebook MUST point at
    # the ``notebook-content.ipynb`` payload pull wrote into the source
    # dir -- NotebookPackager.pack requires source.suffix == ".ipynb"
    # (sigantry_core/sync/packagers/notebook.py:111). Emitting the bare
    # source dir here (the pre-fix behaviour) breaks the D-22 round-trip.
    assert item["local_path"] == "AIMS/MyNb/notebook-content.ipynb"
    on_disk = report.into / item["local_path"]
    assert on_disk.is_file(), f"emitted local_path must be a real file, got {on_disk}"


# ---------------------------------------------------------------------------
# Test 2 -- Notebook getDefinition unwraps to .ipynb on disk (D-20)
# ---------------------------------------------------------------------------


def test_pull_unwraps_notebook_definition_to_ipynb(tmp_path: Path) -> None:
    """Notebook items pulled with format=ipynb (D-20) -> raw .ipynb on disk.

    Asserts the on-disk bytes equal the LF-normalised original (the
    bytes we set up are already LF -- normalisation is idempotent --
    so the round-trip is byte-identical).
    """
    workspace_id = "ws-pull-ipynb"
    out = tmp_path / "out"
    ipynb_bytes = _minimal_ipynb_bytes()

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(
            router,
            workspace_id=workspace_id,
            item_display_name="MyNb",
        )
        _wire_notebook_definition(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            ipynb_bytes=ipynb_bytes,
        )
        with _client_with_mock_token() as client:
            pull_workspace(workspace_id, into=out, client=client)

    on_disk = out / "AIMS" / "MyNb" / "notebook-content.ipynb"
    assert on_disk.exists(), (
        f"expected raw notebook at {on_disk}; "
        f"directory contents: {list((out / 'AIMS' / 'MyNb').iterdir()) if (out / 'AIMS' / 'MyNb').exists() else 'missing'}"
    )
    assert on_disk.read_bytes() == ipynb_bytes


# ---------------------------------------------------------------------------
# Test 3 -- LOAD-BEARING: pull does NOT emit a DeployRecord (D-15)
# ---------------------------------------------------------------------------


def test_pull_does_not_emit_deploy_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-15: sync pull is read-only; NO DeployRecord written.

    LOAD-BEARING -- monkeypatches
    :func:`sigantry_core.governance.audit.emit_deploy_record` to raise
    on call. If a future change wires audit emission into pull, this
    test flips red.
    """

    def _explode(*_a, **_k):
        raise AssertionError("pull must not call emit_deploy_record (D-15 invariant)")

    # NOTE: ``sigantry_core.governance.__init__`` re-exports the
    # ``audit`` symbol from ``sigantry_core.governance.rbac``, so the
    # bare attribute ``sigantry_core.governance.audit`` resolves to a
    # function rather than the submodule. Reach into ``sys.modules``
    # to obtain the actual ``sigantry_core.governance.audit`` module
    # (which carries ``emit_deploy_record``).
    import importlib
    import sys

    importlib.import_module("sigantry_core.governance.audit")
    audit_mod = sys.modules["sigantry_core.governance.audit"]
    monkeypatch.setattr(audit_mod, "emit_deploy_record", _explode)
    # Defensively, if pull.py ever imports the symbol directly, the
    # following catches that too.
    import sigantry_core.sync.pull as pull_mod

    if hasattr(pull_mod, "emit_deploy_record"):
        monkeypatch.setattr(pull_mod, "emit_deploy_record", _explode)

    workspace_id = "ws-pull-no-audit"
    out = tmp_path / "out"

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(router, workspace_id=workspace_id)
        _wire_notebook_definition(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            ipynb_bytes=_minimal_ipynb_bytes(),
        )
        with _client_with_mock_token() as client:
            # Must NOT raise -- emit_deploy_record never fires.
            pull_workspace(workspace_id, into=out, client=client)


# ---------------------------------------------------------------------------
# Test 4 -- non-empty target refusal without --force (D-21)
# ---------------------------------------------------------------------------


def test_pull_refuses_non_empty_target_without_force(tmp_path: Path) -> None:
    """D-21: --into existing-non-empty-dir without --force -> typed error.

    Re-call with ``force=True`` succeeds (the dirty file may be
    overwritten -- operators acknowledged the clobber semantics).
    """
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "file.txt").write_text("preserve me", encoding="utf-8")

    workspace_id = "ws-pull-non-empty"

    # First call: refuse without --force.
    with _client_with_mock_token() as client:
        with pytest.raises(PullTargetNotEmptyError) as exc_info:
            pull_workspace(workspace_id, into=dirty, force=False, client=client)
        assert exc_info.value.target == str(dirty)

    # Second call: --force succeeds. Wire the snapshot endpoints so the
    # implementation can complete a clean pull on the now-acknowledged
    # dirty target.
    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(router, workspace_id=workspace_id)
        _wire_notebook_definition(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            ipynb_bytes=_minimal_ipynb_bytes(),
        )
        with _client_with_mock_token() as client:
            report = pull_workspace(workspace_id, into=dirty, force=True, client=client)
    assert report.items_pulled == 1
    # The pre-existing file is left in place unless the pull writes
    # over the same path (it does not -- we wrote to AIMS/MyNb/...).
    assert (dirty / "file.txt").exists()


# ---------------------------------------------------------------------------
# Test 5 -- emitted sync.yml preserves the workspace's logical_id (D-22)
# ---------------------------------------------------------------------------


def test_pull_preserves_logical_id_in_emitted_manifest(tmp_path: Path) -> None:
    """D-22: emitted sync.yml's logical_id matches the pulled item's snapshot id.

    Round-trip preservation invariant -- a follow-up ``sync apply``
    against the same workspace MUST be a no-op because every item's
    logical_id matches what already lives in the workspace.
    """
    workspace_id = "ws-pull-logical-id"
    out = tmp_path / "out"
    real_guid = "real-guid-from-fabric-12345"

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(
            router,
            workspace_id=workspace_id,
            item_id=real_guid,
            item_display_name="MyNb",
        )
        _wire_notebook_definition(
            router,
            workspace_id=workspace_id,
            item_id=real_guid,
            ipynb_bytes=_minimal_ipynb_bytes(),
        )
        with _client_with_mock_token() as client:
            report = pull_workspace(workspace_id, into=out, client=client)

    payload = yaml.safe_load(report.sync_yml_path.read_text(encoding="utf-8"))
    assert payload["items"][0]["logical_id"] == real_guid


# ---------------------------------------------------------------------------
# Test 6 -- CR-01: server-supplied part path with traversal -> typed error,
# nothing written outside source_dir
# ---------------------------------------------------------------------------


def test_pull_rejects_path_traversal_in_definition_part(tmp_path: Path) -> None:
    """CR-01: a Fabric response carrying ``"path": "../../etc/passwd"`` MUST
    raise :class:`PullDefinitionPathTraversalError` and write NO bytes
    outside the per-item source directory.

    LOAD-BEARING -- this is the regression gate for the path-traversal
    primitive surfaced in REVIEW.md (Phase 13). A future regression that
    drops the ``Path.resolve()``-based containment check flips this test
    red.
    """
    workspace_id = "ws-pull-traversal"
    out = tmp_path / "out"

    # Compose a Fabric response with one part whose ``path`` escapes
    # source_dir via ``../../<sentinel>``. The sentinel filename is
    # placed in tmp_path so we can assert it does NOT exist after the
    # rejection (i.e. no file was written outside source_dir).
    sentinel_name = "evil-traversal-sentinel.txt"
    malicious_payload = b"This file should never reach disk."

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(
            router,
            workspace_id=workspace_id,
            item_id="nb-evil",
            item_display_name="EvilNb",
        )
        router.post(
            f"/v1/workspaces/{workspace_id}/notebooks/nb-evil/getDefinition",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "definition": {
                        "parts": [
                            {
                                # Walk up out of <out>/AIMS/EvilNb back to
                                # tmp_path; the sentinel filename would land
                                # next to ``out`` if the containment check
                                # were missing.
                                "path": f"../../../{sentinel_name}",
                                "payload": _b64(malicious_payload),
                                "payloadType": "InlineBase64",
                            }
                        ]
                    }
                },
            )
        )
        with (
            _client_with_mock_token() as client,
            pytest.raises(PullDefinitionPathTraversalError) as exc_info,
        ):
            pull_workspace(workspace_id, into=out, client=client)

    # Error message carries the offending part_path so the operator can
    # find the bad item without re-running with verbose tracing.
    assert sentinel_name in str(exc_info.value)
    assert "EvilNb" in str(exc_info.value)

    # Sentinel file MUST NOT have been written anywhere on disk -- not
    # next to ``out``, not in tmp_path, not anywhere in or above the
    # tree. We sweep tmp_path recursively to be thorough.
    leaked = list(tmp_path.rglob(sentinel_name))
    assert leaked == [], f"path traversal succeeded; sentinel file leaked to: {leaked}"


# ---------------------------------------------------------------------------
# Test 7 -- WR-02: malformed base64 payload -> typed PullDefinitionDecodeError
# ---------------------------------------------------------------------------


def test_pull_raises_typed_error_on_malformed_base64(tmp_path: Path) -> None:
    """WR-02: a malformed ``payload`` raises :class:`PullDefinitionDecodeError`
    carrying the offending item's identity + part path.

    Replaces the previous "unhandled binascii.Error aborts mid-flight"
    failure mode with a typed error so callers (CLI / programmatic) can
    log a useful message and operators can pinpoint the bad workspace
    item without re-running with verbose tracing.
    """
    workspace_id = "ws-pull-bad-b64"
    out = tmp_path / "out"

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(
            router,
            workspace_id=workspace_id,
            item_id="nb-malformed",
            item_display_name="MalformedNb",
        )
        router.post(
            f"/v1/workspaces/{workspace_id}/notebooks/nb-malformed/getDefinition",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "definition": {
                        "parts": [
                            {
                                "path": "notebook-content.ipynb",
                                # Invalid base64: contains characters
                                # outside the alphabet and is NOT a valid
                                # length. ``base64.b64decode(..., validate=True)``
                                # raises ``binascii.Error`` for this.
                                "payload": "@@@not-valid-base64!!!",
                                "payloadType": "InlineBase64",
                            }
                        ]
                    }
                },
            )
        )
        with (
            _client_with_mock_token() as client,
            pytest.raises(PullDefinitionDecodeError) as exc_info,
        ):
            pull_workspace(workspace_id, into=out, client=client)

    msg = str(exc_info.value)
    assert "MalformedNb" in msg
    assert "nb-malformed" in msg
    assert "notebook-content.ipynb" in msg


# ---------------------------------------------------------------------------
# Test 8 -- WR-04: --into symlink is resolved before write; no punch-through
# ---------------------------------------------------------------------------


def test_pull_resolves_into_symlink_before_write(tmp_path: Path) -> None:
    """WR-04: ``--into`` is resolved through symlinks BEFORE every write.

    REVIEW.md WR-04: pre-fix a symlink ``--into`` would silently
    ``mkdir(parents=True, exist_ok=True)`` against the symlink and then
    every nested ``write_bytes`` punched through to the symlink target
    -- a force-multiplier for the CR-01 path-traversal vector. Post-fix
    ``_validate_target`` resolves the symlink and returns the real
    path; ``pull_workspace`` then uses the resolved path for every
    downstream filesystem operation.

    This test creates ``<tmp_path>/symlink`` -> ``<tmp_path>/real_dir``
    and asserts that after the pull the bytes land at the real
    directory under ``real_dir/AIMS/MyNb/...``, not via symlink
    indirection. The behavioural assertion is loose -- ``Path.resolve``
    canonicalises symlinks so the file ends up at the real path either
    way -- but the explicit test pins down the contract so a refactor
    that drops ``_validate_target``'s ``resolve()`` call still has a
    failing test to point at.
    """
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    symlink_dir = tmp_path / "symlink"
    symlink_dir.symlink_to(real_dir, target_is_directory=True)

    workspace_id = "ws-pull-symlink"

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        _wire_workspace_snapshot(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            item_display_name="MyNb",
        )
        _wire_notebook_definition(
            router,
            workspace_id=workspace_id,
            item_id="nb-real-guid",
            ipynb_bytes=_minimal_ipynb_bytes(),
        )
        with _client_with_mock_token() as client:
            report = pull_workspace(workspace_id, into=symlink_dir, client=client)

    # The resolved path lives under real_dir (the symlink target).
    assert real_dir in report.into.parents or report.into == real_dir
    # Bytes landed at the real directory -- visible through the symlink
    # OR directly through real_dir; both should resolve to the same file.
    via_real = real_dir / "AIMS" / "MyNb" / "notebook-content.ipynb"
    assert via_real.exists(), f"expected file via real_dir at {via_real}; not found"
    # The path returned by the report is the resolved (real) path, not
    # the symlink. ``Path.resolve()`` is idempotent so an extra resolve
    # call here is a no-op.
    assert report.into.resolve() == real_dir.resolve()


def test_pull_refuses_symlink_to_non_directory_without_force(
    tmp_path: Path,
) -> None:
    """WR-04: ``--into`` resolving to a file (via symlink) raises typed error.

    A symlink pointing to a regular file is a harder error than a
    non-empty dir; the resolve-first approach catches this and raises
    :class:`PullTargetNotEmptyError` with a message that surfaces both
    the symlink path and the resolved target.
    """
    real_file = tmp_path / "real_file.txt"
    real_file.write_text("not a directory", encoding="utf-8")
    symlink_to_file = tmp_path / "symlink-to-file"
    symlink_to_file.symlink_to(real_file)

    workspace_id = "ws-pull-symlink-file"
    with (
        _client_with_mock_token() as client,
        pytest.raises(PullTargetNotEmptyError) as exc_info,
    ):
        pull_workspace(workspace_id, into=symlink_to_file, force=False, client=client)
    # Error surfaces the resolved real-file path so the operator can
    # diagnose without re-running with verbose tracing.
    assert "real_file.txt" in str(exc_info.value)
    assert exc_info.value.target == str(symlink_to_file)
