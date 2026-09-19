"""AuthProvider contract tests.

The plugin's ``Hs2EntraGroupAuth.get_token`` cannot run without a real
Entra token endpoint; we assert only protocol conformance + ``name`` +
a tokenless init for the plugin. The double's ``get_token`` returns a
static ``Secret`` and fully exercises the contract.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import AuthProvider, Secret
from sigantry_core.testing.doubles import FakeAuth


def _plugin_auth_or_skip():
    pytest.importorskip("sigantry_hs2")
    from sigantry_hs2.auth.hs2_entra_group import Hs2EntraGroupAuth

    return Hs2EntraGroupAuth()


@pytest.mark.contract
def test_auth_provider_double_has_name(fdt_auth_provider_contract) -> None:
    fdt_auth_provider_contract(FakeAuth())


@pytest.mark.contract
def test_auth_provider_double_get_token_returns_secret() -> None:
    auth = FakeAuth(token="contract-token")
    secret = auth.get_token("https://example.invalid/.default")
    assert isinstance(secret, Secret)
    assert secret.value == "contract-token"


@pytest.mark.contract
def test_auth_provider_double_secret_value_is_string() -> None:
    secret = FakeAuth().get_token("scope")
    assert isinstance(secret.value, str) and secret.value


@pytest.mark.contract
def test_auth_provider_double_satisfies_runtime_protocol() -> None:
    assert isinstance(FakeAuth(), AuthProvider)


@pytest.mark.contract
def test_auth_provider_plugin_has_name(fdt_auth_provider_contract) -> None:
    fdt_auth_provider_contract(_plugin_auth_or_skip())


@pytest.mark.contract
def test_auth_provider_plugin_satisfies_runtime_protocol() -> None:
    assert isinstance(_plugin_auth_or_skip(), AuthProvider)
