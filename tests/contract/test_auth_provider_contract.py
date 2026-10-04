"""AuthProvider contract tests.

Runs the contract against the ``FakeAuth`` double, whose ``get_token``
returns a static ``Secret`` and fully exercises the contract.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import AuthProvider, Secret
from sigantry_core.testing.doubles import FakeAuth


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
