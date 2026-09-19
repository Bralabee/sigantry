"""Unit tests for sigantry_core.workspace.folders."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core.client import HttpResponse
from sigantry_core.governance import DestructiveOpError
from sigantry_core.workspace import (
    Folder,
    create_folder,
    delete_folder,
    list_folders,
    move_item,
)


def _resp(body: dict | list, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        json_body=body,
        headers={},
        request_id="req-test",
        operation_id=None,
        elapsed_ms=1.0,
    )


def test_folder_from_api_top_level() -> None:
    f = Folder.from_api({"id": "f1", "displayName": "bronze"}, workspace_id="ws1")
    assert f.id == "f1"
    assert f.display_name == "bronze"
    assert f.parent_folder_id is None
    assert f.workspace_id == "ws1"


def test_folder_from_api_nested() -> None:
    f = Folder.from_api(
        {"id": "f2", "displayName": "raw", "parentFolderId": "f1"}, workspace_id="ws1"
    )
    assert f.parent_folder_id == "f1"


def test_list_folders_paginates() -> None:
    c = MagicMock()
    c.list_paginated.return_value = iter(
        [
            {"id": "f1", "displayName": "bronze"},
            {"id": "f2", "displayName": "silver", "parentFolderId": "f1"},
        ]
    )
    result = list(list_folders(c, "ws1"))
    c.list_paginated.assert_called_once_with("/v1/workspaces/ws1/folders")
    assert [f.id for f in result] == ["f1", "f2"]
    assert result[1].parent_folder_id == "f1"


def test_create_folder_top_level_omits_parent() -> None:
    c = MagicMock()
    c.send.return_value = _resp({"id": "f1", "displayName": "bronze"}, status=201)
    f = create_folder(c, "ws1", display_name="bronze")
    args, kwargs = c.send.call_args
    assert args[:2] == ("POST", "/v1/workspaces/ws1/folders")
    assert kwargs["json"] == {"displayName": "bronze"}
    assert f.display_name == "bronze"


def test_create_folder_nested_includes_parent() -> None:
    c = MagicMock()
    c.send.return_value = _resp(
        {"id": "f2", "displayName": "raw", "parentFolderId": "f1"}, status=201
    )
    f = create_folder(c, "ws1", display_name="raw", parent_folder_id="f1")
    _, kwargs = c.send.call_args
    assert kwargs["json"] == {"displayName": "raw", "parentFolderId": "f1"}
    assert f.parent_folder_id == "f1"


def test_delete_folder_requires_force() -> None:
    c = MagicMock()
    # No audit record saved; just verifying the decorator gate.
    with pytest.raises(DestructiveOpError):
        delete_folder(c, "ws1", "f1", force=False)
    c.send.assert_not_called()


def test_delete_folder_sends_delete_when_forced() -> None:
    c = MagicMock()
    c.send.return_value = _resp({}, status=200)
    delete_folder(c, "ws1", "f1", force=True, resource_id="f1")
    args, _ = c.send.call_args
    assert args[:2] == ("DELETE", "/v1/workspaces/ws1/folders/f1")


def test_move_item_to_folder() -> None:
    c = MagicMock()
    c.send.return_value = _resp({"id": "item1", "folderId": "f2"})
    move_item(c, "ws1", "item1", target_folder_id="f2")
    args, kwargs = c.send.call_args
    assert args[:2] == ("POST", "/v1/workspaces/ws1/items/item1/move")
    assert kwargs["json"] == {"targetFolderId": "f2"}


def test_move_item_to_root_passes_null_target() -> None:
    c = MagicMock()
    c.send.return_value = _resp({"id": "item1", "folderId": None})
    move_item(c, "ws1", "item1", target_folder_id=None)
    _, kwargs = c.send.call_args
    # Null is explicit — "move to workspace root" is None not omitted.
    assert kwargs["json"] == {"targetFolderId": None}


# ---------------------------------------------------------------------------
# Audit-2026-05-07 W1.6 — cycle-safe shared parent-chain walk
# ---------------------------------------------------------------------------
#
# Falsifiability: every test below FAILS against the pre-fix
# implementation, where ``workspace.reconciler._index_folders_by_path``
# and ``sync.snapshot._index_folders_by_path`` walked the parent_folder_id
# chain with no visited-set protection. A self-referencing folder or a
# cycle in the response payload would infinite-loop the entire reconciler
# / snapshot path. The fix lifted the cycle-protected walk that already
# lived in sync/diff.py into a shared helper and threaded all three sites
# through it.


class TestParentChainCycleSafe:
    @staticmethod
    def _f(
        id_: str, display_name: str, parent_folder_id: str | None, ws_id: str = "ws-cycle"
    ) -> Folder:
        return Folder(
            id=id_,
            display_name=display_name,
            parent_folder_id=parent_folder_id,
            workspace_id=ws_id,
        )

    def test_walk_parent_chain_no_cycle_returns_root_first_segments(self) -> None:
        from sigantry_core.workspace.folders import walk_parent_chain

        folders = [
            self._f("root-id", "raw", None),
            self._f("mid-id", "UI-Created", "root-id"),
            self._f("leaf-id", "leaf", "mid-id"),
        ]
        by_id = {f.id: f for f in folders}
        assert walk_parent_chain("leaf-id", by_id) == ("raw", "UI-Created", "leaf")

    def test_walk_parent_chain_self_reference_terminates(self) -> None:
        # Pre-fix this hung forever.
        from sigantry_core.workspace.folders import walk_parent_chain

        folders = [self._f("self-id", "self", "self-id")]
        by_id = {f.id: f for f in folders}
        assert walk_parent_chain("self-id", by_id) == ("self",)

    def test_walk_parent_chain_two_node_cycle_terminates(self) -> None:
        from sigantry_core.workspace.folders import walk_parent_chain

        folders = [
            self._f("a-id", "A", "b-id"),
            self._f("b-id", "B", "a-id"),
        ]
        by_id = {f.id: f for f in folders}
        result = walk_parent_chain("a-id", by_id)
        assert set(result) == {"A", "B"}
        assert len(result) == 2

    def test_walk_parent_chain_three_node_cycle_terminates(self) -> None:
        from sigantry_core.workspace.folders import walk_parent_chain

        folders = [
            self._f("a-id", "A", "b-id"),
            self._f("b-id", "B", "c-id"),
            self._f("c-id", "C", "a-id"),
        ]
        by_id = {f.id: f for f in folders}
        result = walk_parent_chain("a-id", by_id)
        assert set(result) == {"A", "B", "C"}
        assert len(result) == 3

    def test_walk_parent_chain_none_returns_empty(self) -> None:
        from sigantry_core.workspace.folders import walk_parent_chain

        assert walk_parent_chain(None, {}) == ()

    def test_walk_parent_chain_unknown_id_returns_empty(self) -> None:
        from sigantry_core.workspace.folders import walk_parent_chain

        assert walk_parent_chain("ghost-id", {}) == ()

    def test_index_folders_by_path_does_not_hang_on_cycle(self) -> None:
        from sigantry_core.workspace.folders import index_folders_by_path

        folders = [self._f("self-id", "self", "self-id")]
        result = index_folders_by_path(folders)
        assert isinstance(result, dict)
        assert len(result) == 1

    def test_index_folder_paths_to_id_does_not_hang_on_cycle(self) -> None:
        from sigantry_core.workspace.folders import index_folder_paths_to_id

        folders = [
            self._f("a-id", "A", "b-id"),
            self._f("b-id", "B", "a-id"),
        ]
        result = index_folder_paths_to_id(folders)
        assert isinstance(result, dict)
        assert len(result) <= 2

    def test_folder_path_string_root_for_none(self) -> None:
        from sigantry_core.workspace.folders import folder_path_string

        assert folder_path_string(None, {}) == "/"

    def test_folder_path_string_root_for_unknown_id(self) -> None:
        from sigantry_core.workspace.folders import folder_path_string

        # Defensive: snapshot inconsistency. Surface as root rather
        # than raise (matches pre-fix behaviour in sync/diff.py).
        assert folder_path_string("ghost-id", {}) == "/"

    def test_folder_path_string_returns_forward_slash_path(self) -> None:
        from sigantry_core.workspace.folders import folder_path_string

        folders = [
            self._f("root-id", "raw", None),
            self._f("mid-id", "UI-Created", "root-id"),
        ]
        by_id = {f.id: f for f in folders}
        assert folder_path_string("mid-id", by_id) == "/raw/UI-Created"
