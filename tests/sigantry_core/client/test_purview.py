"""Unit tests for PurviewRestClient - stub subclass for v2.

Plan 02-03 Task 1. Covers:
- Subclass relationship to BaseRestClient
- Requires explicit base_url (no canonical default — Purview is account-scoped)
- default_scope is PURVIEW_SCOPE
- from_defaults() requires base_url (keyword-only, no default)
- No additional public methods beyond BaseRestClient + from_defaults
"""

from __future__ import annotations

import pytest

from sigantry_core.auth.audiences import PURVIEW_SCOPE
from sigantry_core.client import PurviewRestClient
from sigantry_core.client.base import BaseRestClient


class TestPurviewRestClient:
    def test_is_subclass_of_base(self):
        assert issubclass(PurviewRestClient, BaseRestClient)

    def test_empty_base_url_raises_value_error(self, mock_token_provider):
        with pytest.raises(ValueError) as exc_info:
            PurviewRestClient(token_provider=mock_token_provider, base_url="")
        msg = str(exc_info.value).lower()
        assert "account" in msg or "purview" in msg

    def test_whitespace_base_url_raises_value_error(self, mock_token_provider):
        with pytest.raises(ValueError):
            PurviewRestClient(token_provider=mock_token_provider, base_url="   ")

    def test_default_scope_is_purview_scope(self, mock_token_provider):
        client = PurviewRestClient(
            token_provider=mock_token_provider,
            base_url="https://contoso.purview.azure.net",
        )
        assert client._default_scope == PURVIEW_SCOPE
        assert client._base_url == "https://contoso.purview.azure.net"

    def test_from_defaults_requires_base_url(self):
        """from_defaults is keyword-only; calling without base_url raises TypeError."""
        with pytest.raises(TypeError):
            PurviewRestClient.from_defaults()  # type: ignore[call-arg]

    def test_from_defaults_with_base_url(self, monkeypatch, mock_token_provider):
        monkeypatch.setattr(
            "sigantry_core.client.purview.get_token_provider",
            lambda tenant_id=None: mock_token_provider,
        )
        client = PurviewRestClient.from_defaults(base_url="https://contoso.purview.azure.net")
        assert client._tp is mock_token_provider
        assert client._base_url == "https://contoso.purview.azure.net"
        assert client._default_scope == PURVIEW_SCOPE

    def test_from_defaults_threads_tenant_id(self, monkeypatch, mock_token_provider):
        captured: dict[str, str | None] = {"tenant": "UNSET"}

        def fake_get_tp(tenant_id=None):
            captured["tenant"] = tenant_id
            return mock_token_provider

        monkeypatch.setattr("sigantry_core.client.purview.get_token_provider", fake_get_tp)
        PurviewRestClient.from_defaults(
            base_url="https://contoso.purview.azure.net",
            tenant_id="t-purview",
        )
        assert captured["tenant"] == "t-purview"

    def test_no_extra_public_methods_vs_base(self):
        """Purview is a stub — only adds from_defaults on top of BaseRestClient."""
        base_public = {m for m in dir(BaseRestClient) if not m.startswith("_")}
        purv_public = {m for m in dir(PurviewRestClient) if not m.startswith("_")}
        extra = purv_public - base_public
        assert extra == {"from_defaults"}, f"Unexpected public methods on Purview stub: {extra}"
