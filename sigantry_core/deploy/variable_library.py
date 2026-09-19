"""Fabric Variable Library CRUD + definition builder (DEPLOY-06).

Five REST endpoints (all /v1/workspaces/{ws}/variableLibraries scope):
  POST                          -> create   (201 sync OR 202 LRO; send_lro handles both)
  GET                           -> list     (paginated)
  GET    .../{vlId}             -> get      (200 sync)
  PATCH  .../{vlId}             -> update   (200 sync)
  DELETE .../{vlId}             -> delete   (200 sync; destructive)

Definition envelope (``VariableLibraryV1``) is a 4-part ``InlineBase64``
payload: ``variables.json`` + ``valueSets/<active>.json`` + ``settings.json``
+ ``.platform``. :func:`build_definition` constructs it with a single
active-value-set slot — consumer repos layer additional value sets via
their own build step (Phase 7 INTEG-01 onboarding).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sigantry_core.client import FabricRestClient
from sigantry_core.governance.audit import destructive_op


@dataclass(frozen=True, slots=True)
class Variable:
    """A single variable row in ``variables.json``.

    ``type`` is the Fabric Variable Library type tag — one of
    ``"String"``, ``"Int"``, ``"Bool"``, ``"Guid"``, ... (upstream set).
    """

    name: str
    type: str
    value: Any
    note: str | None = None


@dataclass(frozen=True, slots=True)
class VariableLibrary:
    """Frozen view of a Variable Library GET/PATCH/list response."""

    id: str
    workspace_id: str
    display_name: str
    description: str | None
    active_value_set: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> VariableLibrary:
        props = payload.get("properties") or {}
        return cls(
            id=payload["id"],
            workspace_id=payload["workspaceId"],
            display_name=payload["displayName"],
            description=payload.get("description"),
            active_value_set=props.get("activeValueSetName"),
        )


def _b64(obj: Any) -> str:
    """UTF-8 JSON -> base64 ASCII (Fabric InlineBase64 payload)."""
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def build_definition(
    variables: list[Variable],
    value_sets: dict[str, dict[str, Any]],
    *,
    active_value_set: str = "valueSet1",
    description: str | None = None,
    display_name: str = "VariableLibrary",
) -> dict[str, Any]:
    """Build the 4-part ``InlineBase64`` envelope for POST /variableLibraries.

    Output shape::

        {"format": "VariableLibraryV1",
         "parts": [
             {"path": "variables.json",              "payload": <b64>, "payloadType": "InlineBase64"},
             {"path": "valueSets/<active>.json",     "payload": <b64>, "payloadType": "InlineBase64"},
             {"path": "settings.json",               "payload": <b64>, "payloadType": "InlineBase64"},
             {"path": ".platform",                   "payload": <b64>, "payloadType": "InlineBase64"},
         ]}
    """
    variables_json = {
        "variables": [
            {"name": v.name, "type": v.type, "value": v.value, "note": v.note} for v in variables
        ]
    }
    settings_json = {"activeValueSetName": active_value_set}
    platform_json = {
        "version": "2.0",
        "$schema": (
            "https://developer.microsoft.com/json-schemas/fabric/platform/platformProperties.json"
        ),
        "config": {"logicalId": "00000000-0000-0000-0000-000000000000"},
        "metadata": {
            "type": "VariableLibrary",
            "displayName": display_name,
            "description": description or "",
        },
    }
    parts: list[tuple[str, Any]] = [
        ("variables.json", variables_json),
        (f"valueSets/{active_value_set}.json", value_sets.get(active_value_set, {})),
        ("settings.json", settings_json),
        (".platform", platform_json),
    ]
    return {
        "format": "VariableLibraryV1",
        "parts": [
            {"path": path, "payload": _b64(body), "payloadType": "InlineBase64"}
            for path, body in parts
        ],
    }


def create_variable_library(
    client: FabricRestClient,
    workspace_id: str,
    *,
    display_name: str,
    description: str | None = None,
    definition: dict[str, Any] | None = None,
) -> VariableLibrary:
    """POST /v1/workspaces/{ws}/variableLibraries - 201 sync OR 202 LRO.

    ``send_lro`` handles both shapes transparently (it returns the sync body
    on 2xx; on 202 it polls to terminal state and returns the operation
    result).
    """
    body: dict[str, Any] = {"displayName": display_name}
    if description is not None:
        body["description"] = description
    if definition is not None:
        body["definition"] = definition
    payload = client.send_lro(
        "POST",
        f"/v1/workspaces/{workspace_id}/variableLibraries",
        json=body,
    )
    return VariableLibrary.from_api(payload if isinstance(payload, dict) else {})


def list_variable_libraries(
    client: FabricRestClient, workspace_id: str
) -> Iterator[VariableLibrary]:
    """GET /v1/workspaces/{ws}/variableLibraries - paginated."""
    for payload in client.list_paginated(f"/v1/workspaces/{workspace_id}/variableLibraries"):
        yield VariableLibrary.from_api(payload)


def get_variable_library(
    client: FabricRestClient,
    workspace_id: str,
    variable_library_id: str,
) -> VariableLibrary:
    """GET .../variableLibraries/{vlId} - 200 sync."""
    resp = client.send(
        "GET",
        f"/v1/workspaces/{workspace_id}/variableLibraries/{variable_library_id}",
    )
    body = resp.json_body if isinstance(resp.json_body, dict) else {}
    return VariableLibrary.from_api(body)


def update_variable_library(
    client: FabricRestClient,
    workspace_id: str,
    variable_library_id: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
) -> VariableLibrary:
    """PATCH .../variableLibraries/{vlId} - 200 sync.

    Empty-body PATCH is a no-op on the Fabric side but still valid — we do
    not guard against ``display_name is None and description is None`` so
    callers can probe.
    """
    body: dict[str, Any] = {}
    if display_name is not None:
        body["displayName"] = display_name
    if description is not None:
        body["description"] = description
    resp = client.send(
        "PATCH",
        f"/v1/workspaces/{workspace_id}/variableLibraries/{variable_library_id}",
        json=body,
    )
    out_body = resp.json_body if isinstance(resp.json_body, dict) else {}
    return VariableLibrary.from_api(out_body)


@destructive_op(
    "variable_library",
    "delete",
    resource_arg="variable_library_id",
)
def delete_variable_library(
    client: FabricRestClient,
    workspace_id: str,
    variable_library_id: str,
    *,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
    resource_id: str | None = None,
    token_provider: Any | None = None,
) -> None:
    """DELETE .../variableLibraries/{vlId} - 200 sync. Destructive (decorated)."""
    client.send(
        "DELETE",
        f"/v1/workspaces/{workspace_id}/variableLibraries/{variable_library_id}",
    )
