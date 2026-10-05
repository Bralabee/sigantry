"""``sync pull`` refuses an item whose name cannot name a directory inside ``--into``.

The item's display name and the folder path it sits in come from the
workspace listing and name the directory its definition parts are written
to. A display name must be one name (no ``/`` or ``\\``, the rule a
``sync.yml`` is held to), and ``<into>/<folder path>/<display name>`` must
resolve strictly inside ``--into``. Every item is checked before the first
definition is fetched, so a refused workspace leaves ``--into`` empty: no
definition is requested, no file is written (for the refused item or for
any other), and no ``sync.yml`` is emitted.

The HTTP transport is faked with ``respx``; nothing here reaches a network.
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
from sigantry_core.sync.errors import PullItemNameRefusedError, SyncEngineError
from sigantry_core.sync.pull import pull_workspace

_WS = "ws-pull-names"
_NOTEBOOK = json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


def _client() -> FabricRestClient:
    tp = MagicMock(spec=TokenProvider)
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    tp.tenant_id = "test-tenant-id"
    return FabricRestClient(token_provider=tp)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _pull(
    tmp_path: Path, *, folder_name: str, second_name: str
) -> tuple[Path, list[respx.Route], Exception | None]:
    """Pull two notebooks: ``GoodNb`` at the root first, then ``second_name`` in a folder.

    Returns ``--into``, the two getDefinition routes and the raised error.
    """
    into = tmp_path / "into"
    items = [("nb-good", "GoodNb", None), ("nb-second", second_name, "f-1")]
    payload = base64.b64encode(_NOTEBOOK.encode("utf-8")).decode("ascii")
    with respx.mock(base_url=FABRIC_AUDIENCE, assert_all_called=False) as router:
        router.get(f"/v1/workspaces/{_WS}/folders").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "f-1",
                            "displayName": folder_name,
                            "parentFolderId": None,
                            "workspaceId": _WS,
                        }
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
                            "id": item_id,
                            "displayName": name,
                            "type": "Notebook",
                            "workspaceId": _WS,
                            "description": None,
                            "folderId": folder_id,
                        }
                        for item_id, name, folder_id in items
                    ]
                },
            )
        )
        routes = [
            router.post(f"/v1/workspaces/{_WS}/notebooks/{item_id}/getDefinition").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "definition": {
                            "parts": [
                                {
                                    "path": "notebook-content.ipynb",
                                    "payload": payload,
                                    "payloadType": "InlineBase64",
                                }
                            ]
                        }
                    },
                )
            )
            for item_id, _name, _folder_id in items
        ]
        raised: Exception | None = None
        with _client() as client:
            try:
                pull_workspace(_WS, into=into, client=client)
            except Exception as exc:
                raised = exc
    return into, routes, raised


@pytest.mark.parametrize(
    ("label", "folder_name", "second_name"),
    [
        ("absolute display name", "Orders", "<outside>/ESCAPED"),
        ("display name with parent segments", "Orders", "../../ESCAPED"),
        ("display name with backslashes", "Orders", "..\\..\\ESCAPED"),
        ("display name with a slash", "Orders", "a/b"),
        ("display name '..'", "Orders", ".."),
        ("folder named '..'", "..", "ESCAPED"),
    ],
)
def test_pull_refuses_an_item_whose_directory_is_not_inside_into(
    tmp_path: Path, label: str, folder_name: str, second_name: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    second_name = second_name.replace("<outside>", str(outside))
    before = _tree(tmp_path)

    into, routes, raised = _pull(tmp_path, folder_name=folder_name, second_name=second_name)

    assert isinstance(raised, PullItemNameRefusedError), repr(raised)
    assert isinstance(raised, SyncEngineError)  # the CLI maps it to exit 1
    assert raised.item_id == "nb-second"
    assert raised.display_name == second_name
    assert "nb-second" in str(raised)
    # No definition was requested, for the refused item or the good one.
    assert [route.called for route in routes] == [False, False]
    # Nothing was written anywhere: --into exists (pull creates it first)
    # and is empty, and nothing else under tmp_path changed.
    assert _tree(tmp_path) == sorted([*before, "into"])
    assert list(into.iterdir()) == []


def test_pull_writes_both_items_when_their_names_are_plain(tmp_path: Path) -> None:
    """Control: the same two-item workspace with plain names pulls both."""
    into, routes, raised = _pull(tmp_path, folder_name="Orders", second_name="SecondNb")

    assert raised is None
    assert [route.called for route in routes] == [True, True]
    assert (into / "GoodNb" / "notebook-content.ipynb").is_file()
    assert (into / "Orders" / "SecondNb" / "notebook-content.ipynb").is_file()
    manifest = yaml.safe_load((into / "sync.yml").read_text(encoding="utf-8"))
    assert sorted(i["display_name"] for i in manifest["items"]) == ["GoodNb", "SecondNb"]
