"""Unit tests for sigantry_core.deploy.variable_library (DEPLOY-06).

Covers the 4-part InlineBase64 ``VariableLibraryV1`` envelope, CRUD
wrappers, the ``@destructive_op`` gate on delete, and the DTO
from_api conversions.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from sigantry_core.client import FabricRestClient, HttpResponse
from sigantry_core.deploy.variable_library import (
    Variable,
    VariableLibrary,
    build_definition,
    create_variable_library,
    delete_variable_library,
    get_variable_library,
    list_variable_libraries,
    update_variable_library,
)
from sigantry_core.governance.audit import DestructiveOpError


def _mock_client(*, send_body: dict | None = None, lro_result: dict | None = None) -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.send.return_value = HttpResponse(
        status_code=200,
        json_body=send_body or {},
        headers={},
        request_id="req",
        operation_id=None,
        elapsed_ms=1.0,
    )
    c.send_lro.return_value = lro_result
    return c


class TestBuildDefinition:
    def test_envelope_shape_default_value_set(self) -> None:
        d = build_definition(
            [Variable("a", "String", "1")],
            value_sets={"valueSet1": {"a": "1"}},
        )
        assert d["format"] == "VariableLibraryV1"
        paths = [p["path"] for p in d["parts"]]
        assert paths == [
            "variables.json",
            "valueSets/valueSet1.json",
            "settings.json",
            ".platform",
        ]
        for part in d["parts"]:
            assert part["payloadType"] == "InlineBase64"

    def test_active_value_set_flows_to_parts_and_settings(self) -> None:
        d = build_definition(
            [Variable("a", "String", "1")],
            value_sets={"prod": {"a": "prod-val"}, "dev": {"a": "dev-val"}},
            active_value_set="prod",
        )
        paths = [p["path"] for p in d["parts"]]
        assert paths[1] == "valueSets/prod.json"
        settings_part = d["parts"][2]
        decoded = json.loads(base64.b64decode(settings_part["payload"]).decode())
        assert decoded["activeValueSetName"] == "prod"

    def test_base64_payload_round_trips(self) -> None:
        vars_ = [Variable("env", "String", "prod"), Variable("n", "Int", 42)]
        d = build_definition(vars_, value_sets={"valueSet1": {"env": "prod"}})
        variables_part = d["parts"][0]
        decoded = json.loads(base64.b64decode(variables_part["payload"]).decode())
        assert decoded == {
            "variables": [
                {"name": "env", "type": "String", "value": "prod", "note": None},
                {"name": "n", "type": "Int", "value": 42, "note": None},
            ]
        }

    def test_note_round_trips(self) -> None:
        d = build_definition(
            [Variable("retain_days", "Int", 30, note="retention")],
            value_sets={"valueSet1": {"retain_days": 30}},
        )
        decoded = json.loads(base64.b64decode(d["parts"][0]["payload"]).decode())
        assert decoded["variables"][0]["note"] == "retention"

    def test_platform_metadata_carries_display_name_and_description(self) -> None:
        d = build_definition(
            [],
            value_sets={"valueSet1": {}},
            display_name="ExampleConfig",
            description="Example runtime config",
        )
        platform_part = d["parts"][3]
        decoded = json.loads(base64.b64decode(platform_part["payload"]).decode())
        assert decoded["metadata"]["type"] == "VariableLibrary"
        assert decoded["metadata"]["displayName"] == "ExampleConfig"
        assert decoded["metadata"]["description"] == "Example runtime config"


class TestDTO:
    def test_from_api_minimum(self) -> None:
        vl = VariableLibrary.from_api({"id": "vl-1", "workspaceId": "ws-1", "displayName": "x"})
        assert vl.id == "vl-1"
        assert vl.workspace_id == "ws-1"
        assert vl.display_name == "x"
        assert vl.description is None
        assert vl.active_value_set is None

    def test_from_api_with_properties(self) -> None:
        vl = VariableLibrary.from_api(
            {
                "id": "vl-2",
                "workspaceId": "ws-2",
                "displayName": "y",
                "description": "hi",
                "properties": {"activeValueSetName": "prod"},
            }
        )
        assert vl.description == "hi"
        assert vl.active_value_set == "prod"


class TestCRUD:
    def test_create_uses_send_lro(self) -> None:
        c = _mock_client(
            lro_result={
                "id": "vl-1",
                "workspaceId": "ws-1",
                "displayName": "dn",
            }
        )
        vl = create_variable_library(c, "ws-1", display_name="dn", description="hi")
        c.send_lro.assert_called_once()
        args, kwargs = c.send_lro.call_args
        assert args[:2] == ("POST", "/v1/workspaces/ws-1/variableLibraries")
        body = kwargs["json"]
        assert body["displayName"] == "dn"
        assert body["description"] == "hi"
        assert isinstance(vl, VariableLibrary)
        assert vl.id == "vl-1"

    def test_create_with_definition(self) -> None:
        c = _mock_client(
            lro_result={
                "id": "vl-1",
                "workspaceId": "ws-1",
                "displayName": "dn",
            }
        )
        defn = build_definition(
            [Variable("a", "String", "1")],
            value_sets={"valueSet1": {"a": "1"}},
        )
        create_variable_library(c, "ws-1", display_name="dn", definition=defn)
        body = c.send_lro.call_args.kwargs["json"]
        assert "definition" in body
        assert body["definition"]["format"] == "VariableLibraryV1"

    def test_list_yields_dtos(self) -> None:
        c = _mock_client()
        c.list_paginated.return_value = iter(
            [
                {"id": "vl-1", "workspaceId": "ws-1", "displayName": "a"},
                {"id": "vl-2", "workspaceId": "ws-1", "displayName": "b"},
            ]
        )
        out = list(list_variable_libraries(c, "ws-1"))
        assert len(out) == 2
        assert all(isinstance(v, VariableLibrary) for v in out)
        assert [v.id for v in out] == ["vl-1", "vl-2"]

    def test_get(self) -> None:
        c = _mock_client(send_body={"id": "vl-9", "workspaceId": "ws-1", "displayName": "n"})
        vl = get_variable_library(c, "ws-1", "vl-9")
        c.send.assert_called_once_with("GET", "/v1/workspaces/ws-1/variableLibraries/vl-9")
        assert vl.id == "vl-9"

    def test_update_sends_patch(self) -> None:
        c = _mock_client(send_body={"id": "vl-1", "workspaceId": "ws-1", "displayName": "new"})
        vl = update_variable_library(c, "ws-1", "vl-1", display_name="new")
        args, kwargs = c.send.call_args
        assert args[:2] == ("PATCH", "/v1/workspaces/ws-1/variableLibraries/vl-1")
        assert kwargs["json"] == {"displayName": "new"}
        assert vl.display_name == "new"


class TestDeleteGate:
    def test_delete_requires_force(self) -> None:
        c = _mock_client()
        with pytest.raises(DestructiveOpError, match="force=True"):
            delete_variable_library(c, "ws-1", "vl-1")  # type: ignore[call-arg]
        c.send.assert_not_called()

    def test_delete_with_force_hits_endpoint(self) -> None:
        c = _mock_client()
        delete_variable_library(c, "ws-1", "vl-1", force=True, resource_id="vl-1")
        c.send.assert_called_once_with("DELETE", "/v1/workspaces/ws-1/variableLibraries/vl-1")
