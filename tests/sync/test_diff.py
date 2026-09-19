"""Tests for ``sigantry_core.sync.diff`` -- Plan 13-06 / DRIFT-01..02 / D-23 / D-24.

Replaces the 9 Wave 0 xfail stubs with real assertions over the bucket
algorithm + the SemVer-pinned wire keyset. CLI exit-code tests live in
``tests/sync/test_cli_diff.py`` (Task 2). The committed-schema contract
test lives in ``tests/sync/test_drift_schema_committed.py`` (Task 1).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from sigantry_core.sync.diff import (
    DriftReport,
    diff_workspace_against_manifest,
)
from sigantry_core.sync.snapshot import WorkspaceSnapshot
from sigantry_core.workspace.folders import Folder
from sigantry_core.workspace.items import Item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, body: str) -> Path:
    """Materialise a sync.yml under ``tmp_path`` and return the path."""
    p = tmp_path / "sync.yml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _build_snapshot(
    workspace_id: str,
    *,
    folders: list[Folder] | None = None,
    items: list[Item] | None = None,
) -> WorkspaceSnapshot:
    """Build a synthetic :class:`WorkspaceSnapshot` for the bucket-algorithm tests.

    Saves having to spin up a respx-backed FabricRestClient for tests
    that only exercise the diff logic against an in-memory snapshot --
    the production fetch path is exercised separately by
    ``tests/sync/test_snapshot.py``.
    """
    folders = folders or []
    items = items or []
    folders_by_id: dict[str, Folder] = {f.id: f for f in folders}
    items_by_id: dict[str, Item] = {it.id: it for it in items}

    # Build the same forward-slash path index the production code uses.
    folder_path_index: dict[str, str] = {}
    for f in folders:
        segs: list[str] = []
        node: Folder | None = f
        while node is not None:
            segs.append(node.display_name)
            parent_id = node.parent_folder_id
            node = folders_by_id.get(parent_id) if parent_id else None
        path = "/" + "/".join(reversed(segs))
        folder_path_index[path] = f.id

    item_to_folder: dict[str, str | None] = {it.id: it.folder_id for it in items}

    return WorkspaceSnapshot(
        workspace_id=workspace_id,
        folders_by_id=folders_by_id,
        items_by_id=items_by_id,
        folder_path_index=folder_path_index,
        item_to_folder=item_to_folder,
    )


# ---------------------------------------------------------------------------
# 1. test_diff_detects_added_item -- workspace has an extra
# ---------------------------------------------------------------------------


def test_diff_detects_added_item(tmp_path: Path) -> None:
    """Workspace has an extra item not in manifest -> entry in `added` array."""
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /
            display_name: Nb1
            logical_id: nb-1-guid
        """,
    )
    folder = Folder(id="f-raw", display_name="raw", parent_folder_id=None, workspace_id="ws")
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id=None,
        ),
        Item(
            id="nb-extra-guid",
            display_name="ExtraNb",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id="f-raw",
        ),
    ]
    snap = _build_snapshot("ws", folders=[folder], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    assert len(report.added) == 1, report.added
    assert report.added[0]["display_name"] == "ExtraNb"
    assert report.added[0]["folder_path"] == "/raw"
    assert report.added[0]["type"] == "Notebook"
    assert report.added[0]["logical_id"] == "nb-extra-guid"
    assert len(report.removed) == 0
    assert len(report.modified) == 0


# ---------------------------------------------------------------------------
# 2. test_diff_detects_removed_item -- manifest has an item missing in workspace
# ---------------------------------------------------------------------------


def test_diff_detects_removed_item(tmp_path: Path) -> None:
    """Manifest has 2 items, workspace has 1 -> entry in `removed` array."""
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /
            display_name: Nb1
            logical_id: nb-1-guid
          - local_path: ./Nb2.ipynb
            type: Notebook
            target_folder: /
            display_name: MissingNb
            logical_id: nb-2-guid
        """,
    )
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id=None,
        ),
    ]
    snap = _build_snapshot("ws", folders=[], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    assert len(report.removed) == 1, report.removed
    assert report.removed[0]["logical_id"] == "nb-2-guid"
    assert report.removed[0]["display_name"] == "MissingNb"
    assert len(report.added) == 0
    assert len(report.modified) == 0


# ---------------------------------------------------------------------------
# 3. test_diff_detects_modified_folder_path -- D-24 metadata-only modified
# ---------------------------------------------------------------------------


def test_diff_detects_modified_folder_path(tmp_path: Path) -> None:
    """D-24: folder_path differs -> entry in `modified` with fields_changed=['folder_path']."""
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /A
            display_name: Nb1
            logical_id: nb-1-guid
        """,
    )
    folder = Folder(
        id="f-b",
        display_name="B",
        parent_folder_id=None,
        workspace_id="ws",
    )
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id="f-b",
        ),
    ]
    snap = _build_snapshot("ws", folders=[folder], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    assert len(report.modified) == 1, report.modified
    assert report.modified[0]["logical_id"] == "nb-1-guid"
    assert report.modified[0]["fields_changed"] == ["folder_path"]
    assert len(report.added) == 0
    assert len(report.removed) == 0


# ---------------------------------------------------------------------------
# 4. test_diff_unchanged_array_populated -- match across both sides
# ---------------------------------------------------------------------------


def test_diff_unchanged_array_populated(tmp_path: Path) -> None:
    """manifest + workspace match -> entry in `unchanged` with logical_id only."""
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /raw
            display_name: Nb1
            logical_id: nb-1-guid
        """,
    )
    folder = Folder(
        id="f-raw",
        display_name="raw",
        parent_folder_id=None,
        workspace_id="ws",
    )
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id="f-raw",
        ),
    ]
    snap = _build_snapshot("ws", folders=[folder], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    assert len(report.unchanged) == 1, report.unchanged
    assert report.unchanged[0]["logical_id"] == "nb-1-guid"
    assert set(report.unchanged[0].keys()) == {"logical_id"}
    assert len(report.added) == 0
    assert len(report.removed) == 0
    assert len(report.modified) == 0
    assert report.has_drift() is False


# ---------------------------------------------------------------------------
# 5. test_diff_json_schema -- LOAD-BEARING D-23 keyset
# ---------------------------------------------------------------------------


def test_diff_json_schema() -> None:
    """LOAD-BEARING (D-23 / D-37): top-level + per-entry keysets locked.

    Build a synthetic :class:`DriftReport` with one entry per category
    and assert the canonical wire-shape:

    * Top-level keyset: ``{schema_version, added, removed, modified, unchanged}``.
    * ``added`` / ``removed`` per-entry keyset: ``{logical_id, display_name, type, folder_path}``.
    * ``modified`` per-entry keyset: ``{logical_id, fields_changed}``.
    * ``unchanged`` per-entry keyset: ``{logical_id}``.

    Adding any field requires a SemVer-minor bump on
    ``docs/reference/drift-schema.json`` AND on the runtime model.
    """
    report = DriftReport(
        schema_version="1.0.0",
        added=[
            {
                "logical_id": "a",
                "display_name": "A",
                "type": "Notebook",
                "folder_path": "/",
            }
        ],
        removed=[
            {
                "logical_id": "b",
                "display_name": "B",
                "type": "Notebook",
                "folder_path": "/",
            }
        ],
        modified=[{"logical_id": "c", "fields_changed": ["folder_path"]}],
        unchanged=[{"logical_id": "d"}],
    )
    payload = report.to_json()

    assert set(payload.keys()) == {
        "schema_version",
        "added",
        "removed",
        "modified",
        "unchanged",
    }
    assert payload["schema_version"] == "1.0.0"

    assert set(payload["added"][0].keys()) == {
        "logical_id",
        "display_name",
        "type",
        "folder_path",
    }
    assert set(payload["removed"][0].keys()) == {
        "logical_id",
        "display_name",
        "type",
        "folder_path",
    }
    assert set(payload["modified"][0].keys()) == {
        "logical_id",
        "fields_changed",
    }
    assert set(payload["unchanged"][0].keys()) == {"logical_id"}


# ---------------------------------------------------------------------------
# 6. test_diff_synth_key_for_bootstrap_manifest_without_logical_ids
# ---------------------------------------------------------------------------


def test_diff_synth_key_for_bootstrap_manifest_without_logical_ids(
    tmp_path: Path,
) -> None:
    """Bootstrap manifest (no explicit logical_ids) joins via synth:<display>.<type>.

    Operator's primary use case: 9 raw .ipynb without prior pull. The
    diff should still be deterministic when the manifest carries no
    explicit ids -- both sides synthesise a ``synth:<display_name>.<type>``
    key so a workspace item with the same display_name + type matches a
    manifest entry without forcing the operator to pull-then-edit.
    """
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /
            display_name: Nb1
        """,
    )
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id=None,
        ),
    ]
    snap = _build_snapshot("ws", folders=[], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    # No drift: same display_name + type + folder_path on both sides.
    assert report.has_drift() is False, report.to_json()
    assert len(report.unchanged) == 1
    assert report.unchanged[0]["logical_id"] == "synth:Nb1.Notebook"


# ---------------------------------------------------------------------------
# 7. test_diff_modified_detection_uses_only_locked_fields_d24
# ---------------------------------------------------------------------------


def test_diff_modified_detection_uses_only_locked_fields_d24(
    tmp_path: Path,
) -> None:
    """D-24: modified detection compares display_name + type + folder_path only.

    The `description` field on workspace items is INTENTIONALLY ignored
    because it is not in the locked field set. This test exercises a
    case where description differs but the locked fields match, and
    asserts the item lands in ``unchanged`` not ``modified``.
    """
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /
            display_name: Nb1
            logical_id: nb-1-guid
        """,
    )
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1",
            type="Notebook",
            workspace_id="ws",
            description="some long workspace-side description",
            sensitivity_label_id=None,
            folder_id=None,
        ),
    ]
    snap = _build_snapshot("ws", folders=[], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    # Description differs but D-24 only compares the locked fields.
    assert len(report.modified) == 0, report.modified
    assert len(report.unchanged) == 1


# ---------------------------------------------------------------------------
# 8. test_diff_empty_manifest_and_empty_workspace
# ---------------------------------------------------------------------------


def test_diff_empty_manifest_and_empty_workspace(tmp_path: Path) -> None:
    """Empty manifest + empty workspace -> all four buckets empty; has_drift False."""
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items: []
        """,
    )
    snap = _build_snapshot("ws", folders=[], items=[])
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    assert report.added == []
    assert report.removed == []
    assert report.modified == []
    assert report.unchanged == []
    assert report.has_drift() is False


# ---------------------------------------------------------------------------
# 9. test_diff_modified_detection_multiple_fields
# ---------------------------------------------------------------------------


def test_diff_modified_detection_multiple_fields(tmp_path: Path) -> None:
    """fields_changed contains every locked field that differs (sorted)."""
    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /A
            display_name: Nb1Manifest
            logical_id: nb-1-guid
        """,
    )
    folder = Folder(id="f-b", display_name="B", parent_folder_id=None, workspace_id="ws")
    items = [
        Item(
            id="nb-1-guid",
            display_name="Nb1Workspace",  # display_name differs
            type="Notebook",  # type matches
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id="f-b",  # folder_path differs (/A vs /B)
        ),
    ]
    snap = _build_snapshot("ws", folders=[folder], items=items)
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)
    assert len(report.modified) == 1, report.modified
    assert report.modified[0]["fields_changed"] == [
        "display_name",
        "folder_path",
    ]


# ---------------------------------------------------------------------------
# 10. test_diff_workspace_synth_alias_collision_surfaces_duplicates
# ---------------------------------------------------------------------------


def test_diff_workspace_synth_alias_collision_surfaces_duplicates(
    tmp_path: Path,
    caplog,
) -> None:
    """WR-05: workspace items sharing (display_name, type) must not be silently dropped.

    REVIEW.md WR-05: pre-fix the synthetic-alias pass in
    ``_build_workspace_index`` silently dropped the SECOND workspace
    item when two items shared ``(display_name, type)``. A bootstrap
    manifest joining via the synth key would match only the first
    item and the diff would surface "1 unchanged + 0 added" -- silently
    discarding the duplicate.

    Post-fix:
    1. The collision is logged at WARNING with both item IDs so the
       operator can investigate the duplicate display_name in their
       workspace.
    2. The GUID-keyed entry for the second item still appears in the
       index, so the diff correctly classifies it as ``added``
       (manifest declared one item, workspace has two -> 1 unchanged +
       1 added).

    Together these give operators the right semantics + observability:
    duplicates are visible rather than swallowed.
    """
    import logging

    manifest_path = _write_manifest(
        tmp_path,
        """
        schema_version: "1.0.0"
        items:
          - local_path: ./Nb1.ipynb
            type: Notebook
            target_folder: /
            display_name: DuplicateNb
        """,
    )
    items = [
        Item(
            id="nb-first-guid",
            display_name="DuplicateNb",
            type="Notebook",
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id=None,
        ),
        Item(
            id="nb-second-guid",
            display_name="DuplicateNb",  # same display_name
            type="Notebook",  # same type -> synth_key collision
            workspace_id="ws",
            description=None,
            sensitivity_label_id=None,
            folder_id=None,
        ),
    ]
    snap = _build_snapshot("ws", folders=[], items=items)

    caplog.set_level(logging.WARNING, logger="sigantry_core.sync.diff")
    report = diff_workspace_against_manifest(manifest_path, "ws", snapshot=snap)

    # Collision logged at WARNING with both IDs.
    matching = [
        rec for rec in caplog.records if "diff_workspace_synth_alias_collision" in rec.message
    ]
    assert matching, (
        "expected a synth_alias_collision WARN log when two workspace "
        "items shared (display_name, type); got log records: "
        f"{[r.message for r in caplog.records]}"
    )
    msg = matching[0].message
    assert "DuplicateNb" in msg
    # Both item GUIDs surface in the WARN message.
    assert "nb-first-guid" in msg
    assert "nb-second-guid" in msg

    # Bucket semantics: manifest has 1 entry joining via synth key;
    # workspace has 2 items sharing (display_name, type). The synth
    # alias points to the FIRST iterated item -> ``unchanged``. The
    # SECOND item is still indexed under its GUID -> ``added``. Pre-fix
    # the second item silently disappeared from the report.
    total_surfaced = (
        len(report.unchanged) + len(report.added) + len(report.removed) + len(report.modified)
    )
    # Both workspace items must surface SOMEWHERE (no silent drop).
    assert total_surfaced >= 2, (
        "WR-05 regression: workspace items duplicated on (display_name, type) "
        f"silently dropped; got total_surfaced={total_surfaced} "
        f"unchanged={report.unchanged} added={report.added} "
        f"removed={report.removed} modified={report.modified}"
    )
    # Specifically: 1 unchanged (the manifest match) + 1 added (the
    # duplicate workspace item).
    assert len(report.added) == 1, (
        f"expected the duplicate workspace item to surface as `added`; got added={report.added}"
    )
    assert len(report.unchanged) == 1, (
        f"expected the manifest entry to match one workspace item via the "
        f"synth alias; got unchanged={report.unchanged}"
    )
