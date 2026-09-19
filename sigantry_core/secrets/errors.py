"""SecretStore exception types (Plan 16-02).

The only exception in this module today is :class:`SecretReadNotSupported`
-- raised by :class:`GithubSecretsSecretStore.get` because the GitHub
Actions secrets REST API is metadata-only (see RESEARCH §Pitfall 5):
``GET /repos/.../actions/secrets/{name}`` returns ``created_at`` /
``updated_at`` / ``name`` but NEVER the value. The value is retrievable
only at workflow runtime via ``${{ secrets.MY_SECRET }}`` substitution.

The contract test for ``SecretStore`` (``test_secret_store_contract.py``)
treats ``SecretReadNotSupported`` as a documented behaviour, not a bug --
``get()`` may either return ``str | None`` OR raise this exception. Any
other behaviour is a contract break.
"""

from __future__ import annotations


class SecretReadNotSupported(Exception):  # noqa: N818
    # Plan 16-02 + RESEARCH §Pitfall 5 fix the name as ``SecretReadNotSupported``
    # (NOT ``SecretReadNotSupportedError``). Adopters catch this typed
    # exception explicitly; renaming to fit the *Error suffix convention
    # would break the documented contract surface (the contract test
    # imports the name directly).
    """Raised when a SecretStore impl cannot return the secret value via ``get``.

    Documented backends with this constraint:

    - :class:`sigantry_core.secrets.github_secrets.GithubSecretsSecretStore`
      (RESEARCH §Pitfall 5; ``GET /repos/.../actions/secrets/{name}`` is
      metadata-only by GitHub's API design).

    Adopters that need read-after-write should use
    :class:`sigantry_core.secrets.key_vault.KeyVaultSecretStore` or
    :class:`sigantry_core.secrets.ado_variable_group.AdoVariableGroupSecretStore`
    -- both support symmetric ``set`` / ``get`` round-trips.
    """


__all__ = ["SecretReadNotSupported"]
