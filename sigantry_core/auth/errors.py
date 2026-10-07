"""Exception hierarchy for sigantry_core.auth.

Mitigates Pitfall 1 (401 vs 403 distinction). Every error carries the triple
(credential_used, scope, remediation) so a caller can produce an actionable
log line without re-probing.
"""

from __future__ import annotations


class FabricAuthError(Exception):
    """Base class for every auth-layer error."""

    def __init__(
        self,
        message: str,
        *,
        credential_used: str | None = None,
        scope: str | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.credential_used = credential_used
        self.scope = scope
        self.remediation = remediation

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"{self.__class__.__name__}({super().__str__()!r}, "
            f"credential_used={self.credential_used!r}, "
            f"scope={self.scope!r}, "
            f"remediation={self.remediation!r})"
        )


class TokenAcquisitionError(FabricAuthError):
    """Raised when no credential in the chain returned a usable token (3xx exit)."""


class TenantMismatchError(TokenAcquisitionError):
    """A token was refused because it is not for the pinned tenant.

    Raised by the credential a tenant-pinned
    :class:`~sigantry_core.auth.token_provider.TokenProvider` holds, when the
    token's ``tid`` claim names another tenant, when the token carries no
    readable ``tid`` claim (``token_tenant`` is then ``"unreadable"``), or when
    a caller asks that credential for a token from a different tenant
    (``token_tenant`` is then the tenant that was asked for).

    The message names both tenants, the credential class and the scope. It
    never contains the token.
    """

    def __init__(
        self,
        message: str,
        *,
        expected_tenant: str,
        token_tenant: str,
        credential_used: str | None = None,
        scope: str | None = None,
        remediation: str | None = None,
    ) -> None:
        super().__init__(
            message,
            credential_used=credential_used,
            scope=scope,
            remediation=remediation,
        )
        self.expected_tenant = expected_tenant
        self.token_tenant = token_tenant


class InvalidTenantIdError(FabricAuthError, ValueError):
    """A tenant to pin is not a directory (tenant) ID GUID.

    Raised before any token is requested. A ``ValueError`` as well, so callers
    that validate arguments with ``except ValueError`` see it.
    """


class TenantSettingError(FabricAuthError):
    """Raised when token works but the Fabric API is not enabled for the SPN (403)."""


class GroupMembershipError(FabricAuthError):
    """Raised when the calling principal is not a member of the expected Entra group."""


class KeyVaultResolutionError(FabricAuthError):
    """Raised when a kv://<vault>/<name> URI cannot be resolved."""
