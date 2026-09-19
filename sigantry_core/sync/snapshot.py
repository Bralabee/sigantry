"""WorkspaceSnapshot — INTROSPECT-01..03 foundation (Phase 13 / D-02).

Two paginated REST calls (Council D verified) provide a complete snapshot
of a Fabric workspace's folder + item topology:

* ``GET /v1/workspaces/{id}/folders``  — list_folders pagination
* ``GET /v1/workspaces/{id}/items``    — list_items pagination

The combined snapshot is the source of truth for the three Phase 13
consumers — ``sync apply``, ``sync pull``, and ``sigantry diff``. Each
entry point calls :func:`snapshot_workspace` afresh on every CLI
invocation; there is no module-level / cross-run cache (D-09 /
INTROSPECT-03).

INTROSPECT-02 invariant
-----------------------

Folder routing joins on ``folder_id`` (the Fabric GUID), NEVER on
``display_name``. Sibling folders with the same display_name at
different parent paths — e.g. ``/raw/Bronze`` and ``/curated/Bronze`` —
resolve to two distinct folder_ids in
:attr:`WorkspaceSnapshot.folder_path_index`. The companion grep test
``tests/sync/test_snapshot.py::test_snapshot_no_displayname_equality_in_routing``
fails CI if any literal display-name equality compare leaks into this
module.

D-31: every REST call routes through
:class:`sigantry_core.client.FabricRestClient` using
``TokenProvider.from_defaults()``. There is no direct ``httpx`` usage
in this module.
"""

from __future__ import annotations

from functools import cached_property

from pydantic import BaseModel, ConfigDict

from sigantry_core.client import FabricRestClient
from sigantry_core.workspace.folders import Folder, list_folders
from sigantry_core.workspace.items import Item, list_items

#: SemVer-pinned wire contract for the snapshot JSON output. Bumped on
#: any breaking change to the WorkspaceSnapshot field set; consumers
#: that read pre-serialised snapshot JSON can branch on this value.
#: Mirrors the convention used by the drift JSON (Phase 13 Test 6) and
#: the sync.yml manifest (D-05) so all three Phase 13 wire formats
#: surface a versioned schema.
SNAPSHOT_SCHEMA_VERSION = "1.0.0"


class WorkspaceSnapshot(BaseModel):
    """Frozen workspace topology snapshot (D-02).

    Fields (all read-only after construction):

    * ``schema_version``: SemVer string for the snapshot wire contract.
      Defaults to :data:`SNAPSHOT_SCHEMA_VERSION`. Forward-compat with
      the drift JSON + sync.yml conventions (Phase 13 UAT-found low-pri
      gap surfaced 2026-04-29).
    * ``workspace_id``: the workspace GUID this snapshot describes.
    * ``folders_by_id``: ``{folder_id: Folder}`` — raw lookup over the
      paginated folders response.
    * ``items_by_id``: ``{item_id: Item}`` — raw lookup over the paginated
      items response.
    * ``folder_path_index``: ``{"/raw/Bronze": "<folder-guid>", ...}`` —
      forward-slash, leading-``/`` path strings whose values are
      ``folder_id`` GUIDs. The path string carries display-name text but
      the value is always an id (INTROSPECT-02).
    * ``item_to_folder``: ``{item_id: folder_id | None}`` — routing map;
      ``None`` means the item lives at the workspace root.

    ``Folder`` and ``Item`` are stdlib ``@dataclass(frozen=True,
    slots=True)`` rather than pydantic models, so the snapshot uses
    ``arbitrary_types_allowed=True`` to accept them as opaque values
    inside the otherwise strict (``extra="forbid"``) pydantic schema.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    workspace_id: str
    folders_by_id: dict[str, Folder]
    items_by_id: dict[str, Item]
    folder_path_index: dict[str, str]
    item_to_folder: dict[str, str | None]

    @cached_property
    def id_to_path(self) -> dict[str, str]:
        """Reverse map of :attr:`folder_path_index`: ``{folder_id: path}``.

        Computed once per snapshot instance via :func:`functools.cached_property`
        so callers performing reverse lookups (``sync/pull.py`` per-item
        folder routing; ``sync/diff.py`` synth-alias backfill) get O(1)
        membership tests instead of the O(N) scan that
        ``_resolve_folder_path_from_id`` previously paid per call. With M
        folders and N items, the per-pull cost drops from O(N*M) to
        O(N + M).

        Audit-2026-05-07 W4.1.
        """
        return {fid: path for path, fid in self.folder_path_index.items()}


def _index_folders_by_path(folders: list[Folder]) -> dict[str, str]:
    """Build a ``{path: folder_id}`` map from a list of Folders.

    Path format: forward-slash separated, leading ``/``, segments are
    ``display_name`` strings. Mirrors the algorithm in
    :func:`sigantry_core.workspace.reconciler._index_folders_by_path` but
    emits string paths (rather than tuple paths) so manifest comparisons
    in Plans 13-04 / 13-05 / 13-06 can use the same ``str`` keys.

    INTROSPECT-02: collisions in ``display_name`` at different parent
    paths produce two distinct entries — different paths map to
    different folder_ids — because the path is constructed by walking
    each folder's ``parent_folder_id`` chain to the root.

    Cycle-safe via :func:`sigantry_core.workspace.folders.index_folder_paths_to_id`
    (audit-2026-05-07 W1.6).
    """
    from sigantry_core.workspace.folders import index_folder_paths_to_id

    return index_folder_paths_to_id(folders)


def snapshot_workspace(
    workspace_id: str,
    *,
    client: FabricRestClient | None = None,
) -> WorkspaceSnapshot:
    """Build a fresh :class:`WorkspaceSnapshot` via two paginated REST calls.

    Parameters
    ----------
    workspace_id:
        Fabric workspace GUID.
    client:
        Optional :class:`FabricRestClient`. Tests pass an explicit
        respx-backed client so the underlying httpx transport can be
        intercepted. When ``None`` the function constructs one via
        :meth:`FabricRestClient.from_defaults` and uses it as a context
        manager so the connection pool closes on exit.

    Returns
    -------
    WorkspaceSnapshot
        Frozen; safe to pass-by-value to multiple consumers within a
        single CLI invocation.

    Notes
    -----
    No module-level cache. Two consecutive calls each issue two
    paginated REST traversals (D-09 / INTROSPECT-03). Pagination is
    delegated to
    :meth:`sigantry_core.client.BaseRestClient.list_paginated` (Phase 3)
    which already exercises Fabric's ``continuationToken`` semantics.
    """
    if client is None:
        with FabricRestClient.from_defaults() as owned_client:
            return _snapshot_with_client(workspace_id, owned_client)
    return _snapshot_with_client(workspace_id, client)


def _snapshot_with_client(workspace_id: str, client: FabricRestClient) -> WorkspaceSnapshot:
    """Build the snapshot with an already-constructed REST client.

    Two paginated traversals — folders first, then items — keeps the
    Council D evidence (2 REST endpoints) tight and lets respx-driven
    tests assert per-endpoint call counts independently.
    """
    folders: list[Folder] = list(list_folders(client, workspace_id))
    items: list[Item] = list(list_items(client, workspace_id))

    folders_by_id: dict[str, Folder] = {f.id: f for f in folders}
    items_by_id: dict[str, Item] = {it.id: it for it in items}
    folder_path_index = _index_folders_by_path(folders)
    item_to_folder: dict[str, str | None] = {it.id: it.folder_id for it in items}

    return WorkspaceSnapshot(
        workspace_id=workspace_id,
        folders_by_id=folders_by_id,
        items_by_id=items_by_id,
        folder_path_index=folder_path_index,
        item_to_folder=item_to_folder,
    )


__all__ = ("SNAPSHOT_SCHEMA_VERSION", "WorkspaceSnapshot", "snapshot_workspace")
