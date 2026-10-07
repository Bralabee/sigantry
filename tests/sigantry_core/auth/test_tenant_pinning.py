"""Tenant pinning at the credential the TokenProvider holds and hands out.

A pinned provider must request every token from the pinned tenant and refuse
one whose ``tid`` claim names another tenant, on every path a token takes:
``TokenProvider.get_token`` and a caller holding ``get_credential()``, which
is how fabric-cicd gets its tokens. Without a tenant nothing changes.

Every token here is a fake: ``header.payload.signature`` with base64url JSON.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from azure.core.credentials import AccessToken, AccessTokenInfo

from sigantry_core.auth.audiences import FABRIC_SCOPE, GRAPH_SCOPE
from sigantry_core.auth.errors import TokenAcquisitionError
from sigantry_core.auth.token_provider import (
    TokenProvider,
    get_token_provider,
    reset_token_provider,
)

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def fake_jwt(**claims: Any) -> str:
    header = _b64url(json.dumps({"typ": "JWT", "alg": "none"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.sig"


class _FakeCredential:
    """Records every token request; returns a fake JWT carrying ``tid``."""

    def __init__(self, tid: str | None = TENANT_A, *, token: str | None = None) -> None:
        self.tid = tid
        self.token = token
        self.calls: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []

    def _value(self) -> str:
        if self.token is not None:
            return self.token
        return fake_jwt(tid=self.tid) if self.tid is not None else fake_jwt(oid="x")

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.calls.append(("get_token", scopes, dict(kwargs)))
        return AccessToken(self._value(), int(time.time()) + 3600)

    def get_token_info(self, *scopes: str, options: Any = None) -> AccessTokenInfo:
        self.calls.append(("get_token_info", scopes, dict(options or {})))
        return AccessTokenInfo(self._value(), int(time.time()) + 3600)


class _GetTokenOnlyCredential:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.calls.append(dict(kwargs))
        return AccessToken(fake_jwt(tid=TENANT_A), int(time.time()) + 3600)


@pytest.fixture(autouse=True)
def _clean_pool():
    reset_token_provider()
    yield
    reset_token_provider()


def _mismatch_error() -> type[Exception]:
    from sigantry_core.auth import TenantMismatchError

    return TenantMismatchError


def _invalid_tenant_error() -> type[Exception]:
    from sigantry_core.auth import InvalidTenantIdError

    return InvalidTenantIdError


# ---- requests carry the pin ------------------------------------------------


def test_pinned_get_token_requests_the_pinned_tenant() -> None:
    cred = _FakeCredential(tid=TENANT_A)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    tp.get_token(FABRIC_SCOPE)
    assert cred.calls == [("get_token", (FABRIC_SCOPE,), {"tenant_id": TENANT_A})]


def test_handed_out_credential_requests_the_pin_on_a_bare_get_token() -> None:
    """fabric-cicd calls ``token_credential.get_token(scope)`` with no tenant."""
    cred = _FakeCredential(tid=TENANT_A)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    token = tp.get_credential().get_token(FABRIC_SCOPE)
    assert cred.calls == [("get_token", (FABRIC_SCOPE,), {"tenant_id": TENANT_A})]
    assert token.token == fake_jwt(tid=TENANT_A)
    assert tp.credential is tp.get_credential()


def test_handed_out_credential_checks_tid_on_a_bare_get_token() -> None:
    tp = TokenProvider(credential=_FakeCredential(tid=TENANT_B), tenant_id=TENANT_A)
    with pytest.raises(_mismatch_error()):
        tp.get_credential().get_token(FABRIC_SCOPE)


def test_handed_out_credential_pins_and_checks_get_token_info() -> None:
    cred = _FakeCredential(tid=TENANT_A)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    info = tp.get_credential().get_token_info(FABRIC_SCOPE)  # type: ignore[attr-defined]
    assert info.token == fake_jwt(tid=TENANT_A)
    assert cred.calls == [("get_token_info", (FABRIC_SCOPE,), {"tenant_id": TENANT_A})]

    cred.tid = TENANT_B
    with pytest.raises(_mismatch_error()):
        tp.get_credential().get_token_info(FABRIC_SCOPE)  # type: ignore[attr-defined]


def test_wrapper_has_no_get_token_info_when_the_credential_lacks_it() -> None:
    cred = _GetTokenOnlyCredential()
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    assert not hasattr(tp.get_credential(), "get_token_info")
    tp.get_token(FABRIC_SCOPE)
    assert cred.calls == [{"tenant_id": TENANT_A}]


def test_default_chain_is_pinned_too() -> None:
    cred = _FakeCredential(tid=TENANT_B)
    with patch(
        "sigantry_core.auth.token_provider.DefaultAzureCredential", return_value=cred
    ) as dac:
        tp = TokenProvider(tenant_id=TENANT_A)
    assert dac.call_args.kwargs["additionally_allowed_tenants"] == [TENANT_A]
    assert tp.credential is not cred
    with pytest.raises(_mismatch_error()):
        tp.get_token(FABRIC_SCOPE)
    assert cred.calls[0][2] == {"tenant_id": TENANT_A}


# ---- the tid check ---------------------------------------------------------


def test_tid_mismatch_raises_tenant_mismatch_error() -> None:
    cred = _FakeCredential(tid=TENANT_B)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    with pytest.raises(_mismatch_error()) as info:
        tp.get_token(FABRIC_SCOPE)
    exc: Any = info.value
    assert isinstance(exc, TokenAcquisitionError)
    assert exc.expected_tenant == TENANT_A
    assert exc.token_tenant == TENANT_B
    assert exc.credential_used == "_FakeCredential"
    assert exc.scope == FABRIC_SCOPE
    assert TENANT_A in str(exc) and TENANT_B in str(exc)
    assert "az login --tenant" in (exc.remediation or "")
    token = fake_jwt(tid=TENANT_B)
    for text in (str(exc), repr(exc), exc.remediation or ""):
        assert token not in text
        assert token.split(".")[1] not in text
    # A refused token is never cached.
    assert tp.last_credential_class(FABRIC_SCOPE) is None


def test_tid_in_another_case_is_accepted() -> None:
    cred = _FakeCredential(tid=TENANT_A.upper())
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    assert tp.get_token(FABRIC_SCOPE) == fake_jwt(tid=TENANT_A.upper())
    assert cred.calls == [("get_token", (FABRIC_SCOPE,), {"tenant_id": TENANT_A})]


@pytest.mark.parametrize(
    "token",
    [
        fake_jwt(oid="no-tid-claim"),
        fake_jwt(tid=""),
        fake_jwt(tid=["not", "a", "string"]),
        "opaque-token-that-is-not-a-jwt",
        "a.!!!.c",
    ],
    ids=["no-tid", "blank-tid", "non-string-tid", "opaque", "undecodable"],
)
def test_a_token_whose_tenant_cannot_be_read_is_refused(token: str) -> None:
    tp = TokenProvider(credential=_FakeCredential(token=token), tenant_id=TENANT_A)
    with pytest.raises(_mismatch_error()) as info:
        tp.get_token(FABRIC_SCOPE)
    assert getattr(info.value, "token_tenant", None) == "unreadable"
    assert token not in str(info.value)


def test_a_caller_asking_for_another_tenant_is_refused() -> None:
    cred = _FakeCredential(tid=TENANT_B)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    with pytest.raises(_mismatch_error()):
        tp.get_credential().get_token(FABRIC_SCOPE, tenant_id=TENANT_B)
    with pytest.raises(_mismatch_error()):
        tp.get_credential().get_token_info(  # type: ignore[attr-defined]
            FABRIC_SCOPE, options={"tenant_id": TENANT_B}
        )
    assert cred.calls == []


def test_a_caller_asking_for_the_pinned_tenant_in_another_case_is_served() -> None:
    cred = _FakeCredential(tid=TENANT_A)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    tp.get_credential().get_token(FABRIC_SCOPE, tenant_id=TENANT_A.upper())
    assert cred.calls == [("get_token", (FABRIC_SCOPE,), {"tenant_id": TENANT_A})]


def test_fabric_cicd_endpoint_refuses_a_token_from_another_tenant() -> None:
    """fabric-cicd 1.4.0 requests its token itself, on the handed-out credential."""
    from fabric_cicd._common._exceptions import TokenError
    from fabric_cicd._common._fabric_endpoint import FabricEndpoint

    cred = _FakeCredential(tid=TENANT_B)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    with pytest.raises(TokenError) as info:
        FabricEndpoint(token_credential=tp.get_credential())
    assert isinstance(info.value.__cause__, _mismatch_error())
    assert cred.calls[0][2] == {"tenant_id": TENANT_A}


# ---- no tenant: unchanged --------------------------------------------------


def test_without_a_tenant_the_credential_is_called_as_before() -> None:
    cred = _FakeCredential(tid=TENANT_B)
    tp = TokenProvider(credential=cred)
    assert tp.credential is cred
    assert tp.get_credential() is cred
    assert tp.get_token(FABRIC_SCOPE) == fake_jwt(tid=TENANT_B)
    tp.get_token(GRAPH_SCOPE)
    assert cred.calls == [
        ("get_token", (FABRIC_SCOPE,), {}),
        ("get_token", (GRAPH_SCOPE,), {}),
    ]


# ---- the tenant id must be a GUID ------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["contoso.onmicrosoft.com", "tenant-a", "", "   ", "{" + TENANT_A + "}"],
)
def test_a_tenant_that_is_not_a_guid_is_refused_before_any_credential_call(value: str) -> None:
    cred = _FakeCredential(tid=TENANT_A)
    with patch("sigantry_core.auth.token_provider.DefaultAzureCredential") as dac:
        with pytest.raises(_invalid_tenant_error()) as info:
            TokenProvider(tenant_id=value)
        with pytest.raises(_invalid_tenant_error()):
            TokenProvider(credential=cred, tenant_id=value)
        with pytest.raises(_invalid_tenant_error()):
            get_token_provider(tenant_id=value)
    dac.assert_not_called()
    assert cred.calls == []
    assert isinstance(info.value, ValueError)
    assert "directory (tenant) ID" in str(info.value)


def test_a_guid_with_surrounding_whitespace_is_pinned_stripped() -> None:
    cred = _FakeCredential(tid=TENANT_A)
    tp = TokenProvider(credential=cred, tenant_id=f"  {TENANT_A}\n")
    assert tp.tenant_id == TENANT_A
    tp.get_token(FABRIC_SCOPE)
    assert cred.calls[0][2] == {"tenant_id": TENANT_A}


# ---- the provider pool -------------------------------------------------------


def test_pool_keys_tenants_case_and_whitespace_insensitively() -> None:
    with patch("sigantry_core.auth.token_provider.DefaultAzureCredential"):
        lower = get_token_provider(tenant_id=TENANT_A)
        assert get_token_provider(tenant_id=TENANT_A.upper()) is lower
        assert get_token_provider(tenant_id=f" {TENANT_A} ") is lower
        assert get_token_provider(tenant_id=TENANT_B) is not lower
        default = get_token_provider()
        assert default is get_token_provider(None)
        assert default is not lower
        assert default.tenant_id is None


def test_errors_are_exported() -> None:
    from sigantry_core import auth

    assert issubclass(auth.TenantMismatchError, auth.TokenAcquisitionError)
    assert issubclass(auth.InvalidTenantIdError, auth.FabricAuthError)
    assert "TenantMismatchError" in auth.__all__
    assert "InvalidTenantIdError" in auth.__all__
