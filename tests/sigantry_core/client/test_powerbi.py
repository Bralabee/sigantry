"""Unit tests for PowerBIRestClient - Power BI REST API subclass.

Plan 02-03 Task 1. Covers:
- Default base_url (https://api.powerbi.com) and default_scope (POWERBI_SCOPE)
- Subclass relationship to BaseRestClient
- @odata.nextLink pagination end-to-end via list_paginated()
- from_defaults() wires the process-wide TokenProvider singleton
"""

from __future__ import annotations

import json
from pathlib import Path

from sigantry_core.auth.audiences import POWERBI_SCOPE
from sigantry_core.client import PowerBIRestClient
from sigantry_core.client.base import BaseRestClient
from sigantry_core.client.powerbi import POWERBI_DEFAULT_BASE_URL

FIXTURES = Path(__file__).parent / "fixtures"


class TestPowerBIRestClient:
    def test_defaults_base_url_and_scope(self, mock_token_provider):
        client = PowerBIRestClient(token_provider=mock_token_provider)
        assert client._base_url == POWERBI_DEFAULT_BASE_URL
        assert client._default_scope == POWERBI_SCOPE

    def test_is_subclass_of_base(self):
        assert issubclass(PowerBIRestClient, BaseRestClient)

    def test_send_uses_powerbi_scope(self, mock_token_provider, respx_router):
        respx_router.get(f"{POWERBI_DEFAULT_BASE_URL}/v1.0/myorg/groups").respond(
            status_code=200,
            json={"value": []},
        )
        client = PowerBIRestClient(token_provider=mock_token_provider)
        resp = client.send("GET", "/v1.0/myorg/groups")
        assert resp.status_code == 200
        mock_token_provider.get_token.assert_called_once_with(POWERBI_SCOPE)

    def test_odata_nextlink_pagination(self, mock_token_provider, respx_router):
        """End-to-end: @odata.nextLink chain goes through list_paginated()."""
        pages = json.loads((FIXTURES / "paginated_odata_nextlink.json").read_text(encoding="utf-8"))
        # respx matches on path only - use side_effect for sequenced pages
        # (Plan 02-02 Decision #1).
        import httpx

        route = respx_router.get(f"{POWERBI_DEFAULT_BASE_URL}/v1.0/myorg/groups")
        route.side_effect = [
            httpx.Response(200, json=pages[0]),
            httpx.Response(200, json=pages[1]),
        ]

        client = PowerBIRestClient(token_provider=mock_token_provider)
        items = list(client.list_paginated("/v1.0/myorg/groups"))
        assert [i["id"] for i in items] == ["g1", "g2", "g3"]

    def test_from_defaults_uses_singleton(self, monkeypatch, mock_token_provider):
        monkeypatch.setattr(
            "sigantry_core.client.powerbi.get_token_provider",
            lambda tenant_id=None: mock_token_provider,
        )
        client = PowerBIRestClient.from_defaults()
        assert client._tp is mock_token_provider
        assert client._default_scope == POWERBI_SCOPE
        assert client._base_url == POWERBI_DEFAULT_BASE_URL

    def test_from_defaults_threads_tenant_id(self, monkeypatch, mock_token_provider):
        captured: dict[str, str | None] = {"tenant": "UNSET"}

        def fake_get_tp(tenant_id=None):
            captured["tenant"] = tenant_id
            return mock_token_provider

        monkeypatch.setattr("sigantry_core.client.powerbi.get_token_provider", fake_get_tp)
        PowerBIRestClient.from_defaults(tenant_id="t-pbi")
        assert captured["tenant"] == "t-pbi"
