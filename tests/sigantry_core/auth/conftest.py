"""Shared fixtures for the auth test tree.

- `fake_credential`: MagicMock mimicking DefaultAzureCredential with
  a fixed-expiry AccessToken.
- `fake_secret_client`: MagicMock mimicking SecretClient with a configurable
  get_secret return value.
- `respx_router`: a respx.MockRouter yielded for the test body (auto-clean).
- `make_jwt`: factory for constructing an unsigned JWT with controllable claims
  for diagnose.decode_token_claims tests.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
import respx
from azure.core.credentials import AccessToken


@pytest.fixture
def fake_credential() -> MagicMock:
    cred = MagicMock(name="FakeDefaultAzureCredential")
    cred.get_token.return_value = AccessToken(
        token="fake-access-token-xyz",
        expires_on=int(time.time()) + 3600,
    )
    return cred


@pytest.fixture
def fake_secret_client() -> MagicMock:
    client = MagicMock(name="FakeSecretClient")
    kv_secret = MagicMock(name="FakeKvSecret")
    kv_secret.value = "resolved-secret-value"
    client.get_secret.return_value = kv_secret
    return client


@pytest.fixture
def respx_router() -> respx.MockRouter:
    with respx.mock(assert_all_called=False) as router:
        yield router


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture
def make_jwt() -> Callable[[dict], str]:
    def _factory(claims: dict) -> str:
        header = _b64url(json.dumps({"typ": "JWT", "alg": "none"}).encode())
        payload = _b64url(json.dumps(claims).encode())
        sig = ""
        return f"{header}.{payload}.{sig}"

    return _factory
