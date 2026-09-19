"""Unit tests for sigantry_core.workspace.reconciler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sigantry_core.client import HttpResponse
from sigantry_core.governance.audit import DestructiveOpError
from sigantry_core.workspace.folders import Folder
from sigantry_core.workspace.reconciler import (
    PlannedFolderDelete,
    PlannedItemUnpublish,
    ReconcilePlan,
    _index_folders_by_path,
    _normalise_preserve_set,
    _scan_repo,
    apply_reconcile,
    plan_reconcile,
    reconcile_folders_from_repo,
)


def _resp(body: dict, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        json_body=body,
        headers={},
        request_id="req-test",
        operation_id=None,
        elapsed_ms=1.0,
    )


def _make_platform(dir_path: Path, display_name: str, item_type: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    platform = dir_path / ".platform"
    platform.write_text(
        json.dumps(
            {
                "$schema": "https://example/platform/2.0.0/schema.json",
                "metadata": {"type": item_type, "displayName": display_name},
                "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"},
            }
        ),
        encoding="utf-8",
    )


def test_scan_repo_builds_folder_paths(tmp_path: Path) -> None:
    _make_platform(tmp_path / "RootNotebook.Notebook", "RootNotebook", "Notebook")
    _make_platform(tmp_path / "bronze" / "RawLakehouse.Lakehouse", "RawLakehouse", "Lakehouse")
    _make_platform(tmp_path / "bronze" / "raw" / "Ingest.Notebook", "Ingest", "Notebook")

    items = sorted(_scan_repo(tmp_path), key=lambda r: r.display_name)
    assert [r.display_name for r in items] == ["Ingest", "RawLakehouse", "RootNotebook"]
    by_name = {r.display_name: r for r in items}
    assert by_name["RootNotebook"].folder_path == ()
    assert by_name["RawLakehouse"].folder_path == ("bronze",)
    assert by_name["Ingest"].folder_path == ("bronze", "raw")


def test_index_folders_builds_path_map() -> None:
    root = Folder("f1", "bronze", None, "ws1")
    child = Folder("f2", "raw", "f1", "ws1")
    gchild = Folder("f3", "daily", "f2", "ws1")
    other = Folder("f4", "silver", None, "ws1")
    m = _index_folders_by_path([root, child, gchild, other])
    assert m[("bronze",)].id == "f1"
    assert m[("bronze", "raw")].id == "f2"
    assert m[("bronze", "raw", "daily")].id == "f3"
    assert m[("silver",)].id == "f4"


def test_plan_reconcile_creates_missing_folders_and_moves_items(tmp_path: Path) -> None:
    # Repo layout: bronze/raw/Ingest.Notebook, silver/Report.Notebook
    _make_platform(tmp_path / "bronze" / "raw" / "Ingest.Notebook", "Ingest", "Notebook")
    _make_platform(tmp_path / "silver" / "Report.Notebook", "Report", "Notebook")

    client = MagicMock()
    # Workspace has only "bronze" folder (and no "raw" under it, no "silver").
    client.list_paginated.side_effect = [
        iter([{"id": "fB", "displayName": "bronze"}]),  # folders
        iter(
            [
                {
                    "id": "i1",
                    "displayName": "Ingest",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": None,  # lives at root today — needs moving to bronze/raw
                },
                {
                    "id": "i2",
                    "displayName": "Report",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fB",  # today it's under bronze — needs moving to silver
                },
            ]
        ),
    ]

    plan = plan_reconcile(client, "ws1", repository_directory=tmp_path)
    created_paths = {pc.path for pc in plan.create_folders}
    assert created_paths == {("bronze", "raw"), ("silver",)}

    moves = {m.display_name: m for m in plan.move_items}
    assert set(moves) == {"Ingest", "Report"}
    assert moves["Ingest"].to_folder_path == ("bronze", "raw")
    assert moves["Ingest"].from_folder_id is None
    assert moves["Report"].to_folder_path == ("silver",)
    assert moves["Report"].from_folder_id == "fB"
    assert plan.unresolved_items == []


def test_plan_is_noop_when_state_already_matches(tmp_path: Path) -> None:
    _make_platform(tmp_path / "bronze" / "Ingest.Notebook", "Ingest", "Notebook")

    client = MagicMock()
    client.list_paginated.side_effect = [
        iter([{"id": "fB", "displayName": "bronze"}]),
        iter(
            [
                {
                    "id": "i1",
                    "displayName": "Ingest",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fB",
                }
            ]
        ),
    ]
    plan = plan_reconcile(client, "ws1", repository_directory=tmp_path)
    assert plan.create_folders == []
    assert plan.move_items == []


def test_plan_flags_items_missing_in_workspace(tmp_path: Path) -> None:
    _make_platform(tmp_path / "bronze" / "NotDeployedYet.Notebook", "NotDeployedYet", "Notebook")

    client = MagicMock()
    client.list_paginated.side_effect = [iter([]), iter([])]
    plan = plan_reconcile(client, "ws1", repository_directory=tmp_path)
    assert len(plan.unresolved_items) == 1
    assert plan.unresolved_items[0].display_name == "NotDeployedYet"
    assert plan.move_items == []


def test_apply_creates_folders_then_moves_items(tmp_path: Path) -> None:
    client = MagicMock()
    client.list_paginated.return_value = iter([])  # no existing folders at apply-time refresh

    # Simulate POST /folders responses — return the body with a server-assigned id.
    def fake_send(method: str, path: str, json: dict | None = None, **kwargs):
        if method == "POST" and path.endswith("/folders"):
            parent = (json or {}).get("parentFolderId")
            new_id = f"new-{(json or {})['displayName']}"
            return _resp(
                {
                    "id": new_id,
                    "displayName": (json or {})["displayName"],
                    **({"parentFolderId": parent} if parent else {}),
                },
                status=201,
            )
        if method == "POST" and "/items/" in path and path.endswith("/move"):
            return _resp({}, status=200)
        raise AssertionError(f"unexpected send({method}, {path})")

    client.send.side_effect = fake_send

    plan = ReconcilePlan(
        workspace_id="ws1",
        create_folders=[
            # intentionally out-of-order — apply must sort by path length
            type("P", (), {"path": ("bronze", "raw")})(),
            type("P", (), {"path": ("bronze",)})(),
        ],
        move_items=[
            type(
                "M",
                (),
                {
                    "item_id": "i1",
                    "display_name": "Ingest",
                    "item_type": "Notebook",
                    "from_folder_id": None,
                    "to_folder_path": ("bronze", "raw"),
                },
            )()
        ],
    )
    report = apply_reconcile(client, plan)
    # Folders created in order (shortest path first)
    created = [
        c for c in client.send.call_args_list if "/folders" in c.args[1] and c.args[0] == "POST"
    ]
    assert created[0].kwargs["json"]["displayName"] == "bronze"
    assert "parentFolderId" not in created[0].kwargs["json"]
    assert created[1].kwargs["json"]["displayName"] == "raw"
    assert created[1].kwargs["json"]["parentFolderId"] == "new-bronze"
    # Move issued with resolved target folder id
    move = next(c for c in client.send.call_args_list if c.args[1].endswith("/move"))
    assert move.args[1] == "/v1/workspaces/ws1/items/i1/move"
    assert move.kwargs["json"] == {"targetFolderId": "new-raw"}
    assert len(report.folders_created) == 2
    assert len(report.items_moved) == 1


def test_apply_refuses_move_when_target_folder_missing(tmp_path: Path) -> None:
    """A non-empty target folder absent at apply time must RAISE, not root-move.

    Race: operator A plans a move of i1 into /silver (folder exists at plan
    time, so no create is planned); operator B deletes /silver before A's apply
    reaches the move phase. The re-list at apply time no longer contains
    /silver, so the lookup degrades to target_id=None -- the SAME value that
    means 'move to workspace root'. Silently moving i1 to root while reporting
    a successful move to /silver is the bug; apply must refuse instead.
    """
    client = MagicMock()
    client.list_paginated.return_value = iter([])  # /silver gone at apply-time refresh

    def fake_send(method: str, path: str, json: dict | None = None, **kwargs):
        if method == "POST" and "/items/" in path and path.endswith("/move"):
            return _resp({}, status=200)
        raise AssertionError(f"unexpected send({method}, {path})")

    client.send.side_effect = fake_send

    plan = ReconcilePlan(
        workspace_id="ws1",
        create_folders=[],  # /silver existed at plan time, so nothing to create
        move_items=[
            type(
                "M",
                (),
                {
                    "item_id": "i1",
                    "display_name": "Ingest",
                    "item_type": "Notebook",
                    "from_folder_id": None,
                    "to_folder_path": ("silver",),
                },
            )()
        ],
    )
    with pytest.raises(ValueError, match="target folder no longer exists"):
        apply_reconcile(client, plan)
    # And crucially: no move-to-root was issued.
    moves = [c for c in client.send.call_args_list if c.args[1].endswith("/move")]
    assert moves == []


def test_reconcile_folders_from_repo_plan_only(tmp_path: Path) -> None:
    _make_platform(tmp_path / "bronze" / "X.Notebook", "X", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [iter([]), iter([])]
    # apply=False: no side-effecting .send calls should fire
    report = reconcile_folders_from_repo(client, "ws1", repository_directory=tmp_path, apply=False)
    assert report.folders_created == []
    assert report.items_moved == []
    assert len(report.plan.create_folders) == 1
    assert report.plan.create_folders[0].path == ("bronze",)
    client.send.assert_not_called()


# --- Orphan cleanup (include_orphans / unpublish_orphans / force) ----------


def test_plan_orphans_ignored_by_default(tmp_path: Path) -> None:
    """Without include_orphans=True, a workspace item absent from the repo
    is NOT listed for unpublish — additive-only is the baseline behaviour."""
    _make_platform(tmp_path / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter([]),  # folders: none
        iter(
            [
                {"id": "k", "displayName": "keep", "type": "Notebook", "workspaceId": "ws1"},
                {"id": "o", "displayName": "orphan", "type": "Notebook", "workspaceId": "ws1"},
            ]
        ),
    ]
    plan = plan_reconcile(client, "ws1", repository_directory=tmp_path)
    assert plan.unpublish_items == []
    assert plan.delete_folders == []


def test_plan_include_orphans_lists_stale_items_and_folders(tmp_path: Path) -> None:
    """Orphan detection: items + folders present in the workspace but absent
    from the repo tree are listed; the delete-folders list is leaf-first."""
    _make_platform(tmp_path / "bronze" / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter(
            [
                {"id": "fb", "displayName": "bronze"},
                # "archive" and "archive/old" are not referenced by repo — orphan.
                {"id": "fa", "displayName": "archive"},
                {"id": "fo", "displayName": "old", "parentFolderId": "fa"},
            ]
        ),
        iter(
            [
                {
                    "id": "i_keep",
                    "displayName": "keep",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fb",
                },
                # Two orphan items — one a Notebook, one a Lakehouse.
                {
                    "id": "i_o1",
                    "displayName": "stale",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fo",
                },
                {
                    "id": "i_o2",
                    "displayName": "archived_lh",
                    "type": "Lakehouse",
                    "workspaceId": "ws1",
                    "folderId": None,
                },
            ]
        ),
    ]
    plan = plan_reconcile(client, "ws1", repository_directory=tmp_path, include_orphans=True)
    unpub = {(p.display_name, p.item_type) for p in plan.unpublish_items}
    assert unpub == {("stale", "Notebook"), ("archived_lh", "Lakehouse")}
    # Orphan folders sorted leaf-first: "archive/old" before "archive".
    assert [p.path for p in plan.delete_folders] == [("archive", "old"), ("archive",)]
    # "bronze" is referenced by the repo → NOT an orphan.
    assert ("bronze",) not in [p.path for p in plan.delete_folders]


def test_apply_orphans_requires_force_when_opted_in(tmp_path: Path) -> None:
    """unpublish_orphans=True + force=False when plan has orphans raises
    DestructiveOpError BEFORE any mutation. Mirrors the @destructive_op
    contract used by delete_item / delete_folder / unpublish_all_orphan_items."""
    plan = ReconcilePlan(
        workspace_id="ws1",
        unpublish_items=[
            PlannedItemUnpublish(
                item_id="i1",
                display_name="stale",
                item_type="Notebook",
                current_folder_id=None,
            )
        ],
    )
    client = MagicMock()
    client.list_paginated.return_value = iter([])  # apply still re-lists folders
    with pytest.raises(DestructiveOpError, match="force=True"):
        apply_reconcile(client, plan, unpublish_orphans=True, force=False)
    # Nothing deleted.
    assert all(c.args[0] != "DELETE" for c in client.send.call_args_list)


def test_apply_orphans_without_opt_in_leaves_them_alone(tmp_path: Path) -> None:
    """Even when plan.unpublish_items is non-empty, apply with
    unpublish_orphans=False is a no-op for orphans — the additive-only
    default takes precedence."""
    plan = ReconcilePlan(
        workspace_id="ws1",
        unpublish_items=[
            PlannedItemUnpublish(
                item_id="i1",
                display_name="stale",
                item_type="Notebook",
                current_folder_id=None,
            )
        ],
        delete_folders=[PlannedFolderDelete(folder_id="fx", path=("archive",))],
    )
    client = MagicMock()
    client.list_paginated.return_value = iter([])
    report = apply_reconcile(client, plan)  # unpublish_orphans defaults to False
    assert report.items_unpublished == []
    assert report.folders_deleted == []
    assert all(c.args[0] != "DELETE" for c in client.send.call_args_list)


def test_apply_orphans_with_force_deletes_items_then_folders(tmp_path: Path) -> None:
    """Items are deleted before folders; folders are deleted in the order
    they appear in the plan (leaf-first, as populated by plan_reconcile)."""
    plan = ReconcilePlan(
        workspace_id="ws1",
        unpublish_items=[
            PlannedItemUnpublish(
                item_id="i1",
                display_name="stale",
                item_type="Notebook",
                current_folder_id="fa_old",
            )
        ],
        delete_folders=[
            PlannedFolderDelete(folder_id="fa_old", path=("archive", "old")),
            PlannedFolderDelete(folder_id="fa", path=("archive",)),
        ],
    )
    client = MagicMock()
    client.list_paginated.return_value = iter([])
    client.send.return_value = _resp({}, status=200)

    report = apply_reconcile(client, plan, unpublish_orphans=True, force=True)

    deletes = [c for c in client.send.call_args_list if c.args[0] == "DELETE"]
    assert [c.args[1] for c in deletes] == [
        "/v1/workspaces/ws1/items/i1",
        "/v1/workspaces/ws1/folders/fa_old",
        "/v1/workspaces/ws1/folders/fa",
    ]
    assert len(report.items_unpublished) == 1
    assert len(report.folders_deleted) == 2


def test_reconcile_folders_from_repo_dry_run_surfaces_orphans(tmp_path: Path) -> None:
    """apply=False + unpublish_orphans=True: orphans are LISTED in the plan
    but never touched. This is the intended preview workflow before a
    destructive re-run."""
    _make_platform(tmp_path / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter([{"id": "fa", "displayName": "archive"}]),
        iter(
            [
                {"id": "k", "displayName": "keep", "type": "Notebook", "workspaceId": "ws1"},
                {
                    "id": "o",
                    "displayName": "stale",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fa",
                },
            ]
        ),
    ]
    report = reconcile_folders_from_repo(
        client,
        "ws1",
        repository_directory=tmp_path,
        apply=False,
        unpublish_orphans=True,
    )
    assert [p.display_name for p in report.plan.unpublish_items] == ["stale"]
    assert [p.path for p in report.plan.delete_folders] == [("archive",)]
    client.send.assert_not_called()


# --------------------------------------------------------------------------
# Folder-preservation contract (manifest.folders[]) -- Council D constraint
# #5. Tests cover the wiring between operator-supplied preservation paths
# and the reconciler's delete_folders plan.
# --------------------------------------------------------------------------


def test_normalise_preserve_set_expands_ancestors_and_strips_slashes() -> None:
    """Each preserved entry expands to itself + all ancestor tuples, with
    leading/trailing slashes stripped. This is the load-bearing semantics
    that lets ``folders: [/raw/UI-Created]`` keep ``/raw`` alive too."""
    expanded = _normalise_preserve_set(["/raw/UI-Created", "curated/Adhoc/", "/single", ""])
    assert expanded == frozenset(
        {
            ("raw",),
            ("raw", "UI-Created"),
            ("curated",),
            ("curated", "Adhoc"),
            ("single",),
        }
    )
    # Empty / None inputs short-circuit to an empty set (no exceptions).
    assert _normalise_preserve_set(None) == frozenset()
    assert _normalise_preserve_set([]) == frozenset()


def test_plan_preserve_paths_excludes_exact_orphan_folder(tmp_path: Path) -> None:
    """An orphan folder whose path is in the preservation set is filtered
    out of delete_folders. The non-preserved orphan is still listed."""
    _make_platform(tmp_path / "bronze" / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter(
            [
                {"id": "fb", "displayName": "bronze"},
                # Two orphan folders at root level.
                {"id": "fui", "displayName": "UI-Created"},
                {"id": "fa", "displayName": "archive"},
            ]
        ),
        iter(
            [
                {
                    "id": "k",
                    "displayName": "keep",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fb",
                }
            ]
        ),
    ]
    plan = plan_reconcile(
        client,
        "ws1",
        repository_directory=tmp_path,
        include_orphans=True,
        preserve_paths=["/UI-Created"],
    )
    deleted_paths = [p.path for p in plan.delete_folders]
    # /UI-Created is preserved; /archive remains an orphan.
    assert ("UI-Created",) not in deleted_paths
    assert ("archive",) in deleted_paths


def test_plan_preserve_paths_excludes_ancestor_of_preserved(tmp_path: Path) -> None:
    """Declaring a leaf path implicitly preserves its ancestors -- otherwise
    leaf-first deletion of the parent would still drop the protected child.
    Worked example from the folder-preservation runbook."""
    _make_platform(tmp_path / "bronze" / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter(
            [
                {"id": "fb", "displayName": "bronze"},
                # /raw and /raw/UI-Created are both orphan from the repo's POV.
                {"id": "fr", "displayName": "raw"},
                {"id": "fui", "displayName": "UI-Created", "parentFolderId": "fr"},
            ]
        ),
        iter(
            [
                {
                    "id": "k",
                    "displayName": "keep",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fb",
                }
            ]
        ),
    ]
    plan = plan_reconcile(
        client,
        "ws1",
        repository_directory=tmp_path,
        include_orphans=True,
        preserve_paths=["/raw/UI-Created"],
    )
    deleted_paths = [p.path for p in plan.delete_folders]
    # Both /raw and /raw/UI-Created excluded -- ancestor preservation kicks in.
    assert ("raw",) not in deleted_paths
    assert ("raw", "UI-Created") not in deleted_paths


def test_plan_preserve_paths_is_no_op_when_include_orphans_false(
    tmp_path: Path,
) -> None:
    """preserve_paths is irrelevant when include_orphans=False: delete_folders
    is empty regardless. Documents that preservation only matters in the
    orphan-deletion code path."""
    _make_platform(tmp_path / "bronze" / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter(
            [
                {"id": "fb", "displayName": "bronze"},
                {"id": "fa", "displayName": "archive"},  # would be orphan
            ]
        ),
        iter(
            [
                {
                    "id": "k",
                    "displayName": "keep",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                    "folderId": "fb",
                }
            ]
        ),
    ]
    plan = plan_reconcile(
        client,
        "ws1",
        repository_directory=tmp_path,
        include_orphans=False,
        preserve_paths=["/UI-Created", "/raw"],
    )
    assert plan.delete_folders == []


def test_reconcile_folders_from_repo_threads_preserve_paths_through(
    tmp_path: Path,
) -> None:
    """End-to-end through the public top-level entry: preserve_paths reaches
    plan_reconcile and filters the dry-run delete_folders correctly."""
    _make_platform(tmp_path / "keep.Notebook", "keep", "Notebook")
    client = MagicMock()
    client.list_paginated.side_effect = [
        iter(
            [
                {"id": "fp", "displayName": "preserved"},
                {"id": "fo", "displayName": "stale"},
            ]
        ),
        iter(
            [
                {
                    "id": "k",
                    "displayName": "keep",
                    "type": "Notebook",
                    "workspaceId": "ws1",
                }
            ]
        ),
    ]
    report = reconcile_folders_from_repo(
        client,
        "ws1",
        repository_directory=tmp_path,
        apply=False,
        unpublish_orphans=True,
        preserve_paths=["preserved"],
    )
    deleted_paths = [p.path for p in report.plan.delete_folders]
    assert ("preserved",) not in deleted_paths
    assert ("stale",) in deleted_paths
    client.send.assert_not_called()
