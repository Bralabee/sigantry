"""Tenant pinning: the tenant id rules and the credential that enforces a pin.

A :class:`~sigantry_core.auth.token_provider.TokenProvider` built with a
``tenant_id`` holds the credential :func:`pin_credential` returns instead of
the bare credential. Everything that gets a token from that provider goes
through it: ``TokenProvider.get_token`` and every REST client built on the
provider, and fabric-cicd, which calls ``get_token`` itself on the credential
``TokenProvider.get_credential()`` hands it. The pinned credential:

- requests every token with ``tenant_id=<pinned tenant>`` (``get_token``, and
  ``get_token_info`` when the wrapped credential has it);
- reads the ``tid`` claim of every token it returns (the payload is decoded,
  the signature is not checked) and raises :class:`TenantMismatchError` when
  that claim names another tenant, or is missing, or the token cannot be
  decoded;
- refuses a caller that asks it for a token from a different tenant.

The ``tid`` check is the part that holds for every credential: a managed
identity ignores the requested tenant, and ``AZURE_IDENTITY_DISABLE_MULTITENANTAUTH``
makes the other credentials ignore it too.

A tenant to pin must be a directory (tenant) ID GUID in the
``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`` form, because that is the form of the
``tid`` claim it is compared with: a domain name such as
``contoso.onmicrosoft.com`` could never match. :func:`require_tenant_guid` is
the one check; ``TokenProvider`` and the command-line resolution
(:mod:`sigantry_core._cli_tenant`) both call it. Tenant ids are compared
without regard to case or surrounding whitespace (:func:`same_guid`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from sigantry_core.auth.diagnose import decode_token_claims
from sigantry_core.auth.errors import InvalidTenantIdError, TenantMismatchError

if TYPE_CHECKING:
    from azure.core.credentials import (
        AccessToken,
        AccessTokenInfo,
        TokenCredential,
        TokenRequestOptions,
    )

_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: ``TenantMismatchError.token_tenant`` when the token's tenant cannot be read.
UNREADABLE_TENANT = "unreadable"


def normalize_tenant_id(value: str) -> str:
    """Return ``value`` stripped and case-folded, the form tenant ids compare in."""
    return value.strip().casefold()


def same_guid(left: str, right: str) -> bool:
    """Compare two GUIDs (tenant ids, capacity ids) as Entra and Fabric do.

    Case and surrounding whitespace do not matter.
    """
    return normalize_tenant_id(left) == normalize_tenant_id(right)


def require_tenant_guid(value: str, *, source: str = "tenant_id") -> str:
    """Return ``value`` stripped, or raise if it is not a tenant ID GUID.

    ``source`` names where the value came from (a flag or a settings key) in
    the error message. Raises :class:`InvalidTenantIdError` before any token
    is requested.
    """
    candidate = value.strip() if isinstance(value, str) else ""
    if not _GUID.fullmatch(candidate):
        raise InvalidTenantIdError(
            f"{source} {value!r} is not a tenant ID GUID: pinning a tenant needs the "
            "directory (tenant) ID, in the form xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx, "
            "because the token's tid claim it is compared with always has that form",
            remediation=(
                "Use the Directory (tenant) ID shown in the Entra admin center, or "
                "the tenantId that `az account show` prints."
            ),
        )
    return candidate


def _remediation(expected: str) -> str:
    return (
        f"Sign in to that tenant (az login --tenant {expected}), or set "
        f"AZURE_TENANT_ID={expected} for a service principal or workload identity; "
        "a managed identity gets tokens only from its own tenant"
    )


class _TenantPinnedCredential:
    """A ``TokenCredential`` that only returns tokens for one tenant.

    Built by :func:`pin_credential`; see the module docstring.
    """

    def __init__(self, credential: TokenCredential, tenant_id: str) -> None:
        self._inner = credential
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        """The pinned tenant."""
        return self._tenant_id

    @property
    def inner_credential(self) -> TokenCredential:
        """The credential the tokens come from."""
        return self._inner

    def _credential_name(self) -> str:
        return type(self._inner).__name__

    def _refuse_other_tenant(self, requested: str | None, scopes: tuple[str, ...]) -> None:
        if requested is None or same_guid(requested, self._tenant_id):
            return
        scope = " ".join(scopes)
        raise TenantMismatchError(
            f"refused a request for a token from tenant {requested} for {scope}: the "
            f"credential ({self._credential_name()}) is pinned to tenant {self._tenant_id}",
            expected_tenant=self._tenant_id,
            token_tenant=requested,
            credential_used=self._credential_name(),
            scope=scope,
            remediation=f"Request tokens for tenant {self._tenant_id}, or pin that tenant.",
        )

    def _check_tid(self, token: str, scopes: tuple[str, ...]) -> None:
        scope = " ".join(scopes)
        tid = decode_token_claims(token).get("tid") if isinstance(token, str) else None
        if not isinstance(tid, str) or not tid.strip():
            raise TenantMismatchError(
                f"refused a token for {scope}: its tenant could not be read (no tid "
                f"claim), so it is not confirmed as the pinned tenant {self._tenant_id} "
                f"(credential {self._credential_name()})",
                expected_tenant=self._tenant_id,
                token_tenant=UNREADABLE_TENANT,
                credential_used=self._credential_name(),
                scope=scope,
                remediation=_remediation(self._tenant_id),
            )
        if not same_guid(tid, self._tenant_id):
            raise TenantMismatchError(
                f"refused a token for {scope}: it is for tenant {tid.strip()}, not the "
                f"pinned tenant {self._tenant_id} (credential {self._credential_name()})",
                expected_tenant=self._tenant_id,
                token_tenant=tid.strip(),
                credential_used=self._credential_name(),
                scope=scope,
                remediation=_remediation(self._tenant_id),
            )

    def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: Any,
    ) -> AccessToken:
        """Request a token from the pinned tenant and check its ``tid`` claim."""
        self._refuse_other_tenant(tenant_id, scopes)
        if claims is not None:
            kwargs["claims"] = claims
        if enable_cae:
            kwargs["enable_cae"] = enable_cae
        token = self._inner.get_token(*scopes, tenant_id=self._tenant_id, **kwargs)
        self._check_tid(token.token, scopes)
        return token

    def close(self) -> None:
        """Close the wrapped credential, if it can be closed."""
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> _TenantPinnedCredential:
        enter = getattr(self._inner, "__enter__", None)
        if callable(enter):
            enter()
        return self

    def __exit__(self, *args: Any) -> None:
        exit_ = getattr(self._inner, "__exit__", None)
        if callable(exit_):
            exit_(*args)


class _TenantPinnedTokenInfoCredential(_TenantPinnedCredential):
    """:class:`_TenantPinnedCredential` for a credential that has ``get_token_info``."""

    def get_token_info(
        self, *scopes: str, options: TokenRequestOptions | None = None
    ) -> AccessTokenInfo:
        """Request a token from the pinned tenant and check its ``tid`` claim."""
        requested = (options or {}).get("tenant_id")
        self._refuse_other_tenant(requested, scopes)
        pinned_options: TokenRequestOptions = {**(options or {}), "tenant_id": self._tenant_id}
        info = self._inner.get_token_info(*scopes, options=pinned_options)  # type: ignore[attr-defined]
        self._check_tid(info.token, scopes)
        return info  # type: ignore[no-any-return]


def pin_credential(credential: TokenCredential, tenant_id: str) -> TokenCredential:
    """Wrap ``credential`` so that it only returns tokens for ``tenant_id``.

    ``tenant_id`` must already have passed :func:`require_tenant_guid`. The
    wrapper has ``get_token_info`` only when ``credential`` has it, so code
    that chooses between the two methods by looking for ``get_token_info``
    chooses as it did before.
    """
    if hasattr(credential, "get_token_info"):
        return _TenantPinnedTokenInfoCredential(credential, tenant_id)
    return _TenantPinnedCredential(credential, tenant_id)


__all__ = [
    "UNREADABLE_TENANT",
    "normalize_tenant_id",
    "pin_credential",
    "require_tenant_guid",
    "same_guid",
]
