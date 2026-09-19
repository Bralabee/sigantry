"""Unit tests for FabricRestClient - Fabric Core REST API subclass.

Plan 02-03 Task 1. Covers:
- Default base_url (https://api.fabric.microsoft.com) and default_scope (FABRIC_SCOPE)
- Subclass relationship to BaseRestClient
- send() uses FABRIC_SCOPE and the Fabric base URL end-to-end
- from_defaults() wires the process-wide TokenProvider singleton
- from_defaults() threads tenant_id through to get_token_provider()
- Context-manager close semantics
- list_paginated() and send_lro() smoke tests (full matrices live in test_pagination.py / test_lro.py)
"""

from __future__ import annotations

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.client import FabricRestClient
from sigantry_core.client.base import BaseRestClient


class TestFabricRestClient:
    def test_defaults_base_url_and_scope(self, mock_token_provider):
        client = FabricRestClient(token_provider=mock_token_provider)
        assert client._base_url == FABRIC_AUDIENCE
        assert client._default_scope == FABRIC_SCOPE

    def test_is_subclass_of_base(self):
        assert issubclass(FabricRestClient, BaseRestClient)

    def test_send_uses_fabric_base_and_scope(self, mock_token_provider, respx_router):
        route = respx_router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").respond(
            status_code=200,
            json={"value": []},
            headers={"x-ms-request-id": "r1"},
        )
        client = FabricRestClient(token_provider=mock_token_provider)
        resp = client.send("GET", "/v1/workspaces")
        assert resp.status_code == 200
        assert resp.request_id == "r1"
        mock_token_provider.get_token.assert_called_once_with(FABRIC_SCOPE)
        assert route.called

    def test_from_defaults_uses_singleton(self, monkeypatch, mock_token_provider):
        """from_defaults() constructs via get_token_provider() — the Phase 1 singleton."""
        monkeypatch.setattr(
            "sigantry_core.client.fabric.get_token_provider",
            lambda tenant_id=None: mock_token_provider,
        )
        client = FabricRestClient.from_defaults()
        assert client._tp is mock_token_provider
        assert client._base_url == FABRIC_AUDIENCE
        assert client._default_scope == FABRIC_SCOPE

    def test_from_defaults_threads_tenant_id(self, monkeypatch, mock_token_provider):
        captured: dict[str, str | None] = {"tenant": "UNSET"}

        def fake_get_tp(tenant_id=None):
            captured["tenant"] = tenant_id
            return mock_token_provider

        monkeypatch.setattr("sigantry_core.client.fabric.get_token_provider", fake_get_tp)
        FabricRestClient.from_defaults(tenant_id="t-xyz")
        assert captured["tenant"] == "t-xyz"

    def test_from_defaults_accepts_base_url_override(self, monkeypatch, mock_token_provider):
        monkeypatch.setattr(
            "sigantry_core.client.fabric.get_token_provider",
            lambda tenant_id=None: mock_token_provider,
        )
        client = FabricRestClient.from_defaults(base_url="https://api.fabric.microsoft.us")
        assert client._base_url == "https://api.fabric.microsoft.us"
        # Scope remains the pinned constant.
        assert client._default_scope == FABRIC_SCOPE

    def test_context_manager_returns_self(self, mock_token_provider):
        with FabricRestClient(token_provider=mock_token_provider) as client:
            assert isinstance(client, FabricRestClient)
            assert isinstance(client, BaseRestClient)

    def test_list_paginated_smoke(self, mock_token_provider, respx_router):
        respx_router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").respond(
            status_code=200,
            json={"value": [{"id": 1}]},
            headers={"x-ms-request-id": "r1"},
        )
        client = FabricRestClient(token_provider=mock_token_provider)
        items = list(client.list_paginated("/v1/workspaces"))
        assert items == [{"id": 1}]

    def test_send_lro_inline_200(self, mock_token_provider, respx_router):
        """200 from the initial call bypasses LRO polling and returns the body."""
        respx_router.post(f"{FABRIC_AUDIENCE}/v1/workspaces").respond(
            status_code=200,
            json={"id": "ws-1"},
        )
        client = FabricRestClient(token_provider=mock_token_provider)
        result = client.send_lro("POST", "/v1/workspaces", json={"displayName": "x"})
        assert result == {"id": "ws-1"}

    def test_http_client_user_agent_is_toolkit_branded(self, mock_token_provider, respx_router):
        captured: dict[str, str] = {}

        def _capture(request):
            captured["ua"] = request.headers.get("user-agent", "")
            import httpx

            return httpx.Response(200, json={"value": []})

        respx_router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(side_effect=_capture)

        client = FabricRestClient(token_provider=mock_token_provider)
        client.send("GET", "/v1/workspaces")
        assert captured["ua"].startswith("fabric-dataops/")
