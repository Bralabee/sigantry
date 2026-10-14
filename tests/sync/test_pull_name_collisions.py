"""``sync pull`` gives same-name items their own directories and refuses names ``sync.yml`` refuses.

Issue #67. An item is written to ``<into>/<folder path>/<display name>/``
unless another item in the same folder has the same display name; then each
of them goes to ``<display name>.<Type>/``, the Fabric Git integration's
naming. Names are compared ignoring case, as Windows and macOS file systems
compare them. Before this, a Report and a SemanticModel both named ``Sales``
were written to one directory, the second item's files overwriting the
first's, and both ``sync.yml`` entries pointed at it.

Every item is checked against the rules ``sync.yml`` holds an item to
(:class:`~sigantry_core.sync.manifest.SyncItem`) before any definition is
fetched. A name such as ``Sales.v2`` or ``Q1: Sales`` used to be written
under ``--into`` and only then fail, with a raw pydantic error and no
``sync.yml``.

The HTTP transport is faked with ``respx``; nothing here reaches a network.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
import yaml

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import FABRIC_AUDIENCE
from sigantry_core.client import FabricRestClient
from sigantry_core.sync.apply import _pack_phase
from sigantry_core.sync.errors import PullItemNameRefusedError
from sigantry_core.sync.manifest import load_manifest
from sigantry_core.sync.pull import pull_workspace

_WS = "ws-pull-collisions"
_SEGMENTS = {"Notebook": "notebooks", "Report": "reports", "SemanticModel": "semanticModels"}
_CONTENT_FILE = {
    "Notebook": "notebook-content.ipynb",
    "Report": "definition.pbir",
    "SemanticModel": "definition.pbism",
}


@dataclass(frozen=True)
class _Item:
    item_id: str
    name: str
    type: str
    folder_id: str | None = None


@dataclass
class _Pulled:
    into: Path
    routes: dict[str, respx.Route]
    raised: Exception | None

    @property
    def fetched(self) -> list[str]:
        return sorted(item_id for item_id, route in self.routes.items() if route.called)


def _client() -> FabricRestClient:
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _platform(item: _Item) -> str:
    """The item's own ``.platform``; the logicalId tells two items' files apart."""
    return json.dumps(
        {
            "metadata": {"type": item.type, "displayName": item.name},
            "config": {"version": "2.0", "logicalId": item.item_id},
        },
        indent=2,
    )


def _content(item: _Item) -> str:
    if item.type == "Notebook":
        return json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})
    return json.dumps({"owner": item.item_id})


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _pull(
    tmp_path: Path, items: list[_Item], folders: list[tuple[str, str, str | None]] = ()
) -> _Pulled:
    """Pull ``items`` from a fake workspace holding ``folders`` (id, name, parent id)."""
    into = tmp_path / "into"
    with respx.mock(base_url=FABRIC_AUDIENCE, assert_all_called=False) as router:
        router.get(f"/v1/workspaces/{_WS}/folders").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": fid,
                            "displayName": name,
                            "parentFolderId": parent,
                            "workspaceId": _WS,
                        }
                        for fid, name, parent in folders
                    ]
                },
            )
        )
        router.get(f"/v1/workspaces/{_WS}/items").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": it.item_id,
                            "displayName": it.name,
                            "type": it.type,
                            "workspaceId": _WS,
                            "description": None,
                            "folderId": it.folder_id,
                        }
                        for it in items
                    ]
                },
            )
        )
        routes = {
            it.item_id: router.post(
                f"/v1/workspaces/{_WS}/{_SEGMENTS[it.type]}/{it.item_id}/getDefinition"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "definition": {
                            "parts": [
                                {"path": ".platform", "payload": _b64(_platform(it))},
                                {
                                    "path": _CONTENT_FILE[it.type],
                                    "payload": _b64(_content(it)),
                                    "payloadType": "InlineBase64",
                                },
                            ]
                        }
                    },
                )
            )
            for it in items
        }
        raised: Exception | None = None
        with _client() as client:
            try:
                pull_workspace(_WS, into=into, client=client)
            except Exception as exc:
                raised = exc
    return _Pulled(into, routes, raised)


def _local_paths(into: Path) -> dict[str, str]:
    manifest = yaml.safe_load((into / "sync.yml").read_text(encoding="utf-8"))
    return {entry["logical_id"]: entry["local_path"] for entry in manifest["items"]}


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# -- same-name items -----------------------------------------------------------


def test_same_name_items_in_one_folder_get_a_directory_each(tmp_path: Path) -> None:
    items = [
        _Item("rep-1", "Sales", "Report", "f-gold"),
        _Item("sm-1", "Sales", "SemanticModel", "f-gold"),
        _Item("nb-1", "Sales", "Notebook", "f-gold"),
        _Item("rep-2", "Orders", "Report", "f-gold"),
    ]
    pulled = _pull(tmp_path, items, folders=[("f-gold", "Gold", None)])

    assert pulled.raised is None, repr(pulled.raised)
    assert pulled.fetched == ["nb-1", "rep-1", "rep-2", "sm-1"]
    gold = pulled.into / "Gold"
    assert sorted(p.name for p in gold.iterdir()) == [
        "Orders",
        "Sales.Notebook",
        "Sales.Report",
        "Sales.SemanticModel",
    ]
    for it in items:
        directory = gold / ("Orders" if it.name == "Orders" else f"Sales.{it.type}")
        assert (directory / ".platform").read_text(encoding="utf-8") == _platform(it)
        assert (directory / _CONTENT_FILE[it.type]).read_text(encoding="utf-8") == _content(it)
    assert _local_paths(pulled.into) == {
        "rep-1": "Gold/Sales.Report",
        "sm-1": "Gold/Sales.SemanticModel",
        "nb-1": "Gold/Sales.Notebook/notebook-content.ipynb",
        "rep-2": "Gold/Orders",
    }


def test_same_name_in_different_folders_keeps_plain_directories(tmp_path: Path) -> None:
    items = [_Item("rep-1", "Sales", "Report", "f-gold"), _Item("sm-1", "Sales", "SemanticModel")]
    pulled = _pull(tmp_path, items, folders=[("f-gold", "Gold", None)])

    assert pulled.raised is None, repr(pulled.raised)
    assert _local_paths(pulled.into) == {"rep-1": "Gold/Sales", "sm-1": "Sales"}


def test_names_that_differ_only_in_case_count_as_the_same_name(tmp_path: Path) -> None:
    items = [_Item("rep-1", "Sales", "Report"), _Item("sm-1", "sales", "SemanticModel")]
    pulled = _pull(tmp_path, items)

    assert pulled.raised is None, repr(pulled.raised)
    assert _local_paths(pulled.into) == {"rep-1": "Sales.Report", "sm-1": "sales.SemanticModel"}
    assert (pulled.into / "Sales.Report" / ".platform").read_text(encoding="utf-8") == (
        _platform(items[0])
    )
    assert (pulled.into / "sales.SemanticModel" / ".platform").read_text(encoding="utf-8") == (
        _platform(items[1])
    )


@pytest.mark.parametrize(
    ("label", "items", "folders"),
    [
        (
            "one type, names differing only in case",
            [_Item("rep-1", "Sales", "Report"), _Item("rep-2", "sales", "Report")],
            [],
        ),
        (
            "folders whose paths differ only in case",
            [_Item("rep-1", "Sales", "Report", "f-1"), _Item("rep-2", "Sales", "Report", "f-2")],
            [("f-1", "Gold", None), ("f-2", "gold", None)],
        ),
    ],
)
def test_items_left_with_one_directory_are_refused_before_any_fetch(
    tmp_path: Path, label: str, items: list[_Item], folders: list[tuple[str, str, str | None]]
) -> None:
    pulled = _pull(tmp_path, items, folders=folders)

    assert isinstance(pulled.raised, PullItemNameRefusedError), repr(pulled.raised)
    assert sorted(r.item_id for r in pulled.raised.refused) == ["rep-1", "rep-2"]
    assert "compared ignoring case" in str(pulled.raised)
    assert pulled.fetched == []
    assert list(pulled.into.iterdir()) == []


# -- names sync.yml refuses ----------------------------------------------------


@pytest.mark.parametrize(
    ("label", "item", "folders", "reason"),
    [
        ("a '.' in the name", _Item("rep-x", "Sales.v2", "Report"), [], "banned characters"),
        ("a ':' in the name", _Item("rep-x", "Q1: Sales", "Report"), [], "banned characters"),
        (
            "'..' under a folder",
            _Item("rep-x", "..", "Report", "f-b"),
            [("f-a", "A", None), ("f-b", "B", "f-a")],
            "banned characters",
        ),
        (
            "a banned character in the folder path",
            _Item("rep-x", "Sales", "Report", "f-a"),
            [("f-a", "A.old", None)],
            "target_folder segment 'A.old'",
        ),
    ],
)
def test_a_name_sync_yml_refuses_is_refused_before_any_fetch(
    tmp_path: Path,
    label: str,
    item: _Item,
    folders: list[tuple[str, str, str | None]],
    reason: str,
) -> None:
    # A plain item is listed first, so a refusal that came after its fetch would show.
    plain = _Item("rep-ok", "Plain", "Report")
    before = _tree(tmp_path)

    pulled = _pull(tmp_path, [plain, item], folders=folders)

    assert isinstance(pulled.raised, PullItemNameRefusedError), repr(pulled.raised)
    assert pulled.raised.item_id == "rep-x"
    assert [r.item_id for r in pulled.raised.refused] == ["rep-x"]
    assert reason in pulled.raised.refused[0].reason
    assert "No item definition was fetched or written" in str(pulled.raised)
    assert pulled.fetched == []
    # --into exists (pull creates it first) and is empty; nothing else changed.
    assert list(pulled.into.iterdir()) == []
    assert _tree(tmp_path) == sorted([*before, "into"])


def test_every_refused_item_is_named_in_one_error(tmp_path: Path) -> None:
    items = [
        _Item("rep-1", "Sales.v2", "Report"),
        _Item("rep-2", "Q1: Sales", "Report"),
        _Item("rep-3", "Plain", "Report"),
    ]
    pulled = _pull(tmp_path, items)

    assert isinstance(pulled.raised, PullItemNameRefusedError), repr(pulled.raised)
    assert [r.item_id for r in pulled.raised.refused] == ["rep-1", "rep-2"]
    message = str(pulled.raised)
    assert message.startswith("sync pull refused 2 workspace items:")
    assert "display_name='Sales.v2'" in message
    assert "display_name='Q1: Sales'" in message
    assert pulled.fetched == []
    assert list(pulled.into.iterdir()) == []


# -- round trip ----------------------------------------------------------------


def test_pulled_sync_yml_for_same_name_items_packs_for_apply(tmp_path: Path) -> None:
    """The emitted sync.yml loads and packs as ``sync apply`` packs it."""
    report = _Item("rep-1", "Sales", "Report", "f-gold")
    model = _Item("sm-1", "Sales", "SemanticModel", "f-gold")
    pulled = _pull(tmp_path, [report, model], folders=[("f-gold", "Gold", None)])
    assert pulled.raised is None, repr(pulled.raised)

    manifest = load_manifest(pulled.into / "sync.yml")
    staging = tmp_path / "staging"
    staging.mkdir()
    _pack_phase(manifest.normalised(), pulled.into, staging)

    for it in (report, model):
        pulled_dir = pulled.into / "Gold" / f"Sales.{it.type}"
        # The pulled files are each item's own, untouched by the other item.
        assert (pulled_dir / ".platform").read_text(encoding="utf-8") == _platform(it)
        staged = staging / "Gold" / f"Sales.{it.type}"
        assert json.loads((staged / ".platform").read_text(encoding="utf-8"))["metadata"] == {
            "type": it.type,
            "displayName": "Sales",
        }
        assert (staged / _CONTENT_FILE[it.type]).read_text(encoding="utf-8") == _content(it)
