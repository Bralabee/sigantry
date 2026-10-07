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


def _patch_powerbi_client(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, dict]:
    """Replace ``PowerBIRestClient`` (the class the fallback builds) with a
    factory that records its keyword arguments and yields a mock client."""
    from sigantry_core.client import powerbi as powerbi_mod

    pbi_instance = MagicMock()
    pbi_cm = MagicMock()
    pbi_cm.__enter__ = MagicMock(return_value=pbi_instance)
    pbi_cm.__exit__ = MagicMock(return_value=False)
    seen: dict = {}

    def fake_client(**kw: object) -> MagicMock:
        seen.update(kw)
        return pbi_cm

    monkeypatch.setattr(powerbi_mod, "PowerBIRestClient", fake_client)
    return pbi_instance, seen


class TestDeleteWorkspacePbiFallback:
    """Fabric DELETE can fail with UnknownError; with pbi_fallback=True the
    delete is tried again through the Power BI groups endpoint
    (DELETE /v1.0/myorg/groups/{id}).
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
        pbi_instance, _ = _patch_powerbi_client(monkeypatch)

        delete_workspace(
            mock_fabric_client,
            "w1",
            force=True,
            resource_id="w1",
            pbi_fallback=True,
        )
        # PBI fallback was hit
        pbi_instance.send.assert_called_once_with("DELETE", "/v1.0/myorg/groups/w1")

    def test_pbi_fallback_ignores_status_code_and_uses_the_callers_provider(
        self,
        mock_fabric_client: MagicMock,
        mock_token_provider: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The docstring says the status code is not checked: a 5xx carrying
        UnknownError triggers the fallback too. The Power BI client is built on
        the Fabric client's token provider (issue #62), and a ``tenant_id``
        that names the provider's pin, in any case, leaves it as it is.
        """
        from sigantry_core.client.errors import ServerError

        mock_fabric_client.send.side_effect = ServerError(
            status_code=500,
            body={"errorCode": "UnknownError", "message": "server"},
            request_id="req-5",
        )
        mock_fabric_client.token_provider = mock_token_provider
        pbi_instance, seen = _patch_powerbi_client(monkeypatch)

        delete_workspace(
            mock_fabric_client,
            "w1",
            force=True,
            resource_id="w1",
            pbi_fallback=True,
            tenant_id=mock_token_provider.tenant_id.upper(),
        )
        pbi_instance.send.assert_called_once_with("DELETE", "/v1.0/myorg/groups/w1")
        assert seen == {"token_provider": mock_token_provider}

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
        pbi_instance, _ = _patch_powerbi_client(monkeypatch)

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


# ---- the Power BI retry authenticates as the caller -------------------------

_TENANT_A = "00000000-0000-0000-0000-00000000000a"
_TENANT_B = "00000000-0000-0000-0000-00000000000b"
_WS = "00000000-0000-0000-0000-0000000000e1"
_POWERBI_DELETE = f"https://api.powerbi.com/v1.0/myorg/groups/{_WS}"


def _fake_jwt(tid: str, marker: str) -> str:
    import base64
    import json

    def b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = b64url(json.dumps({"typ": "JWT", "alg": "none"}).encode())
    payload = b64url(json.dumps({"tid": tid, "marker": marker}).encode())
    return f"{header}.{payload}.sig"


class _RecordingCredential:
    """Issues fake JWTs for ``tid`` (tagged ``marker``) and records each request."""

    def __init__(self, tid: str, marker: str) -> None:
        self.tid = tid
        self.marker = marker
        self.requests: list[tuple[str, str | None]] = []

    def get_token(self, *scopes: str, tenant_id: str | None = None, **_: object):
        import time

        from azure.core.credentials import AccessToken

        self.requests.append((" ".join(scopes), tenant_id))
        return AccessToken(_fake_jwt(self.tid, self.marker), int(time.time()) + 3600)


class _FallbackHarness:
    """A Fabric DELETE that fails with UnknownError and a Power BI DELETE that
    succeeds, over a patched ``httpx.Client.send``; the process default chain
    is replaced by a recording fake so a request through it shows up."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        self.sent: list[tuple[str, str, str]] = []
        self.default_chain: list[_RecordingCredential] = []

        def build_default(**_: object) -> _RecordingCredential:
            cred = _RecordingCredential(_TENANT_B, "default-chain")
            self.default_chain.append(cred)
            return cred

        def fake_send(client: httpx.Client, request: httpx.Request, **_: object) -> httpx.Response:
            url = str(request.url)
            self.sent.append((request.method, url, request.headers.get("Authorization", "")))
            if request.method == "DELETE" and url.endswith(f"/v1/workspaces/{_WS}"):
                body = {"errorCode": "UnknownError", "message": "transient"}
                return httpx.Response(400, json=body, request=request)
            if request.method == "DELETE" and url == _POWERBI_DELETE:
                return httpx.Response(200, request=request)
            raise AssertionError(f"unexpected request {request.method} {url}")

        monkeypatch.setattr(
            "sigantry_core.auth.token_provider.DefaultAzureCredential", build_default
        )
        monkeypatch.setattr(httpx.Client, "send", fake_send)

    def powerbi_auth(self) -> list[str]:
        return [auth for method, url, auth in self.sent if url == _POWERBI_DELETE]


@pytest.fixture
def fallback(monkeypatch: pytest.MonkeyPatch):
    from sigantry_core.auth.token_provider import reset_token_provider

    reset_token_provider()
    yield _FallbackHarness(monkeypatch)
    reset_token_provider()


class TestPbiFallbackAuthenticatesAsTheCaller:
    """Issue #62, credential part: the Power BI retry of a failed workspace
    delete must use the caller's token provider (the ``token_provider``
    argument, else the Fabric client's own), so it runs as the same
    principal under the same tenant pin, never the process default chain.
    """

    def test_retry_uses_the_fabric_clients_provider(self, fallback: _FallbackHarness) -> None:
        from sigantry_core.auth import POWERBI_SCOPE, TokenProvider
        from sigantry_core.client import FabricRestClient

        caller = _RecordingCredential(_TENANT_A, "caller")
        tp = TokenProvider(credential=caller, tenant_id=_TENANT_A)
        with FabricRestClient(token_provider=tp) as client:
            delete_workspace(client, _WS, force=True, pbi_fallback=True)

        assert fallback.powerbi_auth() == [f"Bearer {_fake_jwt(_TENANT_A, 'caller')}"]
        assert (POWERBI_SCOPE, _TENANT_A) in caller.requests, caller.requests
        assert fallback.default_chain == []

    def test_retry_uses_the_token_provider_argument(self, fallback: _FallbackHarness) -> None:
        from sigantry_core.auth import POWERBI_SCOPE, TokenProvider
        from sigantry_core.client import FabricRestClient

        client_cred = _RecordingCredential(_TENANT_A, "client")
        given_cred = _RecordingCredential(_TENANT_A, "given")
        given = TokenProvider(credential=given_cred, tenant_id=_TENANT_A)
        with FabricRestClient(token_provider=TokenProvider(credential=client_cred)) as client:
            delete_workspace(client, _WS, force=True, pbi_fallback=True, token_provider=given)

        assert fallback.powerbi_auth() == [f"Bearer {_fake_jwt(_TENANT_A, 'given')}"]
        assert given_cred.requests == [(POWERBI_SCOPE, _TENANT_A)]
        assert fallback.default_chain == []

    def test_tenant_id_pins_an_unpinned_callers_credential(
        self, fallback: _FallbackHarness
    ) -> None:
        from sigantry_core.auth import POWERBI_SCOPE, TokenProvider
        from sigantry_core.client import FabricRestClient

        caller = _RecordingCredential(_TENANT_A, "caller")
        with FabricRestClient(token_provider=TokenProvider(credential=caller)) as client:
            delete_workspace(client, _WS, force=True, pbi_fallback=True, tenant_id=_TENANT_A)

        assert fallback.powerbi_auth() == [f"Bearer {_fake_jwt(_TENANT_A, 'caller')}"]
        assert (POWERBI_SCOPE, _TENANT_A) in caller.requests, caller.requests
        assert fallback.default_chain == []

    def test_tenant_id_other_than_the_providers_pin_is_refused_before_the_retry(
        self, fallback: _FallbackHarness
    ) -> None:
        from sigantry_core.auth import TenantMismatchError, TokenProvider
        from sigantry_core.client import FabricRestClient

        caller = _RecordingCredential(_TENANT_A, "caller")
        tp = TokenProvider(credential=caller, tenant_id=_TENANT_A)
        with (
            FabricRestClient(token_provider=tp) as client,
            pytest.raises(TenantMismatchError) as excinfo,
        ):
            delete_workspace(client, _WS, force=True, pbi_fallback=True, tenant_id=_TENANT_B)

        assert _TENANT_A in str(excinfo.value) and _TENANT_B in str(excinfo.value)
        assert fallback.powerbi_auth() == []
        assert fallback.default_chain == []
