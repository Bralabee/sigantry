"""AdoVariableGroupSecretStore -- ADO variable-group SecretStore impl (Plan 16-02).

Composes :class:`sigantry_core.client.base.BaseRestClient` for HTTP
traffic (no new ``import httpx`` exception is added -- the existing
``sigantry_core/client/**`` carve-out covers the inherited transport).

Read-modify-write semantics (RESEARCH §Pitfall 3)
-------------------------------------------------
ADO's REST API for variable groups is **full-resource replace** on PUT:
``PUT /_apis/distributedtask/variablegroups/{groupId}?api-version=7.1``
treats the body as the complete new state. Sending only
``{variables: {foo: {value: "bar"}}}`` would DELETE every other variable
in the group.

Therefore ``set`` and ``delete`` are 3-step flows:

1. ``GET /{org}/_apis/distributedtask/variablegroups/{groupId}?api-version=7.1``
   -- fetch the current full body.
2. Mutate the in-memory dict:
   - ``set``: ``body["variables"][key] = {"value": new_value, "isSecret": True}``
   - ``delete``: ``del body["variables"][key]`` (no-op if absent)
3. ``PUT /{org}/_apis/distributedtask/variablegroups/{groupId}?api-version=7.1``
   -- write back the FULL body, not just the diff.

The ``isSecret: True`` flag asks ADO to encrypt the value at rest server-
side; the client never sees the encrypted bytes. NEVER hand-encrypt the
value before PUT (that would be double encryption).

Auth
----
Uses the standard :class:`sigantry_core.auth.TokenProvider` chain against
``AZURE_DEVOPS_SCOPE`` (mirror :class:`AdoWorkItemProvider` from Phase 11).
PAT auth is NOT supported in v3.0 -- Microsoft fully deprecated ADO PAT-
equivalent OAuth apps in 2026.

Audit-plane integration
-----------------------
``set`` and ``delete`` emit :class:`SecretChangeRecord` via
:func:`emit_secret_change_record` synchronously after the PUT succeeds.
``get`` / ``list_keys`` / ``ping`` do NOT emit (read operations are
out-of-scope per RESEARCH §Anti-Patterns).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from sigantry_core.auth import TokenProvider
from sigantry_core.auth.audiences import AZURE_DEVOPS_SCOPE
from sigantry_core.client.base import BaseRestClient
from sigantry_core.governance.audit import emit_secret_change_record
from sigantry_core.governance.records import SecretChangeRecord

_API_VERSION: Final[str] = "7.1"


class AdoVariableGroupSecretStore:
    """Azure DevOps variable-group SecretStore reference impl.

    Constructor: ``(*, organization: str, project: str, group_id: int,
    token_provider: TokenProvider, http_client=None)`` where
    ``organization`` is the ADO org slug (e.g.
    ``"contoso-dataops"``), ``project`` is the project name, and
    ``group_id`` is the integer variable-group id (visible in the ADO
    UI URL).

    Method semantics (read-modify-write per RESEARCH §Pitfall 3):

    - ``get(key)``: GET the group; return ``str | None``. Secret values
      that are flagged ``isSecret: true`` come back as ``null`` from
      ADO's API by design (the secret value is server-side-only after
      first PUT); we still honour the Protocol shape and return
      ``None`` in that case so ``get`` is symmetric with absence.
    - ``set(key, value)``: GET full body, mutate one var with
      ``isSecret: True``, PUT full body, emit audit record.
    - ``delete(key)``: GET full body, ``del body["variables"][key]``,
      PUT full body, emit audit record.
    - ``list_keys(prefix)``: GET full body, return prefix-filtered key
      list.
    - ``ping()``: GET on the group endpoint -- raises on auth/network
      failure via the BaseRestClient pipeline.
    """

    name: str = "ado_variable_group"

    def __init__(
        self,
        *,
        organization: str,
        project: str,
        group_id: int,
        token_provider: TokenProvider,
        http_client: Any | None = None,
        actor: str | None = None,
    ) -> None:
        self._organization = organization
        self._project = project
        self._group_id = group_id
        self._actor = actor or self._infer_actor(token_provider)
        self._client = BaseRestClient(
            token_provider=token_provider,
            base_url=f"https://dev.azure.com/{organization}",
            default_scope=AZURE_DEVOPS_SCOPE,
            http_client=http_client,
        )

    @staticmethod
    def _infer_actor(token_provider: TokenProvider) -> str:
        """Best-effort credential-class inference (mirror destructive_op._infer_principal)."""
        cred = token_provider.last_credential_class("probe")
        return cred or "unknown"

    def _path(self) -> str:
        return f"/{self._project}/_apis/distributedtask/variablegroups/{self._group_id}"

    # ---- internal: GET full body for read-modify-write ----------------

    def _get_group_body(self) -> dict[str, Any]:
        resp = self._client.send(
            "GET",
            self._path(),
            params={"api-version": _API_VERSION},
        )
        body = resp.json_body
        if not isinstance(body, dict):
            raise RuntimeError(
                f"ADO variable-group GET returned non-dict body: {type(body).__name__}"
            )
        # Normalise: the variables map may be missing on a freshly created group.
        if "variables" not in body or not isinstance(body.get("variables"), dict):
            body["variables"] = {}
        return body

    def _put_group_body(self, body: dict[str, Any]) -> None:
        self._client.send(
            "PUT",
            self._path(),
            params={"api-version": _API_VERSION},
            json=body,
        )

    # ---- SecretStore Protocol surface ---------------------------------

    def get(self, key: str) -> str | None:
        body = self._get_group_body()
        var = body["variables"].get(key)
        if not isinstance(var, dict):
            return None
        # ADO returns secret values as None (the server-side-only invariant);
        # plain (non-secret) variables come back with their literal value.
        value = var.get("value")
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        body = self._get_group_body()
        body["variables"][key] = {"value": value, "isSecret": True}
        self._put_group_body(body)
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
        body = self._get_group_body()
        # No-op if the key is absent -- still PUT the body so the audit record
        # reflects the intent and remains symmetric across providers.
        body["variables"].pop(key, None)
        self._put_group_body(body)
        emit_secret_change_record(
            SecretChangeRecord(
                operation="delete",
                key=key,
                store_name=self.name,
                actor=self._actor,
                timestamp=datetime.now(tz=UTC),
            ).with_hash()
        )

    def list_keys(self, prefix: str = "") -> list[str]:
        body = self._get_group_body()
        return [k for k in body["variables"] if isinstance(k, str) and k.startswith(prefix)]

    def ping(self) -> None:
        self._client.send(
            "GET",
            self._path(),
            params={"api-version": _API_VERSION},
        )


__all__ = ["AdoVariableGroupSecretStore"]
