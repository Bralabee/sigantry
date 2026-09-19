"""Tests for sigantry_core.deploy.notebook_binding + the CLI verb.

Covers the read-modify-write of a notebook's ``metadata.dependencies``
(environment / lakehouse), idempotency, additive merge, ``.platform``
preservation, and the argument-validation guards.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.deploy import cli as deploy_cli
from sigantry_core.deploy.cli import fabric_item_app
from sigantry_core.deploy.notebook_binding import (
    BindingResult,
    NotebookBindingError,
    set_notebook_binding,
)

runner = CliRunner()

ENV = "36c2632e-d419-427c-870c-ff6aff34cb42"
ENV_WS = "abc64232-25a2-499d-90ae-9fe5939ae437"
LH = "f1b9d1cb-b8cd-421a-ba5e-e17096fcd79d"


def _b64(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def _get_body(nb: dict | None = None) -> dict:
    nb = nb or {
        "cells": [{"cell_type": "code", "metadata": {}, "source": ["print(1)\n"]}],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    platform = {
        "metadata": {"type": "Notebook", "displayName": "X"},
        "config": {"version": "2.0", "logicalId": "11111111-1111-4111-8111-111111111111"},
    }
    return {
        "definition": {
            "parts": [
                {
                    "path": "notebook-content.ipynb",
                    "payload": _b64(nb),
                    "payloadType": "InlineBase64",
                },
                {"path": ".platform", "payload": _b64(platform), "payloadType": "InlineBase64"},
            ]
        }
    }


def _mock_client(get_body: dict) -> MagicMock:
    """A client whose send_lro returns get_body first, then None (update)."""
    client = MagicMock()
    client.send_lro.side_effect = [get_body, None]
    return client


def _update_definition(client: MagicMock) -> dict:
    """Extract the ``definition`` body sent to the 2nd send_lro (updateDefinition)."""
    update_call = client.send_lro.call_args_list[1]
    return update_call.kwargs["json"]["definition"]


def _decode_update_parts(client: MagicMock) -> list[dict]:
    """Extract the parts sent to the 2nd send_lro call (updateDefinition)."""
    return _update_definition(client)["parts"]


def _content_nb(parts: list[dict]) -> dict:
    part = next(p for p in parts if p["path"].endswith("notebook-content.ipynb"))
    return json.loads(base64.b64decode(part["payload"]))


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


def test_set_environment_injects_dependencies() -> None:
    client = _mock_client(_get_body())
    result = set_notebook_binding(
        client,
        workspace_id="ws",
        item_id="nb",
        environment_id=ENV,
        environment_workspace_id=ENV_WS,
    )
    assert result.changed is True
    assert result.environment == {"environmentId": ENV, "workspaceId": ENV_WS}
    # The updateDefinition body MUST carry format=ipynb (else Fabric fails the
    # async op with PyToIPynbFailure even though the POST returns 202).
    assert _update_definition(client)["format"] == "ipynb"
    nb = _content_nb(_decode_update_parts(client))
    assert nb["metadata"]["dependencies"]["environment"] == {
        "environmentId": ENV,
        "workspaceId": ENV_WS,
    }


def test_set_lakehouse_block() -> None:
    client = _mock_client(_get_body())
    result = set_notebook_binding(
        client,
        workspace_id="ws",
        item_id="nb",
        lakehouse_id=LH,
        lakehouse_name="my_lh",
        lakehouse_workspace_id="ws",
    )
    assert result.changed is True
    lh = _content_nb(_decode_update_parts(client))["metadata"]["dependencies"]["lakehouse"]
    assert lh["default_lakehouse"] == LH
    assert lh["default_lakehouse_name"] == "my_lh"
    assert lh["default_lakehouse_workspace_id"] == "ws"
    assert lh["known_lakehouses"] == [{"id": LH}]


def test_idempotent_no_change_skips_update() -> None:
    """Re-applying the same env binding issues NO updateDefinition."""
    nb = {
        "cells": [],
        "metadata": {
            "dependencies": {"environment": {"environmentId": ENV, "workspaceId": ENV_WS}}
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    client = MagicMock()
    client.send_lro.side_effect = [_get_body(nb)]  # only the GET should happen
    result = set_notebook_binding(
        client,
        workspace_id="ws",
        item_id="nb",
        environment_id=ENV,
        environment_workspace_id=ENV_WS,
    )
    assert result.changed is False
    assert client.send_lro.call_count == 1  # no updateDefinition


def test_additive_merge_preserves_other_binding() -> None:
    """Setting a lakehouse leaves an existing environment binding intact."""
    nb = {
        "cells": [],
        "metadata": {
            "dependencies": {"environment": {"environmentId": ENV, "workspaceId": ENV_WS}}
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    client = _mock_client(_get_body(nb))
    set_notebook_binding(
        client,
        workspace_id="ws",
        item_id="nb",
        lakehouse_id=LH,
        lakehouse_name="my_lh",
    )
    deps = _content_nb(_decode_update_parts(client))["metadata"]["dependencies"]
    assert deps["environment"] == {"environmentId": ENV, "workspaceId": ENV_WS}
    assert deps["lakehouse"]["default_lakehouse"] == LH


def test_preserves_platform_part_unchanged() -> None:
    body = _get_body()
    original_platform = next(p for p in body["definition"]["parts"] if p["path"] == ".platform")
    original_payload = original_platform["payload"]
    client = _mock_client(body)
    set_notebook_binding(
        client, workspace_id="ws", item_id="nb", environment_id=ENV, environment_workspace_id=ENV_WS
    )
    parts = _decode_update_parts(client)
    platform = next(p for p in parts if p["path"] == ".platform")
    assert platform["payload"] == original_payload  # logicalId untouched


def test_requires_both_environment_fields() -> None:
    client = MagicMock()
    with pytest.raises(NotebookBindingError, match="environment-workspace-id"):
        set_notebook_binding(client, workspace_id="ws", item_id="nb", environment_id=ENV)
    client.send_lro.assert_not_called()


def test_nothing_to_do_raises() -> None:
    client = MagicMock()
    with pytest.raises(NotebookBindingError, match="Nothing to do"):
        set_notebook_binding(client, workspace_id="ws", item_id="nb")
    client.send_lro.assert_not_called()


def test_non_notebook_missing_content_part_raises() -> None:
    body = {
        "definition": {
            "parts": [{"path": "report.json", "payload": _b64({}), "payloadType": "InlineBase64"}]
        }
    }
    client = MagicMock()
    client.send_lro.side_effect = [body]
    with pytest.raises(NotebookBindingError, match="is the item a Notebook"):
        set_notebook_binding(
            client,
            workspace_id="ws",
            item_id="nb",
            environment_id=ENV,
            environment_workspace_id=ENV_WS,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _DummyCtx:
    def __enter__(self):
        return MagicMock()

    def __exit__(self, *exc):
        return False


def test_cli_set_binding_forwards_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_bind(client, **kwargs):
        captured.update(kwargs)
        return BindingResult(
            changed=True,
            environment={"environmentId": ENV, "workspaceId": ENV_WS},
            lakehouse=None,
        )

    monkeypatch.setattr(deploy_cli, "_client", lambda tid: _DummyCtx())
    monkeypatch.setattr(deploy_cli, "set_notebook_binding", fake_bind)

    result = runner.invoke(
        fabric_item_app,
        [
            "set-binding",
            "--workspace-id",
            "ws",
            "--item-id",
            "nb",
            "--environment-id",
            ENV,
            "--environment-workspace-id",
            ENV_WS,
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["environment_id"] == ENV
    assert captured["environment_workspace_id"] == ENV_WS
    assert "bound" in result.output


def test_cli_set_binding_error_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_bind(client, **kwargs):
        raise NotebookBindingError("Nothing to do")

    monkeypatch.setattr(deploy_cli, "_client", lambda tid: _DummyCtx())
    monkeypatch.setattr(deploy_cli, "set_notebook_binding", fake_bind)

    result = runner.invoke(
        fabric_item_app, ["set-binding", "--workspace-id", "ws", "--item-id", "nb"]
    )
    assert result.exit_code != 0, result.output
    assert "set-binding failed" in result.output
