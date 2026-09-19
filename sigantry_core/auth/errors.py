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


class TenantSettingError(FabricAuthError):
    """Raised when token works but the Fabric API is not enabled for the SPN (403)."""


class GroupMembershipError(FabricAuthError):
    """Raised when the calling principal is not a member of sg-fabric-automation."""


class KeyVaultResolutionError(FabricAuthError):
    """Raised when a kv://<vault>/<name> URI cannot be resolved."""
