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
- ``check_entra_group`` sends a request only when it is given an expected
  group. It needs a Microsoft Graph token (scope ``GRAPH_SCOPE``), not the
  Fabric token used by ``check_tenant_toggles``, and never sends a token
  whose ``aud`` claim names only other resources. A response from Graph
  other than 200 is an ``error`` with a ``classification``, not ``missing``.
  Called without ``expected_group`` it emits a ``FutureWarning``: sigantry
  1.0.0 checked a built-in group there, which has been removed.
- Importing ``ExpectedEntraGroup`` by name, as code written against sigantry
  1.0.0 may do, emits a ``FutureWarning`` (see ``__getattr__``). ``import *``
  does not bind it.
"""

from __future__ import annotations

import base64
import json
import logging
import warnings
from typing import TYPE_CHECKING, Any, Literal

# TID251 (ban httpx) is silenced for this file in pyproject.toml
# [tool.ruff.lint.per-file-ignores]; see the module docstring.
import httpx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, GRAPH_AUDIENCE, GRAPH_SCOPE

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: ``check_entra_group`` detail when no expected group was given.
_GROUP_CHECK_SKIPPED_DETAIL = "not checked: no expected group configured"

#: ``FutureWarning`` for ``check_entra_group()`` called without a group.
_NO_GROUP_WARNING = (
    "check_entra_group() was called without expected_group, so no request is sent. "
    "sigantry 1.0.0 checked a built-in group name, which has been removed. Pass "
    "expected_group=<group display name>."
)

#: ``FutureWarning`` for an import of the deprecated ``ExpectedEntraGroup`` name.
_EXPECTED_ENTRA_GROUP_WARNING = (
    "sigantry_core.auth.diagnose.ExpectedEntraGroup is deprecated: the built-in Entra "
    "group it named has been removed. Configure the group with [auth] expected_group, "
    "SIGANTRY_AUTH__EXPECTED_GROUP or --expected-group, or pass expected_group= to "
    "check_entra_group()."
)

if TYPE_CHECKING:
    #: Deprecated. See ``__getattr__`` below.
    ExpectedEntraGroup: str
else:

    def __getattr__(name: str) -> str:
        """Serve the deprecated ``ExpectedEntraGroup`` name (PEP 562).

        sigantry 1.0.0 exported it as the built-in group ``check_entra_group``
        checked. That value is gone: the name emits a ``FutureWarning`` and then
        returns an empty string, which ``check_entra_group`` treats as no group.
        A warnings filter set to ``error`` raises the warning instead, so
        nothing is returned. Under the default filters a ``FutureWarning`` is
        shown, also when the importer is a package module, where a
        ``DeprecationWarning`` is hidden. Every other unknown name raises
        ``AttributeError`` as before.
        """
        if name == "ExpectedEntraGroup":
            warnings.warn(_EXPECTED_ENTRA_GROUP_WARNING, FutureWarning, stacklevel=2)
            return ""
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: ``aud`` values a Microsoft Graph access token carries: the resource URI,
#: with or without a trailing slash, or Graph's well-known application id.
_GRAPH_TOKEN_AUDIENCES = frozenset(
    {GRAPH_AUDIENCE, f"{GRAPH_AUDIENCE}/", "00000003-0000-0000-c000-000000000000"}
)

#: Most ``memberOf`` pages ``check_entra_group`` reads before it gives up.
_MAX_MEMBER_OF_PAGES = 50

ErrorClassification = Literal["ok", "token_rejected", "api_not_enabled", "other"]

GroupCheckClassification = Literal[
    "ok",
    "missing",
    "skipped",
    "settings_unreadable",
    "token_unavailable",
    "wrong_audience",
    "token_rejected",
    "permission_denied",
    "delegated_only",
    "names_hidden",
    "incomplete",
    "other",
]
"""Why a group check ended as it did.

``ok``, ``missing`` and ``skipped`` decide the membership (or say there was
none to decide). Every other value has ``status="error"``: the membership is
unknown, and the value names what stopped the check.
"""


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
    an empty dict rather than raising; a payload that is JSON but not an
    object carries no claims.
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
    if not isinstance(payload, dict):
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


def group_check_result(
    status: str,
    classification: GroupCheckClassification,
    *,
    expected: str | None,
    detail: str,
    groups: list[str] | None = None,
) -> dict[str, Any]:
    """Build one ``entra_groups`` result in the shape ``check_entra_group`` returns."""
    return {
        "status": status,
        "classification": classification,
        "groups": list(groups or []),
        "expected": expected,
        "detail": detail,
    }


def _graph_error_message(resp: httpx.Response) -> str:
    """Return Graph's ``error.message``, or ``""`` when the body has none."""
    try:
        error = resp.json().get("error") or {}
        message = error.get("message") if isinstance(error, dict) else None
    except Exception:  # not JSON, or not an object
        return ""
    return message if isinstance(message, str) else ""


def _graph_refusal(
    resp: httpx.Response, *, expected_group: str, principal_id: str | None
) -> dict[str, Any]:
    """Explain a non-200 ``memberOf`` response; the membership stays unknown."""
    code = resp.status_code
    if code == 401:
        return group_check_result(
            "error",
            "token_rejected",
            expected=expected_group,
            detail=(
                "401: Microsoft Graph rejected the token; the membership is unknown. "
                f"The token must be issued for {GRAPH_SCOPE}."
            ),
        )
    if code == 403:
        if principal_id:
            need = "Application.Read.All (or Directory.Read.All)"
            whose = "the service principal's"
        else:
            need = "the User.Read delegated permission (or GroupMember.Read.All)"
            whose = "the signed-in user's"
        return group_check_result(
            "error",
            "permission_denied",
            expected=expected_group,
            detail=(
                f"403: Microsoft Graph refused to list {whose} memberships; "
                f"the membership is unknown. Grant {need}."
            ),
        )
    if code == 400 and not principal_id and "delegated" in _graph_error_message(resp).lower():
        return group_check_result(
            "error",
            "delegated_only",
            expected=expected_group,
            detail=(
                "400: /me/memberOf needs a signed-in user; the membership is unknown. "
                "For a service principal, pass its object id with --principal-id."
            ),
        )
    return group_check_result(
        "error",
        "other",
        expected=expected_group,
        detail=f"{code}: {resp.reason_phrase} from Microsoft Graph; the membership is unknown",
    )


def _issued_for_another_resource(audience: object) -> bool:
    """Whether a decoded ``aud`` claim names only resources other than Graph.

    ``aud`` is usually a string but may be a list. An absent claim, or one of
    another type, decides nothing: such a token is sent, as an opaque token is.
    """
    if isinstance(audience, str):
        audiences = [audience]
    elif isinstance(audience, list):
        audiences = [a for a in audience if isinstance(a, str)]
    else:
        return False
    return bool(audiences) and _GRAPH_TOKEN_AUDIENCES.isdisjoint(audiences)


def check_entra_group(
    token: str,
    *,
    principal_id: str | None = None,
    expected_group: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Probe MS Graph for membership of ``expected_group``. Read-only.

    With no ``expected_group`` there is nothing to check and no request is
    sent. The call emits a ``FutureWarning``, because sigantry 1.0.0 checked a
    built-in group there; a warnings filter set to ``error`` raises it, and
    otherwise the result has ``status="skipped"`` -- never ``"ok"``, so an
    unconfigured check cannot read as a passed one. The ``diagnose-auth``
    command builds its skipped result without calling this function, so it
    does not emit that warning.

    ``token`` must be a Microsoft Graph token (scope ``GRAPH_SCOPE``). A token
    whose ``aud`` claim names only other resources, such as the Fabric token,
    is never sent; ``wrong_audience`` means a token was held back for that
    reason.

    When `principal_id` is provided, use /servicePrincipals/{id}/memberOf.
    Otherwise use /me/memberOf (user token path). Later pages
    (``@odata.nextLink``) are followed on the Graph host only.

    Returns: `{"status", "classification", "groups", "expected", "detail"}`,
    where status is one of ``ok`` / ``missing`` / ``error`` / ``skipped`` and
    ``classification`` (a ``GroupCheckClassification``) says why. ``missing``
    means the pages were read to the last one, every group entry had a name and
    none was ``expected_group``. A response other than 200 is an ``error``.
    ``names_hidden`` marks an ``error`` in which some entries had no name and
    none of the named ones was ``expected_group``. ``incomplete`` marks an
    ``error`` in which ``_MAX_MEMBER_OF_PAGES`` pages were read, each with a
    link to a next one, and none of their named entries was ``expected_group``.
    """
    if not expected_group:
        warnings.warn(_NO_GROUP_WARNING, FutureWarning, stacklevel=2)
        return group_check_result(
            "skipped", "skipped", expected=None, detail=_GROUP_CHECK_SKIPPED_DETAIL
        )
    audience = decode_token_claims(token).get("aud")
    if _issued_for_another_resource(audience):
        return group_check_result(
            "error",
            "wrong_audience",
            expected=expected_group,
            detail=(
                f"not checked: the token was issued for {audience!r}, not Microsoft Graph; "
                f"a token for {GRAPH_SCOPE} is required"
            ),
        )
    if principal_id:
        url = f"{GRAPH_AUDIENCE}/v1.0/servicePrincipals/{principal_id}/memberOf?$select=displayName"
    else:
        url = f"{GRAPH_AUDIENCE}/v1.0/me/memberOf?$select=displayName"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    close_client = False
    if client is None:
        client = httpx.Client(timeout=10.0)
        close_client = True

    groups: list[str] = []
    unnamed = 0
    try:
        for page in range(_MAX_MEMBER_OF_PAGES):
            try:
                resp = client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                if page == 0:
                    raise  # the first request fails as it did in 1.0.0
                return group_check_result(
                    "error",
                    "other",
                    expected=expected_group,
                    detail=(
                        f"{type(exc).__name__} reading memberOf page {page + 1} from "
                        "Microsoft Graph; the membership is unknown"
                    ),
                    groups=groups,
                )
            if resp.status_code != 200:
                return _graph_refusal(
                    resp, expected_group=expected_group, principal_id=principal_id
                )
            try:
                body = resp.json()
                for entry in body.get("value", []):
                    name = entry.get("displayName")
                    if name:
                        groups.append(name)
                    elif entry.get("@odata.type", "#microsoft.graph.group") == (
                        "#microsoft.graph.group"
                    ):
                        unnamed += 1
                next_link = body.get("@odata.nextLink")
            except Exception:
                return group_check_result(
                    "error",
                    "other",
                    expected=expected_group,
                    detail="unparseable 200 from Microsoft Graph",
                    groups=groups,
                )
            if expected_group in groups:
                return group_check_result(
                    "ok",
                    "ok",
                    expected=expected_group,
                    detail=f"member of {expected_group}",
                    groups=groups,
                )
            if not next_link:
                break
            if not isinstance(next_link, str) or not next_link.startswith(f"{GRAPH_AUDIENCE}/"):
                return group_check_result(
                    "error",
                    "other",
                    expected=expected_group,
                    detail=(
                        f"Microsoft Graph returned a next page outside {GRAPH_AUDIENCE}; "
                        "it was not followed and the membership is unknown"
                    ),
                    groups=groups,
                )
            url = next_link
        else:
            return group_check_result(
                "error",
                "incomplete",
                expected=expected_group,
                detail=(
                    "not decided: none of the named memberships on the first "
                    f"{_MAX_MEMBER_OF_PAGES} pages is {expected_group}, and Microsoft "
                    "Graph returned a link to more pages"
                ),
                groups=groups,
            )
    finally:
        if close_client:
            client.close()

    if unnamed:
        return group_check_result(
            "error",
            "names_hidden",
            expected=expected_group,
            detail=(
                f"not decided: Microsoft Graph returned {unnamed} membership(s) without "
                f"a name, so {expected_group} may be one of them. Grant GroupMember.Read.All "
                "(or Directory.Read.All) to read group names."
            ),
            groups=groups,
        )
    return group_check_result(
        "missing",
        "missing",
        expected=expected_group,
        detail=f"not in {expected_group}",
        groups=groups,
    )


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
