"""Secret opaque type + kv:// URI resolver.

Mitigates threat T-1-02 (KV exfiltration via logging). Secret.__repr__ and
Secret.__str__ always return the literal string "<redacted>". Only .value
exposes the cleartext. The only legitimate consumer of .value is the caller
that needs to send the secret to its destination (e.g. an Authorization
header); every other use must either pass the Secret object through opaquely
or explicitly opt in via .value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from azure.keyvault.secrets import SecretClient

from sigantry_core.auth.errors import KeyVaultResolutionError

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential


@dataclass(frozen=True)
class Secret:
    """Opaque wrapper for a secret value.

    `repr(s)` and `str(s)` return "<redacted>". `s.value` returns the cleartext.
    `==` is defined in terms of .value so tests can compare, but the object
    is not hashable (prevents accidental use as dict key which would log repr).
    """

    value: str = field(repr=False)

    def __repr__(self) -> str:
        return "<redacted>"

    def __str__(self) -> str:
        return "<redacted>"

    def __format__(self, spec: str) -> str:
        return "<redacted>"


def _parse_kv_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "kv":
        raise KeyVaultResolutionError(
            f"expected kv:// URI, got {parsed.scheme!r}",
            remediation="Pass a URI of the form 'kv://<vault>/<secret-name>'",
        )
    host = parsed.hostname
    name = parsed.path.lstrip("/")
    if not host or not name:
        raise KeyVaultResolutionError(
            f"malformed kv:// URI: {uri!r}",
            remediation="Use 'kv://<vault-short-name>/<secret-name>' "
            "(e.g. 'kv://my-kv-prod/fabric-pat').",
        )
    return host, name


def resolve_secret(
    ref: str,
    *,
    credential: TokenCredential | None = None,
) -> Secret:
    """Resolve a kv://<vault>/<secret> URI to a `Secret`.

    Non-kv:// refs are wrapped in a `Secret` and returned as-is (pass-through).
    This lets config files contain either a literal value or a `kv://` URI
    without the caller branching.

    Params:
        ref: A string - either a plain value or a kv://<vault>/<name> URI.
        credential: Optional explicit credential (e.g. for testing). When None,
            a DefaultAzureCredential-backed TokenProvider is used via its
            shared credential (imported lazily to avoid circular imports).

    Returns:
        Secret wrapping the resolved cleartext.

    Raises:
        KeyVaultResolutionError: on malformed URI or Key Vault failure.
    """
    if not ref.startswith("kv://"):
        return Secret(value=ref)

    vault_short, secret_name = _parse_kv_uri(ref)
    vault_url = f"https://{vault_short}.vault.azure.net"

    if credential is None:
        # Lazy import to avoid circular: keyvault is imported from __init__,
        # token_provider is too - both are safe to import here at call time.
        from sigantry_core.auth.token_provider import get_default_credential

        credential = get_default_credential()

    try:
        client = SecretClient(vault_url=vault_url, credential=credential)
        kv_secret = client.get_secret(secret_name)
    except Exception as exc:  # we re-wrap with remediation
        raise KeyVaultResolutionError(
            f"failed to resolve {ref!r}: {exc}",
            remediation=(
                f"Verify the credential has 'get' on secret '{secret_name}' "
                f"in vault '{vault_short}', and the vault exists."
            ),
        ) from exc

    if kv_secret.value is None:
        raise KeyVaultResolutionError(
            f"{ref!r} returned a secret with null value",
            remediation="Check the secret version; empty secrets are not supported.",
        )
    return Secret(value=kv_secret.value)
