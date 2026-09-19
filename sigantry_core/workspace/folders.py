"""Workspace folder CRUD + item-to-folder move.

Fabric workspaces expose hierarchical folders via:

* ``GET /v1/workspaces/{id}/folders`` — paginated list, each entry carries
  ``id``, ``displayName``, and ``parentFolderId`` (absent at the top level).
* ``POST /v1/workspaces/{id}/folders`` — 201 sync. Body is
  ``{"displayName": ..., "parentFolderId": <optional>}``.
* ``DELETE /v1/workspaces/{id}/folders/{folderId}`` — 200 sync. The workspace
  must have no items or sub-folders under the target; callers are expected
  to teardown leaf-first.
* ``POST /v1/workspaces/{id}/items/{itemId}/move`` — 200 sync. Body is
  ``{"targetFolderId": <folder-id-or-None>}``. ``None`` moves the item back
  to the workspace root. This is the ONLY supported way to change an item's
  folder — ``PATCH /items/{id}`` with ``folderId`` is rejected with
  ``InvalidParameter: UpdateArtifactRequest should have at least one valid
  field to update``.

All endpoints above have been live-probed against a Fabric test tenant.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.governance.audit import destructive_op


@dataclass(frozen=True, slots=True)
class Folder:
    """Frozen DTO for a workspace folder."""

    id: str
    display_name: str
    parent_folder_id: str | None
    workspace_id: str

    @classmethod
    def from_api(cls, payload: dict, workspace_id: str) -> Folder:
        return cls(
            id=payload["id"],
            display_name=payload["displayName"],
            parent_folder_id=payload.get("parentFolderId"),
            workspace_id=workspace_id,
        )


def list_folders(client: FabricRestClient, workspace_id: str) -> Iterator[Folder]:
    """GET /v1/workspaces/{id}/folders — paginated."""
    for payload in client.list_paginated(f"/v1/workspaces/{workspace_id}/folders"):
        yield Folder.from_api(payload, workspace_id)


def create_folder(
    client: FabricRestClient,
    workspace_id: str,
    *,
    display_name: str,
    parent_folder_id: str | None = None,
) -> Folder:
    """POST /v1/workspaces/{id}/folders — 201 sync.

    ``parent_folder_id=None`` creates a top-level folder.
    """
    body: dict = {"displayName": display_name}
    if parent_folder_id is not None:
        body["parentFolderId"] = parent_folder_id
    resp = client.send("POST", f"/v1/workspaces/{workspace_id}/folders", json=body)
    payload = resp.json_body if isinstance(resp.json_body, dict) else {}
    return Folder.from_api(payload, workspace_id)


@destructive_op("folder", "delete", resource_arg="folder_id")
def delete_folder(
    client: FabricRestClient,
    workspace_id: str,
    folder_id: str,
    *,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
    token_provider: TokenProvider | None = None,
    resource_id: str | None = None,
) -> None:
    """DELETE /v1/workspaces/{id}/folders/{folderId} — 200 sync.

    The workspace folder must be empty (no items, no sub-folders) — Fabric
    rejects the delete otherwise. Leaf-first teardown is the caller's job.

    Decorator-enforced: ``force=True`` MUST be passed at call site.
    """
    client.send("DELETE", f"/v1/workspaces/{workspace_id}/folders/{folder_id}")


def move_item(
    client: FabricRestClient,
    workspace_id: str,
    item_id: str,
    *,
    target_folder_id: str | None,
) -> None:
    """POST /v1/workspaces/{id}/items/{itemId}/move — 200 sync.

    ``target_folder_id=None`` moves the item to the workspace root. This is
    the only supported path for changing an item's folder; see module
    docstring for the probe evidence ruling out ``PATCH /items/{id}``.
    """
    body: dict = (
        {"targetFolderId": target_folder_id} if target_folder_id else {"targetFolderId": None}
    )
    client.send("POST", f"/v1/workspaces/{workspace_id}/items/{item_id}/move", json=body)


# ---------------------------------------------------------------------------
# Shared parent-chain walk
# ---------------------------------------------------------------------------
#
# Audit-2026-05-07 W1.6: prior to remediation three call sites
# (``workspace.reconciler._index_folders_by_path``,
# ``sync.snapshot._index_folders_by_path``, and
# ``sync.diff._folder_path_for_id``) each independently walked the
# ``parent_folder_id`` chain to the workspace root. Two of the three
# (``reconciler`` + ``snapshot``) had no cycle protection — a malicious
# or buggy Fabric response that returned a folder whose
# ``parent_folder_id`` pointed back into the chain (or to itself) would
# infinite-loop the reconciler. The third (``diff``) carried a
# ``visited`` set; this helper is the lifted version of that pattern,
# now shared across all three sites.
#
# The helper returns the segment list root-first; callers format to
# tuple (workspace.reconciler) or forward-slash string (sync.snapshot,
# sync.diff). Cycle short-circuit returns the partial chain reached so
# far so callers see a degraded-but-bounded path rather than an infinite
# loop.


def walk_parent_chain(
    folder_id: str | None,
    by_id: Mapping[str, Folder],
) -> tuple[str, ...]:
    """Walk a folder's parent chain to the workspace root, cycle-safe.

    Args:
        folder_id: starting folder id. ``None`` (item lives at workspace
            root) returns the empty tuple.
        by_id: index of ``folder_id -> Folder``. A starting id not
            present in the index returns the empty tuple — caller must
            decide whether to surface the gap or treat as root.

    Returns:
        ``tuple`` of ``display_name`` segments ordered root-first
        (``("raw", "UI-Created", "leaf")``). Cycle-safe: if a parent
        chain refers back into the visited set (or self-references) the
        walk terminates and returns the partial path reached so far.
    """
    if folder_id is None:
        return ()
    starting = by_id.get(folder_id)
    if starting is None:
        return ()
    segments: list[str] = []
    node: Folder | None = starting
    visited: set[str] = set()
    while node is not None and node.id not in visited:
        visited.add(node.id)
        segments.append(node.display_name)
        parent_id = node.parent_folder_id
        node = by_id.get(parent_id) if parent_id else None
    return tuple(reversed(segments))


def folder_path_string(
    folder_id: str | None,
    by_id: Mapping[str, Folder],
) -> str:
    """Forward-slash path string for a folder id.

    Convenience wrapper over :func:`walk_parent_chain` that emits
    ``"/raw/UI-Created/leaf"`` from a starting id, or ``"/"`` for
    ``folder_id=None`` / not-found.
    """
    segments = walk_parent_chain(folder_id, by_id)
    return "/" + "/".join(segments) if segments else "/"


def index_folders_by_path(
    folders: Iterable[Folder],
) -> dict[tuple[str, ...], Folder]:
    """Build a ``{path_segments: Folder}`` map.

    Cycle-safe via :func:`walk_parent_chain`. Callers wanting forward-
    slash string keys (e.g. snapshot indexes) should use
    :func:`index_folder_paths_to_id` instead.
    """
    by_id: dict[str, Folder] = {f.id: f for f in folders}
    out: dict[tuple[str, ...], Folder] = {}
    for f in by_id.values():
        out[walk_parent_chain(f.id, by_id)] = f
    return out


def index_folder_paths_to_id(
    folders: Iterable[Folder],
) -> dict[str, str]:
    """Build a ``{forward-slash path: folder_id}`` map.

    Mirrors :func:`index_folders_by_path` but emits string keys for
    manifest comparison. Cycle-safe.
    """
    by_id: dict[str, Folder] = {f.id: f for f in folders}
    out: dict[str, str] = {}
    for f in by_id.values():
        path = folder_path_string(f.id, by_id)
        out[path] = f.id
    return out
