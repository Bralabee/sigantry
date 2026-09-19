"""Unit tests for sigantry_core.workspace.core (WKSP-01)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core.client import HttpResponse
from sigantry_core.governance import DestructiveOpError
from sigantry_core.workspace import (
    Workspace,
    create_workspace,
    delete_workspace,
    get_workspace,
    list_workspaces,
    update_workspace,
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


def test_workspace_from_api_full() -> None:
    ws = Workspace.from_api(
        {
            "id": "w1",
            "displayName": "ws-dev",
            "description": "the dev workspace",
            "type": "Workspace",
            "capacityId": "cap-1",
            "domainId": "dom-1",
        }
    )
    assert ws.id == "w1"
    assert ws.display_name == "ws-dev"
    assert ws.description == "the dev workspace"
    assert ws.type == "Workspace"
    assert ws.capacity_id == "cap-1"
    assert ws.domain_id == "dom-1"


def test_workspace_from_api_minimal() -> None:
    ws = Workspace.from_api({"id": "w1", "displayName": "ws-dev", "type": "Workspace"})
    assert ws.id == "w1"
    assert ws.description is None
    assert ws.capacity_id is None
    assert ws.domain_id is None


def test_create_workspace_basic(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.send.return_value = _resp(
        {"id": "w1", "displayName": "ws-dev", "type": "Workspace"}, status=201
    )
    ws = create_workspace(mock_fabric_client, display_name="ws-dev")
    mock_fabric_client.send.assert_called_once_with(
        "POST", "/v1/workspaces", json={"displayName": "ws-dev"}
    )
    assert ws.id == "w1"
    assert ws.display_name == "ws-dev"


def test_create_workspace_with_all_fields(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.send.return_value = _resp(
        {
            "id": "w1",
            "displayName": "ws-dev",
            "type": "Workspace",
            "capacityId": "cap-1",
            "description": "desc",
            "domainId": "dom-1",
        },
        status=201,
    )
    create_workspace(
        mock_fabric_client,
        display_name="ws-dev",
        capacity_id="cap-1",
        description="desc",
        domain_id="dom-1",
    )
    sent_json = mock_fabric_client.send.call_args.kwargs["json"]
    assert sent_json == {
        "displayName": "ws-dev",
        "capacityId": "cap-1",
        "description": "desc",
        "domainId": "dom-1",
    }


def test_create_is_sync_not_lro(mock_fabric_client: MagicMock) -> None:
    """Pitfall 1: create is 201 synchronous. Must not call send_lro."""
    mock_fabric_client.send.return_value = _resp(
        {"id": "w1", "displayName": "ws-dev", "type": "Workspace"}, status=201
    )
    create_workspace(mock_fabric_client, display_name="ws-dev")
    mock_fabric_client.send_lro.assert_not_called()


def test_get_workspace(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.send.return_value = _resp(
        {"id": "w1", "displayName": "ws-dev", "type": "Workspace"}
    )
    ws = get_workspace(mock_fabric_client, "w1")
    mock_fabric_client.send.assert_called_once_with("GET", "/v1/workspaces/w1")
    assert ws.id == "w1"


def test_update_workspace_with_fields(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.send.return_value = _resp(
        {"id": "w1", "displayName": "new", "type": "Workspace"}
    )
    update_workspace(mock_fabric_client, "w1", display_name="new")
    mock_fabric_client.send.assert_called_once_with(
        "PATCH", "/v1/workspaces/w1", json={"displayName": "new"}
    )


def test_update_workspace_empty_body(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.send.return_value = _resp(
        {"id": "w1", "displayName": "ws-dev", "type": "Workspace"}
    )
    update_workspace(mock_fabric_client, "w1")
    mock_fabric_client.send.assert_called_once_with("PATCH", "/v1/workspaces/w1", json={})


def test_delete_requires_force(mock_fabric_client: MagicMock) -> None:
    with pytest.raises(DestructiveOpError, match="force=True"):
        delete_workspace(mock_fabric_client, "w1")
    mock_fabric_client.send.assert_not_called()


def test_delete_with_force_hits_api(mock_fabric_client: MagicMock) -> None:
    result = delete_workspace(mock_fabric_client, "w1", force=True, resource_id="w1")
    assert result is None
    mock_fabric_client.send.assert_called_once_with("DELETE", "/v1/workspaces/w1")
    # Not an LRO
    mock_fabric_client.send_lro.assert_not_called()


class TestDeleteWorkspacePbiFallback:
    """Gotcha #6 — Fabric DELETE intermittently returns UnknownError; PBI
    fallback (DELETE /v1.0/myorg/groups/{id}) is more reliable. Pattern lifted
    from usf_fabric_cli_cicd v1.7.16. See ``docs/RELATED-WORK.md``.
    """

    def test_pbi_fallback_off_by_default_propagates_error(
        self, mock_fabric_client: MagicMock
    ) -> None:
        """Without ``pbi_fallback=True`` the original HttpError must surface."""
        from sigantry_core.client.errors import HttpError

        mock_fabric_client.send.side_effect = HttpError(
            status_code=400,
            body={"errorCode": "UnknownError", "message": "transient"},
            request_id="req-x",
        )
        with pytest.raises(HttpError):
            delete_workspace(mock_fabric_client, "w1", force=True, resource_id="w1")

    def test_pbi_fallback_only_on_unknown_error(self, mock_fabric_client: MagicMock) -> None:
        """A different errorCode must NOT trigger fallback, even with flag on."""
        from sigantry_core.client.errors import HttpError

        mock_fabric_client.send.side_effect = HttpError(
            status_code=403,
            body={"errorCode": "InsufficientPermissions", "message": "no"},
            request_id="req-y",
        )
        with pytest.raises(HttpError):
            delete_workspace(
                mock_fabric_client,
                "w1",
                force=True,
                resource_id="w1",
                pbi_fallback=True,
            )

    def test_pbi_fallback_invokes_powerbi_delete_on_unknown_error(
        self, mock_fabric_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On UnknownError + pbi_fallback=True, fall back to PBI groups DELETE."""
        from sigantry_core.client.errors import HttpError

        mock_fabric_client.send.side_effect = HttpError(
            status_code=400,
            body={"errorCode": "UnknownError", "message": "transient"},
            request_id="req-z",
        )

        # Build a context-manager-shaped mock for PowerBIRestClient.from_defaults.
        pbi_instance = MagicMock()
        pbi_cm = MagicMock()
        pbi_cm.__enter__ = MagicMock(return_value=pbi_instance)
        pbi_cm.__exit__ = MagicMock(return_value=False)

        from sigantry_core.client import powerbi as powerbi_mod

        monkeypatch.setattr(
            powerbi_mod.PowerBIRestClient,
            "from_defaults",
            classmethod(lambda cls, **kw: pbi_cm),
        )

        delete_workspace(
            mock_fabric_client,
            "w1",
            force=True,
            resource_id="w1",
            pbi_fallback=True,
            tenant_id="tenant-abc",
        )
        # PBI fallback was hit
        pbi_instance.send.assert_called_once_with("DELETE", "/v1.0/myorg/groups/w1")

    def test_unknown_error_detected_in_nested_error_envelope(
        self, mock_fabric_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Some Fabric responses nest the code under ``error.code``."""
        from sigantry_core.client.errors import HttpError

        mock_fabric_client.send.side_effect = HttpError(
            status_code=400,
            body={"error": {"code": "UnknownError", "message": "transient"}},
            request_id="req-n",
        )
        pbi_instance = MagicMock()
        pbi_cm = MagicMock()
        pbi_cm.__enter__ = MagicMock(return_value=pbi_instance)
        pbi_cm.__exit__ = MagicMock(return_value=False)
        from sigantry_core.client import powerbi as powerbi_mod

        monkeypatch.setattr(
            powerbi_mod.PowerBIRestClient,
            "from_defaults",
            classmethod(lambda cls, **kw: pbi_cm),
        )

        delete_workspace(
            mock_fabric_client,
            "w1",
            force=True,
            resource_id="w1",
            pbi_fallback=True,
        )
        pbi_instance.send.assert_called_once_with("DELETE", "/v1.0/myorg/groups/w1")


def test_list_workspaces_paginated(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.list_paginated.return_value = iter(
        [
            {"id": "w1", "displayName": "a", "type": "Workspace"},
            {"id": "w2", "displayName": "b", "type": "Workspace"},
        ]
    )
    results = list(list_workspaces(mock_fabric_client))
    mock_fabric_client.list_paginated.assert_called_once_with("/v1/workspaces", params=None)
    assert [w.id for w in results] == ["w1", "w2"]


def test_list_workspaces_roles_filter(mock_fabric_client: MagicMock) -> None:
    mock_fabric_client.list_paginated.return_value = iter([])
    list(list_workspaces(mock_fabric_client, roles="Admin"))
    mock_fabric_client.list_paginated.assert_called_once_with(
        "/v1/workspaces", params={"roles": "Admin"}
    )
