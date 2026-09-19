"""Tests for sigantry_core.sync.snapshot — Plan 13-02 / INTROSPECT-01..03.

Replaces the Wave 0 xfail stubs with real assertions covering D-02 (frozen
pydantic v2 dataclass), D-09 / INTROSPECT-03 (no cross-run cache), the
INTROSPECT-01 pagination acceptance test, and the INTROSPECT-02 "folder-id
is the join key" invariant via both a structural test and a load-bearing
grep test against ``sigantry_core/sync/snapshot.py`` itself.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import httpx
import pydantic
import pytest
import respx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE
from sigantry_core.client import FabricRestClient
from sigantry_core.sync.snapshot import WorkspaceSnapshot, snapshot_workspace
from sigantry_core.workspace.folders import Folder
from sigantry_core.workspace.items import Item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_with_mock_token() -> FabricRestClient:
    """Build a FabricRestClient whose token provider is mocked.

    We use the real BaseRestClient (so respx can intercept httpx) but patch
    the token provider so no live auth is attempted.
    """
    from sigantry_core.auth import TokenProvider

    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _folders_url(workspace_id: str) -> str:
    return f"/v1/workspaces/{workspace_id}/folders"


def _items_url(workspace_id: str) -> str:
    return f"/v1/workspaces/{workspace_id}/items"


# ---------------------------------------------------------------------------
# Test 1 — frozen pydantic v2 dataclass (D-02)
# ---------------------------------------------------------------------------


def test_snapshot_returns_frozen_dataclass() -> None:
    """WorkspaceSnapshot is frozen pydantic v2; mutation raises (D-02)."""
    snap = WorkspaceSnapshot(
        workspace_id="ws-1",
        folders_by_id={},
        items_by_id={},
        folder_path_index={},
        item_to_folder={},
    )
    with pytest.raises(pydantic.ValidationError):
        snap.workspace_id = "mutated"  # type: ignore[misc]


def test_snapshot_carries_schema_version_in_serialised_output() -> None:
    """WorkspaceSnapshot emits ``schema_version`` for forward-compat parity.

    UAT-FOUND (Phase 13 Test 1, 2026-04-29 -- low-pri gap): the
    snapshot CLI's JSON output was missing the ``schema_version`` field
    that both the drift JSON (Test 6) and sync.yml manifest (D-05) carry.
    Adding it preserves the wire-format trio: snapshot / sync.yml /
    drift JSON all surface a SemVer string at the top level so consumers
    can branch on it.
    """
    from sigantry_core.sync.snapshot import SNAPSHOT_SCHEMA_VERSION

    snap = WorkspaceSnapshot(
        workspace_id="ws-schema-test",
        folders_by_id={},
        items_by_id={},
        folder_path_index={},
        item_to_folder={},
    )
    assert snap.schema_version == SNAPSHOT_SCHEMA_VERSION
    payload = snap.model_dump(mode="json")
    assert payload["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    # SemVer-shape sanity: starts with 1.0 today; bumped on breaking change.
    assert SNAPSHOT_SCHEMA_VERSION.count(".") == 2


# ---------------------------------------------------------------------------
# Test 2 — combines folders + items into the four index fields
# ---------------------------------------------------------------------------


def test_snapshot_combines_folders_and_items_into_indexes() -> None:
    """folder_path_index + item_to_folder built from list_folders + list_items (INTROSPECT-01)."""
    workspace_id = "ws-combined"
    folders_payload = {
        "value": [
            {"id": "fa", "displayName": "raw"},
            {"id": "fb", "displayName": "Bronze", "parentFolderId": "fa"},
        ]
    }
    items_payload = {
        "value": [
            {
                "id": "i1",
                "displayName": "MyNotebook",
                "type": "Notebook",
                "workspaceId": workspace_id,
                "folderId": "fb",
            },
            {
                "id": "i2",
                "displayName": "RootItem",
                "type": "Notebook",
                "workspaceId": workspace_id,
                # No folderId — lives at workspace root.
            },
        ]
    }

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(_folders_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=folders_payload)
        )
        router.get(_items_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=items_payload)
        )

        with _client_with_mock_token() as client:
            snap = snapshot_workspace(workspace_id, client=client)

    assert snap.workspace_id == workspace_id
    assert len(snap.folders_by_id) == 2
    assert len(snap.items_by_id) == 2
    assert len(snap.folder_path_index) == 2
    assert len(snap.item_to_folder) == 2

    # folder_path_index: every value is a folder_id (a known id from folders_by_id).
    for path, folder_id in snap.folder_path_index.items():
        assert folder_id in snap.folders_by_id
        assert path.startswith("/")

    # item_to_folder: routes mapped correctly; root items map to None.
    assert snap.item_to_folder["i1"] == "fb"
    assert snap.item_to_folder["i2"] is None

    # Folders + items round-trip the source DTO types.
    assert isinstance(snap.folders_by_id["fa"], Folder)
    assert isinstance(snap.items_by_id["i1"], Item)


# ---------------------------------------------------------------------------
# Test 3 — pagination over >100 folders (INTROSPECT-01 acceptance)
# ---------------------------------------------------------------------------


def test_snapshot_pagination_over_100_folders() -> None:
    """LOAD-BEARING (INTROSPECT-01 acceptance): synthetic >100-folder workspace; continuationToken exercised."""
    workspace_id = "ws-pagination"
    page_one = {
        "value": [{"id": f"f{n:03d}", "displayName": f"folder_{n:03d}"} for n in range(100)],
        "continuationToken": "next-page-token",
    }
    page_two = {
        "value": [{"id": f"f{n:03d}", "displayName": f"folder_{n:03d}"} for n in range(100, 150)],
        # Terminal page — no continuationToken.
    }
    items_payload = {
        "value": [
            {
                "id": f"i{n}",
                "displayName": f"item_{n}",
                "type": "Notebook",
                "workspaceId": workspace_id,
            }
            for n in range(5)
        ]
    }

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        folders_route = router.get(_folders_url(workspace_id)).mock(
            side_effect=[
                httpx.Response(200, json=page_one),
                httpx.Response(200, json=page_two),
            ]
        )
        router.get(_items_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=items_payload)
        )

        with _client_with_mock_token() as client:
            snap = snapshot_workspace(workspace_id, client=client)

    assert len(snap.folders_by_id) == 150, (
        "Pagination MUST produce all 150 folders across the two pages — "
        "if this fails, continuationToken is being dropped."
    )
    assert folders_route.call_count >= 2, (
        "INTROSPECT-01 pagination invariant: the folders endpoint must be hit "
        "at least twice when continuationToken is present on page 1."
    )
    # Items endpoint hit exactly once (single page).
    assert len(snap.items_by_id) == 5


# ---------------------------------------------------------------------------
# Test 4 — INTROSPECT-02: folder_id is the join key, never displayName
# ---------------------------------------------------------------------------


def test_snapshot_distinguishes_siblings_by_folder_id_not_displayname() -> None:
    """LOAD-BEARING (INTROSPECT-02): /raw/Bronze and /curated/Bronze resolve to different folder_ids."""
    workspace_id = "ws-siblings"
    folders_payload = {
        "value": [
            {"id": "fa", "displayName": "raw"},
            {"id": "fb", "displayName": "curated"},
            {"id": "fc", "displayName": "Bronze", "parentFolderId": "fa"},
            {"id": "fd", "displayName": "Bronze", "parentFolderId": "fb"},
        ]
    }
    items_payload: dict = {"value": []}

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        router.get(_folders_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=folders_payload)
        )
        router.get(_items_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=items_payload)
        )

        with _client_with_mock_token() as client:
            snap = snapshot_workspace(workspace_id, client=client)

    assert snap.folder_path_index == {
        "/raw": "fa",
        "/curated": "fb",
        "/raw/Bronze": "fc",
        "/curated/Bronze": "fd",
    }
    # The two siblings share the displayName "Bronze" but their folder_ids
    # are distinct — INTROSPECT-02 verified.
    assert snap.folder_path_index["/raw/Bronze"] != snap.folder_path_index["/curated/Bronze"]


# ---------------------------------------------------------------------------
# Test 5 — grep test: zero `displayName ==` literal compares (INTROSPECT-02)
# ---------------------------------------------------------------------------


def test_snapshot_no_displayname_equality_in_routing() -> None:
    """LOAD-BEARING grep-test (INTROSPECT-02): sigantry_core/sync/snapshot.py contains zero display-name equality compares.

    Folder routing MUST join on folder_id, never on display_name. A literal
    ``displayName ==`` (or its snake_case sibling, or the negated forms) in
    snapshot.py would be a routing-correctness regression — Fabric allows
    sibling folders to share a display_name at different parent paths.
    """
    import sigantry_core.sync.snapshot as snapshot_mod

    text = pathlib.Path(snapshot_mod.__file__).read_text("utf-8")
    forbidden = (
        "displayName ==",
        "display_name ==",
        "displayName !=",
        "display_name !=",
    )
    for needle in forbidden:
        assert needle not in text, (
            f"INTROSPECT-02 violation: forbidden literal {needle!r} found in "
            f"{snapshot_mod.__file__}. Folder routing must join on folder_id, "
            "never on display_name."
        )


# ---------------------------------------------------------------------------
# Test 6 — INTROSPECT-03 / D-09: no cross-run caching
# ---------------------------------------------------------------------------


def test_snapshot_no_cross_run_caching() -> None:
    """LOAD-BEARING (INTROSPECT-03 / D-09): two consecutive snapshot_workspace calls each issue paginated REST calls."""
    workspace_id = "ws-no-cache"
    folders_payload = {"value": [{"id": "fa", "displayName": "raw"}]}
    items_payload: dict = {"value": []}

    with respx.mock(base_url=FABRIC_AUDIENCE) as router:
        folders_route = router.get(_folders_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=folders_payload)
        )
        items_route = router.get(_items_url(workspace_id)).mock(
            return_value=httpx.Response(200, json=items_payload)
        )

        with _client_with_mock_token() as client:
            snap1 = snapshot_workspace(workspace_id, client=client)
            snap2 = snapshot_workspace(workspace_id, client=client)

    # Both calls return equivalent state (the underlying mocks are stable).
    assert snap1.folder_path_index == snap2.folder_path_index
    # But each invocation MUST hit the REST surface afresh — D-09 invariant.
    assert folders_route.call_count >= 2, (
        "INTROSPECT-03 / D-09 violation: folders endpoint should be hit on every "
        f"snapshot_workspace call, got {folders_route.call_count} calls for 2 invocations."
    )
    assert items_route.call_count >= 2, (
        "INTROSPECT-03 / D-09 violation: items endpoint should be hit on every "
        f"snapshot_workspace call, got {items_route.call_count} calls for 2 invocations."
    )
