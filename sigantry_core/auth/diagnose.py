"""Diagnostic probes for the diagnose-auth CLI.

Uses ``httpx`` directly instead of ``sigantry_core.client``, so this file is
one of the exceptions to the "one HTTP client" rule in CONTRIBUTING.md. The
exceptions are listed in pyproject.toml: the ``httpx`` banned-api message and
``[tool.ruff.lint.per-file-ignores]``, which silences TID251 for this file.

- ``classify_http_error`` separates 401 (the token itself was rejected) from
  403 (the token is fine; a tenant setting or role blocks the principal).
- ``decode_token_claims`` surfaces the token's tenant (``tid``) so a login to
  the wrong tenant is visible. Raw tokens are never logged or returned; only
  whitelisted claims are.
- ``check_entra_group`` checks membership of an expected group only when it
  is given one; otherwise it reports the check as ``skipped``.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any, Literal

# TID251 (ban httpx) is silenced for this file in pyproject.toml
# [tool.ruff.lint.per-file-ignores]; see the module docstring.
import httpx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, GRAPH_AUDIENCE

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: ``check_entra_group`` detail when no expected group was given.
_GROUP_CHECK_SKIPPED_DETAIL = "not checked: no expected group configured"

ErrorClassification = Literal["ok", "token_rejected", "api_not_enabled", "other"]


def classify_http_error(resp: httpx.Response) -> ErrorClassification:
    """Classify a Fabric Admin REST / MS Graph response.

    - 200 -> "ok"
    - 401 -> "token_rejected" (token itself is invalid or expired)
    - 403 -> "api_not_enabled" (token is fine; SP is blocked by tenant setting
             - Pitfall 1). Fabric REST returns body `{"errorCode":"ApiNotApplicable"...}`
             in this case, but a 403 without that body is the same remediation
             bucket (RBAC issue), so we do not disambiguate further.
    - other -> "other"
    """
    if resp.status_code == 200:
        return "ok"
    if resp.status_code == 401:
        return "token_rejected"
    if resp.status_code == 403:
        return "api_not_enabled"
    return "other"


def decode_token_claims(token: str) -> dict[str, Any]:
    """Decode the JWT payload (middle segment) via stdlib. No signature check.

    Returns a dict of claims (subset if any are missing). Never returns the
    raw token. Never logs the raw token. If the token is malformed, returns
    an empty dict rather than raising.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        # urlsafe_b64decode requires correct padding; JWT omits it.
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:  # malformed token, return empty
        return {}
    # Whitelist claims we explicitly surface; do NOT return arbitrary fields
    # that might contain surprise PII.
    return {
        key: payload.get(key)
        for key in (
            "aud",
            "iss",
            "tid",
            "oid",
            "appid",
            "exp",
            "iat",
            "scp",
            "app_displayname",
        )
        if key in payload
    }


def check_tenant_toggles(
    token: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Probe the Fabric Admin tenantsettings endpoint. Read-only.

    Returns: `{"status", "classification", "toggles_visible", "detail"}`.
    """
    url = f"{FABRIC_AUDIENCE}/v1/admin/tenantsettings"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    close_client = False
    if client is None:
        client = httpx.Client(timeout=10.0)
        close_client = True
    try:
        resp = client.get(url, headers=headers)
    finally:
        if close_client:
            client.close()

    classification = classify_http_error(resp)
    if classification == "ok":
        try:
            body = resp.json()
            toggles = body.get("tenantSettings") or body.get("value") or []
            return {
                "status": "ok",
                "classification": "ok",
                "toggles_visible": len(toggles),
                "detail": f"{len(toggles)} tenant settings visible",
            }
        except Exception:
            return {
                "status": "degraded",
                "classification": "other",
                "toggles_visible": 0,
                "detail": "unparseable 200 body",
            }
    status_map = {
        "token_rejected": ("degraded", "401: token rejected by Fabric Admin REST"),
        "api_not_enabled": ("blocked", "403: service principal blocked by tenant setting"),
        "other": ("blocked", f"{resp.status_code}: {resp.reason_phrase}"),
    }
    status, detail = status_map.get(classification, ("blocked", "unknown"))
    return {
        "status": status,
        "classification": classification,
        "toggles_visible": 0,
        "detail": detail,
    }


def check_entra_group(
    token: str,
    *,
    principal_id: str | None = None,
    expected_group: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Probe MS Graph for membership of ``expected_group``. Read-only.

    With no ``expected_group`` there is nothing to check: no request is sent
    and the result has ``status="skipped"`` -- never ``"ok"``, so an
    unconfigured check cannot read as a passed one.

    When `principal_id` is provided, use /servicePrincipals/{id}/memberOf.
    Otherwise use /me/memberOf (user token path).

    Returns: `{"status", "groups", "expected", "detail"}`, where status is
    one of ``ok`` / ``missing`` / ``error`` / ``skipped``.
    """
    if not expected_group:
        return {
            "status": "skipped",
            "groups": [],
            "expected": None,
            "detail": _GROUP_CHECK_SKIPPED_DETAIL,
        }
    if principal_id:
        url = f"{GRAPH_AUDIENCE}/v1.0/servicePrincipals/{principal_id}/memberOf?$select=displayName"
    else:
        url = f"{GRAPH_AUDIENCE}/v1.0/me/memberOf?$select=displayName"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    close_client = False
    if client is None:
        client = httpx.Client(timeout=10.0)
        close_client = True
    try:
        resp = client.get(url, headers=headers)
    finally:
        if close_client:
            client.close()

    if resp.status_code != 200:
        return {
            "status": "error",
            "groups": [],
            "expected": expected_group,
            "detail": f"{resp.status_code}: {resp.reason_phrase}",
        }

    try:
        body = resp.json()
        groups = [g.get("displayName", "") for g in body.get("value", []) if g.get("displayName")]
    except Exception:
        return {
            "status": "error",
            "groups": [],
            "expected": expected_group,
            "detail": "unparseable 200",
        }

    is_member = expected_group in groups
    return {
        "status": "ok" if is_member else "missing",
        "groups": groups,
        "expected": expected_group,
        "detail": f"member of {expected_group}" if is_member else f"not in {expected_group}",
    }


def build_report(
    *,
    scope: str,
    credential_used: str | None,
    token: str | None,
    tenant_toggles: Mapping[str, Any] | None,
    entra_groups: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the structured diagnose-auth report. NEVER includes raw token.

    A group check reported as ``skipped`` (no expected group) does not change
    the exit code.
    """
    claims = decode_token_claims(token) if token else {}
    exit_code = 0
    if token is None:
        exit_code = 3
    elif (tenant_toggles and tenant_toggles.get("status") != "ok") or (
        entra_groups and entra_groups.get("status") not in ("ok", "skipped", None)
    ):
        exit_code = 2
    return {
        "credential_used": credential_used,
        "scope": scope,
        "token_claims": dict(claims),
        "tenant_toggles": dict(tenant_toggles) if tenant_toggles is not None else None,
        "entra_groups": dict(entra_groups) if entra_groups is not None else None,
        "exit_code": exit_code,
    }
