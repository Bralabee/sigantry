"""GithubSecretsSecretStore -- GitHub Actions secrets SecretStore impl (Plan 16-02).

Composes :class:`sigantry_core.client.base.BaseRestClient` for HTTP
traffic (no new ``import httpx`` exception is added -- the existing
``sigantry_core/client/**`` carve-out covers the inherited transport).

PyNaCl import name vs. PyPI name (RESEARCH §Pitfall 7)
-------------------------------------------------------
The PyPI distribution name is ``PyNaCl`` (capital N). The importable
Python module is ``nacl`` (lowercase, no prefix). We import from the
``nacl.public`` and ``nacl.encoding`` submodules per GitHub's official
REST guide. Adding ``pynacl`` to ``pyproject.toml`` does NOT change
this -- pip is case-insensitive on the PyPI name but the import
statement uses the lowercase module name.

libsodium SealedBox (RESEARCH §Pitfall 2)
------------------------------------------
``set()`` follows GitHub's documented 3-step flow:

1. ``GET /repos/{owner}/{repo}/actions/secrets/public-key`` -- fetch the
   repo's libsodium public key + key_id.
2. SealedBox-encrypt the plaintext with the public key. SealedBox is
   libsodium's anonymous-sender public-key primitive: the encrypted blob
   is decryptable ONLY by the holder of the matching private key
   (GitHub's server). No nonce, no MAC, no key derivation on the client
   side -- SealedBox handles all of it. NEVER use ``SecretBox``
   (symmetric) by mistake.
3. ``PUT /repos/{owner}/{repo}/actions/secrets/{name}`` with body
   ``{"encrypted_value": <b64>, "key_id": <pubkey id>}``.

The public key is re-fetched on every ``set()`` -- caching would risk
encrypting against a rotated key (T-16-02-03). GitHub key rotation is
opaque to clients.

Read constraint (RESEARCH §Pitfall 5)
-------------------------------------
``get()`` raises :class:`SecretReadNotSupported`. The GitHub Actions
secrets REST API is metadata-only for the value -- ``GET /repos/.../
actions/secrets/{name}`` returns ``{name, created_at, updated_at}`` but
NEVER the value. The value is retrievable only at workflow runtime via
``${{ secrets.MY_SECRET }}`` substitution.

Audit-plane integration
-----------------------
``set`` and ``delete`` emit :class:`SecretChangeRecord` via
:func:`emit_secret_change_record` synchronously. ``get`` / ``list_keys``
/ ``ping`` do NOT emit (read operations are out-of-scope per
RESEARCH §Anti-Patterns).
"""

from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime
from typing import Any, Final

# RESEARCH §Pitfall 7: PyPI dist is `PyNaCl`; importable module is `nacl`.
# We import from `nacl.public` (PublicKey + SealedBox) and `nacl.encoding`
# (Base64Encoder) per GitHub's official REST guide.
from nacl import encoding, public

from sigantry_core.auth import TokenProviderProtocol
from sigantry_core.client.base import BaseRestClient
from sigantry_core.governance.audit import emit_secret_change_record
from sigantry_core.governance.records import SecretChangeRecord
from sigantry_core.secrets.errors import SecretReadNotSupported

_GITHUB_BASE: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION_HEADER: Final[str] = "2022-11-28"


class _PatTokenProvider:
    """Placeholder ``TokenProviderProtocol`` for PAT-based GitHub auth.

    Mirrors :class:`sigantry_core.workitems.github._NoopTokenProvider`:
    GitHub auth bypasses the OAuth-scope chain; the ``Authorization``
    header is supplied per-request via ``extra_headers``. This shim
    satisfies :class:`BaseRestClient`'s contract without wiring
    ``DefaultAzureCredential``.
    """

    tenant_id: str | None = None

    def __init__(self, pat: str) -> None:
        self._pat = pat

    def get_token(self, scope: str) -> str:
        # ``scope`` unused -- Authorization is supplied via _auth_headers.
        del scope
        return ""

    def last_credential_class(self, scope: str) -> str | None:
        del scope
        return "GitHub-PAT"


# Import-time conformance check (mirror of workitems/github.py review-fix MD-02).
assert isinstance(_PatTokenProvider("dummy"), TokenProviderProtocol), (
    "_PatTokenProvider drifted from TokenProviderProtocol; "
    "BaseRestClient may now call methods that this shim does not implement."
)


class GithubSecretsSecretStore:
    """GitHub Actions repo-secrets SecretStore reference impl.

    Constructor: ``(*, owner: str, repo: str, pat: str, http_client=None)``
    where ``owner`` / ``repo`` identify the GitHub repository (e.g.
    ``owner="example-org"``, ``repo="sigantry"``) and ``pat`` is a
    personal access token with the ``repo`` scope.

    Method semantics:

    - ``get(key)``: ALWAYS raises :class:`SecretReadNotSupported` --
      GitHub's API does not return secret values (RESEARCH §Pitfall 5).
    - ``set(key, value)``: 3-step flow (pubkey-fetch + SealedBox-encrypt
      + PUT); audit-record emit on success.
    - ``delete(key)``: DELETE on the repo-secret resource; audit-record
      emit on success.
    - ``list_keys(prefix)``: GET ``/repos/{owner}/{repo}/actions/secrets``
      and client-side prefix filter on the returned ``secrets[].name``
      list (the API has no server-side prefix query).
    - ``ping()``: GET on the secrets-list endpoint -- raises
      :class:`AuthError` on 401, :class:`HttpError` on other non-2xx via
      the BaseRestClient pipeline.
    """

    name: str = "github_secrets"

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        pat: str,
        http_client: Any | None = None,
        actor: str | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._pat = pat
        self._actor: str = actor or "GitHub-PAT"
        self._client = BaseRestClient(
            token_provider=_PatTokenProvider(pat),
            base_url=_GITHUB_BASE,
            default_scope="",
            http_client=http_client,
        )

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._pat}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION_HEADER,
        }

    # ---- SecretStore Protocol surface ---------------------------------

    def get(self, key: str) -> str | None:
        """ALWAYS raises :class:`SecretReadNotSupported` (RESEARCH §Pitfall 5).

        The GitHub Actions secrets REST API is metadata-only for the value;
        ``GET /repos/.../actions/secrets/{name}`` returns ``created_at`` /
        ``updated_at`` / ``name`` but the secret value is unreachable
        outside workflow-runtime ``${{ secrets.X }}`` substitution.
        """
        raise SecretReadNotSupported(
            f"GitHub Actions secrets API does not return values; key={key!r} "
            f"(RESEARCH §Pitfall 5). Use KeyVault or ADO variable groups for "
            "read-after-write semantics."
        )

    def set(self, key: str, value: str) -> None:
        # Step 1: GET the repo's libsodium public key.
        pk_response = self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}/actions/secrets/public-key",
            extra_headers=self._auth_headers(),
        )
        pk_payload = pk_response.json_body
        if not isinstance(pk_payload, dict):
            raise RuntimeError(
                f"GitHub public-key response was not a JSON object: {type(pk_payload).__name__}"
            )
        pubkey_b64 = pk_payload.get("key")
        key_id = pk_payload.get("key_id")
        if not isinstance(pubkey_b64, str) or not isinstance(key_id, str):
            raise RuntimeError("GitHub public-key response missing 'key' or 'key_id' fields")

        # Step 2: libsodium SealedBox-encrypt the plaintext (RESEARCH §Pitfall 2).
        # The 4-line snippet is verbatim from GitHub's official REST guide.
        public_key_obj = public.PublicKey(
            pubkey_b64.encode("utf-8"),
            encoding.Base64Encoder(),
        )
        sealed_box = public.SealedBox(public_key_obj)
        encrypted = sealed_box.encrypt(value.encode("utf-8"))
        encrypted_b64 = b64encode(encrypted).decode("utf-8")

        # Step 3: PUT the encrypted secret.
        self._client.send(
            "PUT",
            f"/repos/{self._owner}/{self._repo}/actions/secrets/{key}",
            json={"encrypted_value": encrypted_b64, "key_id": key_id},
            extra_headers=self._auth_headers(),
        )

        # Step 4: audit-record emit (NEVER include `value`).
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
        self._client.send(
            "DELETE",
            f"/repos/{self._owner}/{self._repo}/actions/secrets/{key}",
            extra_headers=self._auth_headers(),
        )
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
        resp = self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}/actions/secrets",
            extra_headers=self._auth_headers(),
        )
        body = resp.json_body if isinstance(resp.json_body, dict) else {}
        secrets = body.get("secrets", [])
        if not isinstance(secrets, list):
            return []
        return [
            s["name"]
            for s in secrets
            if isinstance(s, dict)
            and isinstance(s.get("name"), str)
            and s["name"].startswith(prefix)
        ]

    def ping(self) -> None:
        self._client.send(
            "GET",
            f"/repos/{self._owner}/{self._repo}/actions/secrets",
            extra_headers=self._auth_headers(),
        )


__all__ = ["GithubSecretsSecretStore"]
