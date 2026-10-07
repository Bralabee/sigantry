"""TokenProvider - single entry point for every DefaultAzureCredential call.

One chain, three runtimes:
- Fabric notebook -> ManagedIdentityCredential slot
- ADO pipeline   -> EnvironmentCredential (AZURE_FEDERATED_TOKEN_FILE) or WorkloadIdentityCredential
- Laptop         -> AzureCliCredential

Excluded slots (per CLAUDE.md + 01-RESEARCH.md Pattern 2):
- InteractiveBrowserCredential (breaks pipelines)
- VisualStudioCodeCredential  (noisy in CI)

EnvironmentCredential is NOT excluded - ADO WIF relies on it when the
`AzureCLI@2` / `AzurePowerShell@5` tasks set AZURE_FEDERATED_TOKEN_FILE.

Tenant pinning: with ``tenant_id`` set, the credential the provider holds and
hands out (``credential``, ``get_credential()``) is the pinned credential of
:mod:`sigantry_core.auth.tenant`. It requests every token from that tenant and
refuses, with :class:`~sigantry_core.auth.errors.TenantMismatchError`, a token
whose ``tid`` claim names another tenant or cannot be read. A ``tenant_id``
that is not a tenant ID GUID raises
:class:`~sigantry_core.auth.errors.InvalidTenantIdError` at construction.
Without ``tenant_id`` the credential is used as it is.

Thread-safe, in-process cache. No persistent storage (Pitfall 12).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from azure.identity import DefaultAzureCredential

from sigantry_core.auth.audiences import (
    AZURE_RM_SCOPE,
    FABRIC_SCOPE,
    GRAPH_SCOPE,
    POWERBI_SCOPE,
    PURVIEW_SCOPE,
)
from sigantry_core.auth.errors import TenantMismatchError, TokenAcquisitionError
from sigantry_core.auth.tenant import normalize_tenant_id, pin_credential, require_tenant_guid

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CachedToken:
    access_token: str
    expires_on: int
    credential_class: str


@runtime_checkable
class TokenProviderProtocol(Protocol):
    """Structural contract for anything ``BaseRestClient`` calls as its auth seam.

    ``BaseRestClient`` only invokes two methods on the injected provider:

    - ``get_token(scope: str) -> str``        -- inside ``_build_headers``.
    - ``last_credential_class(scope: str) -> str | None`` -- inside
      ``_log_first_credential``.

    The concrete ``TokenProvider`` class satisfies this Protocol structurally;
    so does ``sigantry_core.workitems.github._NoopTokenProvider`` (the
    GitHub-auth carve-out where Authorization is supplied per-request via
    ``extra_headers``). Both pass an ``isinstance(obj, TokenProviderProtocol)``
    check at runtime because the Protocol is ``@runtime_checkable``.

    The Protocol exists so ``BaseRestClient.__init__`` can accept either
    shape without a ``# type: ignore[arg-type]`` -- review-fix MD-02.
    Adding a method to ``BaseRestClient`` that calls a NEW token-provider
    method should happen by extending this Protocol first; mypy then flags
    every call site whose provider does not implement the new method,
    surfacing the breakage at type-check time rather than at runtime.
    """

    def get_token(self, scope: str) -> str: ...

    def last_credential_class(self, scope: str) -> str | None: ...


class TokenProvider:
    """Thread-safe in-process token cache backed by DefaultAzureCredential.

    With ``tenant_id``, every token comes from that tenant or is refused (see
    the module docstring); this holds for an injected ``credential`` too.
    """

    _SKEW_SECONDS = 300  # refresh when remaining lifetime < 5 minutes

    def __init__(
        self,
        credential: TokenCredential | None = None,
        *,
        tenant_id: str | None = None,
    ) -> None:
        # Checked before any credential is built, so a value that can never
        # match a token's tid claim is refused before a token is requested.
        pinned = require_tenant_guid(tenant_id) if tenant_id is not None else None
        if credential is None:
            kwargs: dict[str, Any] = {
                "exclude_interactive_browser_credential": True,
                "exclude_visual_studio_code_credential": True,
            }
            if pinned is not None:
                # A credential configured for one tenant (AZURE_TENANT_ID)
                # refuses a request for another unless that one is allowed.
                kwargs["additionally_allowed_tenants"] = [pinned]
            credential = DefaultAzureCredential(**kwargs)
        self._inner_credential = credential
        # Pitfall P1-6: the pin is enforced by the credential itself, so it
        # also holds for callers that take the credential (fabric-cicd).
        self._credential = credential if pinned is None else pin_credential(credential, pinned)
        self._tenant_id = pinned
        self._cache: dict[str, _CachedToken] = {}
        self._lock = threading.Lock()

    @property
    def credential(self) -> TokenCredential:
        return self._credential

    @property
    def tenant_id(self) -> str | None:
        return self._tenant_id

    def get_credential(self) -> TokenCredential:
        """Return the TokenCredential the provider gets its tokens from.

        Required by `fabric_cicd.FabricWorkspace(token_credential=...)` (1.0.0
        breaking change). Callers that need the credential object (rather
        than a bearer token via `get_token`) should use this entry point — it
        mirrors the `credential` property as a method so callers can pass
        `token_provider.get_credential` as a factory. With a pinned tenant it
        is the pinned credential, so a caller that requests tokens itself
        gets the same pin.
        """
        return self._credential

    @classmethod
    def from_defaults(cls, *, tenant_id: str | None = None) -> TokenProvider:
        """Construct a TokenProvider backed by the default credential chain.

        Mirrors the `.from_defaults()` factory pattern used across Phase 2/3
        (e.g. `FabricRestClient.from_defaults`). Equivalent to
        ``TokenProvider(tenant_id=tenant_id)`` — default-chain construction.
        """
        return cls(tenant_id=tenant_id)

    def get_token(self, scope: str) -> str:
        """Return a cleartext bearer token for the given scope.

        Raises:
            TokenAcquisitionError: on chain failure.
        """
        with self._lock:
            cached = self._cache.get(scope)
            if cached and cached.expires_on - time.time() > self._SKEW_SECONDS:
                return cached.access_token

            try:
                tok = self._credential.get_token(scope)
            except TenantMismatchError:
                raise
            except Exception as exc:  # wrap + re-raise
                raise TokenAcquisitionError(
                    f"chain failed to acquire token for scope {scope!r}: {exc}",
                    scope=scope,
                    credential_used=type(self._inner_credential).__name__,
                    remediation=(
                        "Check that az login is active (laptop) OR "
                        "AZURE_FEDERATED_TOKEN_FILE is set (ADO pipeline) OR "
                        "IMDS is reachable (Fabric notebook). "
                        "Run `diagnose-auth` for a chain report."
                    ),
                ) from exc

            credential_class = type(self._inner_credential).__name__
            self._cache[scope] = _CachedToken(
                access_token=tok.token,
                expires_on=tok.expires_on,
                credential_class=credential_class,
            )
            logger.info(
                "token_acquired",
                extra={
                    "scope": scope,
                    "credential": credential_class,
                    "expires_on": tok.expires_on,
                    "tenant_id": self._tenant_id,
                },
            )
            return tok.token

    def last_credential_class(self, scope: str) -> str | None:
        """Class name of the credential that produced the currently-cached token."""
        with self._lock:
            cached = self._cache.get(scope)
            return cached.credential_class if cached else None


# ---- Module-level process-wide tenant-keyed pool ------------------------
#
# Audit-2026-05-07 W1.4: prior to remediation a single ``_default_provider``
# was latched on first call; subsequent ``get_token_provider(tenant_id="B")``
# silently returned the provider that had been constructed for tenant A. Any
# multi-tenant orchestrator (e.g. a CI job auditing two customer tenants)
# would target the wrong tenant on the second invocation. The pool below
# keys per ``tenant_id``; ``None`` is preserved as a distinct key so the
# default-credential-chain provider remains addressable.

_default_providers: dict[str | None, TokenProvider] = {}
_provider_lock = threading.Lock()


def get_token_provider(tenant_id: str | None = None) -> TokenProvider:
    """Return the per-tenant TokenProvider. Thread-safe.

    Each distinct ``tenant_id`` (including ``None``) maps to its own
    singleton TokenProvider with an isolated token cache. Re-invoking with
    the same ``tenant_id`` returns the same instance, preserving cache
    semantics within a tenant. Tenant ids are keyed stripped and
    case-folded, so two spellings of one tenant share a provider; a
    ``tenant_id`` that is not a tenant ID GUID raises
    :class:`~sigantry_core.auth.errors.InvalidTenantIdError`.
    """
    key = None if tenant_id is None else normalize_tenant_id(require_tenant_guid(tenant_id))
    with _provider_lock:
        provider = _default_providers.get(key)
        if provider is None:
            provider = TokenProvider(tenant_id=tenant_id)
            _default_providers[key] = provider
        return provider


def reset_token_provider() -> None:
    """Reset the process-wide tenant pool. Test-only - do not call in production."""
    with _provider_lock:
        _default_providers.clear()


def get_default_credential() -> TokenCredential:
    """Return the shared DefaultAzureCredential backing the process singleton.

    Used by `sigantry_core.auth.keyvault.resolve_secret` to avoid spawning
    a second credential chain.
    """
    return get_token_provider().credential


# ---- Convenience wrappers per scope -------------------------------------


def get_token(scope: str) -> str:
    return get_token_provider().get_token(scope)


def get_fabric_token() -> str:
    return get_token_provider().get_token(FABRIC_SCOPE)


def get_powerbi_token() -> str:
    return get_token_provider().get_token(POWERBI_SCOPE)


def get_graph_token() -> str:
    return get_token_provider().get_token(GRAPH_SCOPE)


def get_purview_token() -> str:
    return get_token_provider().get_token(PURVIEW_SCOPE)


def get_azure_rm_token() -> str:
    return get_token_provider().get_token(AZURE_RM_SCOPE)
