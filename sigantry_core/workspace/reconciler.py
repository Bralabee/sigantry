"""Reconcile a Fabric workspace's folder hierarchy to match a repo tree.

When a Fabric workspace is synced from git via Microsoft's native workspace
Git integration, the folder hierarchy is not always reconstructed faithfully
— items frequently land at the workspace root regardless of subdirectory
placement in the repo. This module walks the repo tree and drives the
workspace's folder structure + item placement to match.

Design
------

The reconciler is **plan-then-apply**: it always produces a
``ReconcilePlan`` describing the create-folder, move-item, and (optionally)
orphan-cleanup operations that would reconcile state; the ``apply=True``
flag executes the plan.

The reconciler is **additive by default**: it creates missing folders and
moves items into their expected folders. Orphan cleanup is **opt-in**
(``include_orphans=True`` at plan time; ``unpublish_orphans=True`` +
``force=True`` at apply time) and destructive — it deletes workspace items
whose (displayName, type) is not in the repo tree, and folders that end up
empty as a result.

Folder-path resolution
----------------------

For a ``.platform`` file at ``<repo>/bronze/raw/MyNotebook.Notebook/.platform``
where ``<repo>`` is the ``repository_directory`` argument, the expected
folder path is ``["bronze", "raw"]``. An item whose parent directory is
``<repo>`` itself has an empty folder path (lives at the workspace root).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient
from sigantry_core.governance.audit import DestructiveOpError
from sigantry_core.workspace.folders import (
    Folder,
    create_folder,
    delete_folder,
    list_folders,
    move_item,
)
from sigantry_core.workspace.items import Item, delete_item, list_items


@dataclass(frozen=True, slots=True)
class RepoItem:
    """A Fabric item descriptor found in a repo tree."""

    display_name: str
    item_type: str
    folder_path: tuple[str, ...]  # path segments; empty means workspace root
    platform_path: Path  # absolute path to the .platform file


@dataclass(frozen=True, slots=True)
class PlannedFolderCreate:
    path: tuple[str, ...]  # full path from root; last element is the folder being created


@dataclass(frozen=True, slots=True)
class PlannedItemMove:
    item_id: str
    display_name: str
    item_type: str
    from_folder_id: str | None
    to_folder_path: tuple[str, ...]  # empty tuple means root


@dataclass(frozen=True, slots=True)
class PlannedItemUnpublish:
    """A workspace item that exists but has no match in the repo tree."""

    item_id: str
    display_name: str
    item_type: str
    current_folder_id: str | None


@dataclass(frozen=True, slots=True)
class PlannedFolderDelete:
    """A workspace folder that has no corresponding path in the repo tree."""

    folder_id: str
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReconcilePlan:
    workspace_id: str
    create_folders: list[PlannedFolderCreate] = field(default_factory=list)
    move_items: list[PlannedItemMove] = field(default_factory=list)
    unresolved_items: list[RepoItem] = field(default_factory=list)
    """Repo items whose displayName+type wasn't found in the workspace.

    These are typically items that haven't been synced yet; surfaced so the
    caller can decide whether to deploy them separately.
    """
    unpublish_items: list[PlannedItemUnpublish] = field(default_factory=list)
    """Workspace items with no matching (displayName, type) in the repo tree.

    Populated only when :func:`plan_reconcile` is called with
    ``include_orphans=True``. Applying this list requires explicit
    ``unpublish_orphans=True`` + ``force=True`` at apply time.
    """
    delete_folders: list[PlannedFolderDelete] = field(default_factory=list)
    """Workspace folders with no corresponding path in the repo tree.

    Populated only when :func:`plan_reconcile` is called with
    ``include_orphans=True``. Ordered leaf-first (longest path first) so
    apply can delete children before parents, matching Fabric's requirement
    that a folder be empty before deletion.
    """


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    plan: ReconcilePlan
    folders_created: list[Folder] = field(default_factory=list)
    items_moved: list[PlannedItemMove] = field(default_factory=list)
    items_unpublished: list[PlannedItemUnpublish] = field(default_factory=list)
    folders_deleted: list[PlannedFolderDelete] = field(default_factory=list)


def _scan_repo(repo_dir: Path) -> list[RepoItem]:
    """Walk repo_dir, yield one RepoItem per .platform file found.

    Relies on fabric-cicd's directory-naming convention: each item is a
    subdirectory named ``<displayName>.<itemType>``. The folder path is the
    sequence of directory names BETWEEN ``repo_dir`` and the parent of that
    subdirectory.
    """
    items: list[RepoItem] = []
    for platform_file in repo_dir.rglob(".platform"):
        parent = platform_file.parent  # e.g. .../bronze/raw/MyNotebook.Notebook
        name_with_type = parent.name
        if "." not in name_with_type:
            # malformed directory; skip
            continue
        display_name, _, item_type = name_with_type.rpartition(".")
        if not display_name or not item_type:
            continue
        # Read the .platform to cross-check — the directory name is authoritative
        # but we confirm the declared type matches.
        try:
            meta = json.loads(platform_file.read_text(encoding="utf-8")).get("metadata", {})
            declared_type = meta.get("type")
            declared_name = meta.get("displayName")
            if declared_type and declared_type != item_type:
                item_type = declared_type
            if declared_name:
                display_name = declared_name
        except (json.JSONDecodeError, OSError):
            pass
        rel = parent.parent.relative_to(repo_dir)
        path = tuple(p for p in rel.parts if p != ".")
        items.append(
            RepoItem(
                display_name=display_name,
                item_type=item_type,
                folder_path=path,
                platform_path=platform_file,
            )
        )
    return items


def _index_folders_by_path(folders: Iterable[Folder]) -> dict[tuple[str, ...], Folder]:
    """Build a path→Folder map from a list of folders with parent links.

    Cycle-safe via :func:`sigantry_core.workspace.folders.index_folders_by_path`
    (audit-2026-05-07 W1.6).
    """
    from sigantry_core.workspace.folders import index_folders_by_path

    return index_folders_by_path(folders)


def _normalise_preserve_set(preserve_paths: Iterable[str] | None) -> frozenset[tuple[str, ...]]:
    """Expand operator-supplied ``folders[]`` paths into a tuple-key set.

    Each entry is normalised to a tuple of segments (leading ``/`` optional).
    Every ancestor of a preserved path is added too, so a preserved
    ``/raw/UI-Created`` implicitly preserves ``/raw`` -- otherwise leaf-first
    deletion of the parent would still drop the protected child.
    """
    if not preserve_paths:
        return frozenset()
    expanded: set[tuple[str, ...]] = set()
    for raw in preserve_paths:
        if not raw:
            continue
        normalised = raw.lstrip("/").rstrip("/")
        if not normalised:
            continue
        segments = tuple(normalised.split("/"))
        for i in range(1, len(segments) + 1):
            expanded.add(segments[:i])
    return frozenset(expanded)


def plan_reconcile(
    client: FabricRestClient,
    workspace_id: str,
    *,
    repository_directory: str | Path,
    include_orphans: bool = False,
    preserve_paths: Iterable[str] | None = None,
) -> ReconcilePlan:
    """Compute the plan without executing anything.

    ``include_orphans=True`` additionally populates ``unpublish_items``
    (workspace items whose ``(displayName, type)`` is absent from the repo
    tree) and ``delete_folders`` (workspace folders whose path is absent
    from the repo tree). Applying these still requires the destructive
    gates at apply time — the plan itself has no side effects.

    ``preserve_paths`` is the operator-supplied folder-preservation set
    (the manifest's ``folders[]`` list). Any folder path in this set, OR
    any ancestor of such a path, is excluded from ``delete_folders`` even
    when it is otherwise an orphan. The exclusion is exact-match on the
    path tuple plus its computed ancestors (so declaring ``/raw/UI-Created``
    also keeps ``/raw`` alive). Has no effect when ``include_orphans=False``.
    """
    repo_dir = Path(repository_directory).resolve()
    repo_items = _scan_repo(repo_dir)

    existing_folders = list(list_folders(client, workspace_id))
    folders_by_path = _index_folders_by_path(existing_folders)
    existing_paths = set(folders_by_path.keys())

    # Every unique folder path + ancestors must exist.
    required_paths: set[tuple[str, ...]] = set()
    for ri in repo_items:
        for i in range(1, len(ri.folder_path) + 1):
            required_paths.add(ri.folder_path[:i])

    create_folders: list[PlannedFolderCreate] = []
    missing = sorted(required_paths - existing_paths, key=lambda p: (len(p), p))
    for path in missing:
        create_folders.append(PlannedFolderCreate(path=path))

    existing_items = list(list_items(client, workspace_id))
    existing_items_by_key: dict[tuple[str, str], Item] = {
        (it.display_name, it.type): it for it in existing_items
    }
    # path-for-id lookup: for each existing folder id, what's its full path?
    path_for_id: dict[str, tuple[str, ...]] = {f.id: p for p, f in folders_by_path.items()}

    move_items_list: list[PlannedItemMove] = []
    unresolved: list[RepoItem] = []
    repo_keys: set[tuple[str, str]] = set()
    for ri in repo_items:
        key = (ri.display_name, ri.item_type)
        repo_keys.add(key)
        current = existing_items_by_key.get(key)
        if current is None:
            unresolved.append(ri)
            continue
        current_folder_id = getattr(current, "folder_id", None)
        current_path = path_for_id.get(current_folder_id, ()) if current_folder_id else ()
        # Compare by PATH, not id — target folder may not yet exist (plan creates it
        # in this same run). At apply time we resolve the path to an id.
        if current_path != ri.folder_path:
            move_items_list.append(
                PlannedItemMove(
                    item_id=current.id,
                    display_name=current.display_name,
                    item_type=current.type,
                    from_folder_id=current_folder_id,
                    to_folder_path=ri.folder_path,
                )
            )

    unpublish_items: list[PlannedItemUnpublish] = []
    delete_folders: list[PlannedFolderDelete] = []
    if include_orphans:
        # Orphan items: in workspace but not in repo.
        for it in existing_items:
            if (it.display_name, it.type) not in repo_keys:
                unpublish_items.append(
                    PlannedItemUnpublish(
                        item_id=it.id,
                        display_name=it.display_name,
                        item_type=it.type,
                        current_folder_id=it.folder_id,
                    )
                )
        # Orphan folders: existing paths that are neither required by the repo
        # nor an ancestor of a required path. Ordered leaf-first (longest path
        # first) so apply can delete children before parents.
        needed_paths: set[tuple[str, ...]] = set()
        for req in required_paths:
            for i in range(1, len(req) + 1):
                needed_paths.add(req[:i])
        preserved = _normalise_preserve_set(preserve_paths)
        orphan_paths = sorted(
            (p for p in existing_paths if p and p not in needed_paths and p not in preserved),
            key=lambda p: (-len(p), p),
        )
        for path in orphan_paths:
            delete_folders.append(
                PlannedFolderDelete(folder_id=folders_by_path[path].id, path=path)
            )

    return ReconcilePlan(
        workspace_id=workspace_id,
        create_folders=create_folders,
        move_items=move_items_list,
        unresolved_items=unresolved,
        unpublish_items=unpublish_items,
        delete_folders=delete_folders,
    )


def apply_reconcile(
    client: FabricRestClient,
    plan: ReconcilePlan,
    *,
    unpublish_orphans: bool = False,
    force: bool = False,
    runbook_id: str | None = None,
    token_provider: TokenProvider | None = None,
) -> ReconcileReport:
    """Execute a plan. Creates folders leaf-first-safe (breadth-first), then moves items.

    Folder creation is ordered by path length (shortest first) so parents
    always exist before children. Items are moved after every required
    folder is guaranteed present.

    Orphan cleanup (opt-in) runs LAST so items being moved OUT of a
    now-orphan folder are already gone before the folder delete fires. Both
    destructive steps require ``unpublish_orphans=True`` and ``force=True``;
    either being absent when ``plan.unpublish_items`` or ``plan.delete_folders``
    is non-empty raises :class:`DestructiveOpError`.
    """
    created_by_path: dict[tuple[str, ...], Folder] = {}
    # Re-list to catch any folder that appeared since plan time (idempotent re-runs).
    existing = list(list_folders(client, plan.workspace_id))
    existing_by_path = _index_folders_by_path(existing)
    for planned in sorted(plan.create_folders, key=lambda p: len(p.path)):
        if planned.path in existing_by_path:
            created_by_path[planned.path] = existing_by_path[planned.path]
            continue
        parent_path = planned.path[:-1]
        parent_folder = existing_by_path.get(parent_path) or created_by_path.get(parent_path)
        parent_id = parent_folder.id if parent_folder else None
        folder = create_folder(
            client,
            plan.workspace_id,
            display_name=planned.path[-1],
            parent_folder_id=parent_id,
        )
        created_by_path[planned.path] = folder
        existing_by_path[planned.path] = folder

    moved: list[PlannedItemMove] = []
    for move in plan.move_items:
        target_folder = existing_by_path.get(move.to_folder_path) if move.to_folder_path else None
        # A target_id of None means "workspace root" -- the CORRECT target only
        # when to_folder_path is empty. When to_folder_path is non-empty but the
        # folder is absent from the re-list (deleted/raced between plan and apply
        # by another actor), degrading to None would silently relocate the item
        # to the ROOT and still report the move as succeeded to the intended
        # path. Distinguish the two None meanings: refuse rather than mis-place.
        if move.to_folder_path and target_folder is None:
            raise ValueError(
                f"reconcile: cannot move item {move.item_id!r} to "
                f"{'/'.join(move.to_folder_path)!r} -- target folder no longer "
                f"exists (deleted or raced since plan time). Refusing to move it "
                f"to the workspace root. Re-plan against the current workspace."
            )
        target_id = target_folder.id if target_folder else None
        move_item(client, plan.workspace_id, move.item_id, target_folder_id=target_id)
        moved.append(move)

    unpublished: list[PlannedItemUnpublish] = []
    deleted: list[PlannedFolderDelete] = []
    has_orphans = bool(plan.unpublish_items or plan.delete_folders)
    if has_orphans:
        if not unpublish_orphans:
            # Plan carries orphans but caller didn't opt in — leave them
            # alone. This is the additive-only default.
            pass
        elif not force:
            # Loud failure when caller asks for orphan cleanup without force —
            # mirrors the @destructive_op contract used elsewhere.
            raise DestructiveOpError(
                "reconcile.orphan_cleanup requires force=True "
                "(explicit keyword) when unpublish_orphans=True. "
                "No audit record emitted."
            )
        else:
            # Items first — a folder delete will fail while it still has
            # items. Each call flows through the @destructive_op gate on
            # delete_item, which emits the per-resource audit record.
            for orphan_item in plan.unpublish_items:
                delete_item(
                    client,
                    plan.workspace_id,
                    orphan_item.item_id,
                    force=True,
                    runbook_id=runbook_id,
                    token_provider=token_provider,
                    resource_id=orphan_item.item_id,
                )
                unpublished.append(orphan_item)
            # Then folders, leaf-first (plan is pre-sorted longest-first).
            for orphan_folder in plan.delete_folders:
                delete_folder(
                    client,
                    plan.workspace_id,
                    orphan_folder.folder_id,
                    force=True,
                    runbook_id=runbook_id,
                    token_provider=token_provider,
                    resource_id=orphan_folder.folder_id,
                )
                deleted.append(orphan_folder)

    return ReconcileReport(
        plan=plan,
        folders_created=list(created_by_path.values()),
        items_moved=moved,
        items_unpublished=unpublished,
        folders_deleted=deleted,
    )


def reconcile_folders_from_repo(
    client: FabricRestClient,
    workspace_id: str,
    *,
    repository_directory: str | Path,
    apply: bool = False,
    unpublish_orphans: bool = False,
    force: bool = False,
    runbook_id: str | None = None,
    token_provider: TokenProvider | None = None,
    preserve_paths: Iterable[str] | None = None,
) -> ReconcileReport:
    """Top-level entry: plan + optionally apply.

    When ``apply=False`` the returned ``ReconcileReport`` has empty
    ``folders_created`` / ``items_moved`` lists and the ``plan`` surfaces
    everything that WOULD change.

    ``unpublish_orphans=True`` extends the plan to include orphan items +
    folders (items whose ``(displayName, type)`` is absent from the repo
    tree, and folders whose path is absent). When ``apply=True`` as well,
    those orphans are DELETED — requires ``force=True`` to fire; a caller
    that asks for orphan cleanup without ``force`` gets a
    :class:`DestructiveOpError` and nothing is mutated. The plan is always
    surfaced so dry-run callers can review orphans before opting into
    destruction.

    ``preserve_paths`` is the operator-supplied folder-preservation set
    (the manifest's ``folders[]`` list). Threaded through to
    :func:`plan_reconcile`; folders matching a preserved path or any of
    its ancestors are excluded from ``delete_folders``.
    """
    plan = plan_reconcile(
        client,
        workspace_id,
        repository_directory=repository_directory,
        include_orphans=unpublish_orphans,
        preserve_paths=preserve_paths,
    )
    if not apply:
        return ReconcileReport(plan=plan)
    return apply_reconcile(
        client,
        plan,
        unpublish_orphans=unpublish_orphans,
        force=force,
        runbook_id=runbook_id,
        token_provider=token_provider,
    )
