"""Unit tests for sigantry_core.auth.github_app -- JWT signing + token exchange.

Covers:
    - mint_app_jwt happy path: RS256 algorithm, correct iat/exp/iss claims
      (Pitfall 6 invariants: iat = now - 60, exp = now + 9*60).
    - mint_app_jwt accepts integer app_id and normalises to string in iss.
    - mint_installation_token round-trips via respx mock and returns
      (token, expires_at) parsed from the response.
    - mint_installation_token surfaces 401 from GitHub as
      httpx.HTTPStatusError via raise_for_status.

Plan: 11-05 Task 1.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from sigantry_core.auth.github_app import mint_app_jwt, mint_installation_token


@pytest.fixture(scope="session")
def fake_jwt_signing_key() -> str:
    """RSA 2048 private key in PEM (session -- generation is slow)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture
def respx_router():
    """respx router for mocking httpx at the transport layer."""
    with respx.mock(assert_all_called=False) as router:
        yield router


def _public_pem_from_private(private_pem: str) -> str:
    private_key = serialization.load_pem_private_key(
        private_pem.encode("utf-8"), password=None, backend=default_backend()
    )
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )


def test_mint_app_jwt_signs_with_rs256_and_correct_claims(
    fake_jwt_signing_key: str,
) -> None:
    token = mint_app_jwt("12345", fake_jwt_signing_key)
    public_pem = _public_pem_from_private(fake_jwt_signing_key)
    decoded = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert decoded["iss"] == "12345"
    # Pitfall 6: iat = now - 60
    now = int(time.time())
    assert now - 70 <= decoded["iat"] <= now - 50
    # Pitfall 6: exp = now + 9*60 (NOT 10*60)
    assert now + 9 * 60 - 10 <= decoded["exp"] <= now + 9 * 60 + 10


def test_mint_app_jwt_iss_uses_string_app_id(fake_jwt_signing_key: str) -> None:
    # GitHub accepts string OR integer iss; our impl normalises to string.
    token = mint_app_jwt(67890, fake_jwt_signing_key)  # type: ignore[arg-type]
    public_pem = _public_pem_from_private(fake_jwt_signing_key)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "67890"


def test_mint_installation_token_round_trip(
    fake_jwt_signing_key: str, respx_router: respx.Router
) -> None:
    """POST /app/installations/<id>/access_tokens returns (token, expires_at)."""
    response_body = {
        "token": "fake-installation-token-abc123",
        "expires_at": "2026-04-26T13:00:00Z",
    }
    route = respx_router.post("https://api.github.com/app/installations/77/access_tokens").respond(
        status_code=201, json=response_body
    )
    with httpx.Client() as client:
        token, expires_at = mint_installation_token(
            "12345", fake_jwt_signing_key, "77", http_client=client
        )
    assert token == "fake-installation-token-abc123"
    assert expires_at == datetime(2026, 4, 26, 13, 0, 0, tzinfo=UTC)
    assert route.called
    # Confirm the JWT was passed as Bearer in the Authorization header.
    sent_headers = dict(route.calls[0].request.headers)
    assert sent_headers["authorization"].startswith("Bearer ")
    assert sent_headers["accept"] == "application/vnd.github+json"
    assert sent_headers["x-github-api-version"] == "2022-11-28"


def test_mint_installation_token_raises_on_401(
    fake_jwt_signing_key: str, respx_router: respx.Router
) -> None:
    respx_router.post("https://api.github.com/app/installations/77/access_tokens").respond(
        status_code=401, json={"message": "Bad credentials"}
    )
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        mint_installation_token("12345", fake_jwt_signing_key, "77", http_client=client)
