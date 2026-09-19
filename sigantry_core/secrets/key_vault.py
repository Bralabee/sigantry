"""KeyVaultSecretStore -- Azure Key Vault SecretStore reference impl (Plan 16-02).

Composes :class:`azure.keyvault.secrets.SecretClient` (NOT
``BaseRestClient``). The Azure Key Vault SDK uses ``azure-core`` as its
HTTP transport rather than ``httpx``, so this module's banned-API status
is unaffected -- it does not require an entry in
``[tool.ruff.lint.per-file-ignores]``.

Per RESEARCH §Pattern 2 the SDK route is preferred over a hand-rolled
REST loop because the SDK encapsulates:

- 3-leg challenge auth (Key Vault returns ``WWW-Authenticate`` with the
  resource URI; the SDK re-authenticates against the right scope on the
  fly).
- Soft-delete LROPoller (``begin_delete_secret(name).wait()``) -- a hand-
  rolled REST caller would have to poll the soft-delete operation URL
  with backoff and detect terminal state itself.
- Vault-URI parsing.

Every mutating call (``set`` / ``delete``) emits a
:class:`SecretChangeRecord` via :func:`emit_secret_change_record` so the
audit plane sees every change synchronously. ``get`` / ``list_keys`` /
``ping`` do NOT emit (read operations are out-of-scope for the audit
plane per RESEARCH §Anti-Patterns).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from sigantry_core.governance.audit import emit_secret_change_record
from sigantry_core.governance.records import SecretChangeRecord
from sigantry_core.secrets.errors import SecretReadNotSupported  # noqa: F401  (re-export anchor)

if TYPE_CHECKING:
    pass


def _principal_from_credential(credential: Any) -> str:
    """Best-effort principal inference from a credential object.

    Mirrors the spirit of ``destructive_op._infer_principal`` (audit.py):
    we record the credential class name (e.g.
    ``"DefaultAzureCredential"``) as the actor, never the token bytes.
    """
    if credential is None:
        return "unknown"
    return type(credential).__name__


class KeyVaultSecretStore:
    """Azure Key Vault SecretStore reference impl.

    Constructor accepts an explicit ``vault_url`` (e.g.
    ``"https://my-vault.vault.azure.net/"``) and an optional
    ``credential`` (defaults to ``DefaultAzureCredential()`` -- the
    Phase 1 contract for every Sigantry SDK consumer).

    Method semantics:

    - ``get(key)``: returns ``str | None`` -- ``None`` when the key is
      absent (caught :class:`ResourceNotFoundError`). Other Azure-side
      errors propagate.
    - ``set(key, value)``: synchronous SDK call + audit-record emit.
    - ``delete(key)``: ``begin_delete_secret(key).wait()`` LROPoller
      blocks until the secret is soft-deleted; audit-record emit on
      success.
    - ``list_keys(prefix)``: paginates ``list_properties_of_secrets`` and
      filters by name prefix (client-side; KeyVault has no server-side
      prefix query).
    - ``ping()``: cheapest reachability check -- ``next(iter(
      list_properties_of_secrets()), None)`` raises on auth/network
      failure and is a no-op on success.
    """

    name: str = "key_vault"

    def __init__(
        self,
        *,
        vault_url: str,
        credential: Any | None = None,
        actor: str | None = None,
    ) -> None:
        cred = credential if credential is not None else DefaultAzureCredential()
        self._client = SecretClient(vault_url=vault_url, credential=cred)
        self._vault_url = vault_url
        self._credential = cred
        self._actor: str = actor or _principal_from_credential(cred)

    # ---- SecretStore Protocol surface ---------------------------------

    def get(self, key: str) -> str | None:
        try:
            secret = self._client.get_secret(key)
        except ResourceNotFoundError:
            return None
        # SecretClient.get_secret returns a KeyVaultSecret with .value : str | None.
        value = getattr(secret, "value", None)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        try:
            self._client.set_secret(key, value)
        except BaseException as exc:
            # Audit-2026-05-08 review follow-up (WR-06): an SDK exception
            # mid-set (network blip, permission denied, conflict on a
            # soft-deleted name) may leave intermediate state server-side
            # AND skips the audit record below. Pre-fix the operator had
            # NO audit signal that an attempt happened. Emit a structured
            # WARNING with the same fields the JSONL would have carried
            # so triage off the operator's structured-logging pipeline
            # (journald / Log Analytics / Datadog) has somewhere to
            # land. SecretChangeRecord lacks an ``outcome`` field today
            # (adding one is a schema bump that invalidates every
            # prev_hash chain on existing ledgers; deferred to v3.x);
            # the structured-logger event carries ``outcome=failed`` +
            # ``exc_type`` so the gap is closed at the operator-
            # observability layer even though the JSONL stays canonical.
            self._failure_logger().warning(
                "secret_change_failed",
                extra={
                    "event": "secret_change_failed",
                    "operation": "set",
                    "key": key,
                    "store_name": self.name,
                    "actor": self._actor,
                    "outcome": "failed",
                    "exc_type": type(exc).__name__,
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                },
            )
            raise
        emit_secret_change_record(
            SecretChangeRecord(
                operation="set",
                key=key,
                store_name=self.name,
                actor=self._actor,
                timestamp=datetime.now(tz=UTC),
            ).with_hash()
        )

    def delete(self, key: str) -> None:
        # begin_delete_secret returns LROPoller; .wait() blocks until soft-deleted.
        try:
            self._client.begin_delete_secret(key).wait()
        except BaseException as exc:
            # Audit-2026-05-08 review follow-up (WR-06): symmetric with
            # ``set`` -- an SDK exception mid-delete may leave the secret
            # in a soft-deleted-but-not-purged state (or roll the call
            # back entirely). Pre-fix this skipped the audit record
            # below; post-fix the failure surfaces as a structured
            # WARNING with the same payload shape the JSONL would have
            # carried, so the operator has a triage signal even when
            # the JSONL ledger is silent (deferred ``outcome`` schema
            # field documented above).
            self._failure_logger().warning(
                "secret_change_failed",
                extra={
                    "event": "secret_change_failed",
                    "operation": "delete",
                    "key": key,
                    "store_name": self.name,
                    "actor": self._actor,
                    "outcome": "failed",
                    "exc_type": type(exc).__name__,
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                },
            )
            raise
        emit_secret_change_record(
            SecretChangeRecord(
                operation="delete",
                key=key,
                store_name=self.name,
                actor=self._actor,
                timestamp=datetime.now(tz=UTC),
            ).with_hash()
        )

    @staticmethod
    def _failure_logger() -> logging.Logger:
        """Return the audit-plane logger for the WR-06 failure-event signal.

        Reuses ``sigantry_core.governance.audit`` so the WARNING lands on
        the same channel as the structured-logger events emitted by
        ``emit_secret_change_record`` -- operators triaging the audit
        plane only need to subscribe to one logger name.
        """
        return logging.getLogger("sigantry_core.governance.audit")

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for sp in self._client.list_properties_of_secrets():
            name = getattr(sp, "name", None)
            if isinstance(name, str) and name.startswith(prefix):
                keys.append(name)
        return keys

    def ping(self) -> None:
        # Cheapest reachability check: list one page; raises on auth/network failure.
        next(iter(self._client.list_properties_of_secrets()), None)


__all__ = ["KeyVaultSecretStore"]
