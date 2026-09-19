"""Phase 16 SecretStore reference implementations (Plan 16-02).

Three first-class reference implementations of the
:class:`sigantry_core.protocols.SecretStore` Protocol seam:

- :class:`KeyVaultSecretStore` -- Azure Key Vault via
  ``azure.keyvault.secrets.SecretClient`` (Azure SDK; not BaseRestClient).
- :class:`GithubSecretsSecretStore` -- GitHub Actions repo secrets via
  the BaseRestClient subclass pattern + libsodium SealedBox encryption
  via PyNaCl. ``get()`` raises :class:`SecretReadNotSupported` because
  the GitHub Actions secrets REST API is metadata-only for the value
  (RESEARCH §Pitfall 5).
- :class:`AdoVariableGroupSecretStore` -- Azure DevOps variable group
  via the BaseRestClient subclass pattern + read-modify-write semantics
  (RESEARCH §Pitfall 3). ``set`` / ``delete`` GET the full group body,
  mutate one variable, and PUT the full body back so existing variables
  are preserved.

All three impls emit a :class:`sigantry_core.governance.records.SecretChangeRecord`
via :func:`sigantry_core.governance.audit.emit_secret_change_record`
synchronously on every ``set`` / ``delete`` call. The audit record
NEVER carries the secret value -- only metadata + hash.
"""

from sigantry_core.secrets.ado_variable_group import AdoVariableGroupSecretStore
from sigantry_core.secrets.errors import SecretReadNotSupported
from sigantry_core.secrets.github_secrets import GithubSecretsSecretStore
from sigantry_core.secrets.key_vault import KeyVaultSecretStore

__all__ = [
    "AdoVariableGroupSecretStore",
    "GithubSecretsSecretStore",
    "KeyVaultSecretStore",
    "SecretReadNotSupported",
]
