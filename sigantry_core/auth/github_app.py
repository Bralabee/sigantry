"""GitHub App authentication helper -- JWT mint + installation-token exchange.

NOTE: This module is the second exception (after ``sigantry_core.auth.diagnose``)
to the single-HTTP-client invariant. The per-file ruff ignore for ``TID251``
in ``pyproject.toml`` permits the direct ``import httpx`` here. Rationale:
``BaseRestClient.send`` requires a ``TokenProvider``, but the JWT *is* the
credential we're exchanging -- a chicken-and-egg situation. Exchanging the
JWT for an installation token has to happen outside the standard auth chain,
hence the carve-out.

Auth flow (per
https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app):

    1. Mint an RS256-signed JWT with claims:
         - iat = now - 60s   (Pitfall 6: GitHub allows up to 60s past skew)
         - exp = now + 9*60s (Pitfall 6: GitHub max is 10min; 9 is safer)
         - iss = app_id      (the GitHub App id; integer cast to string)
    2. POST the JWT as a Bearer to /app/installations/{id}/access_tokens.
    3. Response body carries {"token": "...", "expires_at": "ISO 8601"}.
       Token is short-lived (1 hour); expires_at lets the caller cache.

The provider in ``sigantry_core.workitems.github`` calls this helper on
first use and caches the token + expires_at. PAT auth (the simpler v3.0
default) skips this module entirely.

Source: 11-RESEARCH.md Pattern 2 (lines 257-277) + Pitfall 6 (lines 456-461).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Final

import httpx  # legal here -- per-file ruff ignore in pyproject.toml
import jwt  # PyJWT[crypto], dep added in Plan 11-00

_GITHUB_API: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION: Final[str] = "2022-11-28"


def mint_app_jwt(app_id: str, private_key_pem: str) -> str:
    """Sign an RS256 JWT for the given GitHub App.

    Pitfall 6 (clock skew): use ``iat = now - 60`` and ``exp = now + 9*60``.
    GitHub allows up to 60s past skew on ``iat`` and rejects ``exp`` more
    than 10 minutes in the future; 9 minutes leaves a safety margin.

    Parameters
    ----------
    app_id : str
        The GitHub App id (integer cast to string at the call site).
    private_key_pem : str
        The App's RSA private key in PEM format. Treated as opaque -- the
        caller is responsible for resolving secrets via
        ``sigantry_core.auth.keyvault.resolve_secret`` before passing.

    Returns
    -------
    str
        The signed JWT (RS256). Suitable as a ``Bearer`` token on
        ``POST /app/installations/<id>/access_tokens``.
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 9 * 60,
        "iss": str(app_id),
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def mint_installation_token(
    app_id: str,
    private_key_pem: str,
    installation_id: str,
    *,
    http_client: httpx.Client | None = None,
) -> tuple[str, datetime]:
    """Exchange the App JWT for a 1-hour installation token.

    Returns ``(token, expires_at)``. The caller should cache both values
    and only re-mint when ``expires_at`` is within ~1 minute of now.

    Parameters
    ----------
    app_id : str
        The GitHub App id (integer cast to string at the call site).
    private_key_pem : str
        The App's RSA private key in PEM format. See ``mint_app_jwt``.
    installation_id : str
        The GitHub installation id for the target organisation/repo.
    http_client : httpx.Client | None
        Optional client for testability (respx attaches to a custom
        transport). When ``None``, uses ``httpx.post`` with timeout 30s.

    Returns
    -------
    tuple[str, datetime]
        ``(token, expires_at)``. ``expires_at`` is a timezone-aware UTC
        datetime parsed from the response's ``expires_at`` field.
    """
    app_jwt = mint_app_jwt(app_id, private_key_pem)
    url = f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }
    if http_client is not None:
        r = http_client.post(url, headers=headers, timeout=30.0)
    else:
        r = httpx.post(url, headers=headers, timeout=30.0)
    r.raise_for_status()
    body = r.json()
    token = body["token"]
    # GitHub returns ISO 8601 with trailing 'Z'; Python <3.12 fromisoformat
    # rejects 'Z' so substitute '+00:00'.
    expires_iso = body["expires_at"].replace("Z", "+00:00")
    expires_at = datetime.fromisoformat(expires_iso)
    return token, expires_at
