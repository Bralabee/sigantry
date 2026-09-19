"""GithubSecretsSecretStore tests (Plan 16-02).

libsodium SealedBox encryption via pynacl per RESEARCH §Pitfall 2.
GitHub Actions secrets get raises SecretReadNotSupported per RESEARCH §Pitfall 5.

The SealedBox round-trip is PROVEN by generating a real keypair in the
test (``nacl.public.PrivateKey.generate()``), exposing only the public
half via the mocked GET response, capturing the PUT body, and decrypting
the captured ``encrypted_value`` back to the original plaintext using the
matching private key. This is the canonical way to verify the encryption
is correct -- a shape-only assertion would not catch a wrong-key-encoding
bug.
"""

from __future__ import annotations

import importlib
import json
from base64 import b64decode, b64encode
from pathlib import Path

import pytest

# Audit-2026-05-07 W1.10: pynacl is the libsodium SealedBox dependency
# this test exercises directly. Without it, ``from nacl import public``
# hard-fails at collection time. ``importorskip`` keeps the suite
# discoverable for contributors running pytest in a non-conda env.
pytest.importorskip("nacl", reason="pynacl required for libsodium SealedBox round-trip")

import httpx
import respx
from nacl import public

from sigantry_core.protocols import SecretStore
from sigantry_core.secrets.errors import SecretReadNotSupported
from sigantry_core.secrets.github_secrets import GithubSecretsSecretStore


def _make_keypair() -> tuple[public.PrivateKey, str]:
    """Generate a libsodium keypair; return (private_key, public_key_b64)."""
    private_key = public.PrivateKey.generate()
    public_key_b64 = b64encode(bytes(private_key.public_key)).decode("utf-8")
    return private_key, public_key_b64


def test_get_not_supported() -> None:
    """get() raises SecretReadNotSupported per RESEARCH §Pitfall 5.

    The exception is documented (NOT a generic 404 / KeyError / None).
    Adopters that need read-after-write should use KeyVault or ADO.
    """
    store = GithubSecretsSecretStore(owner="o", repo="r", pat="fake-pat-test")
    with pytest.raises(SecretReadNotSupported, match="GitHub Actions secrets API"):
        store.get("MY_SECRET")


def test_set_sealed_box_encryption_correct() -> None:
    """libsodium SealedBox round-trip: encrypt-with-public-key + decrypt-with-private-key.

    Uses a REAL pynacl keypair (RESEARCH §Pitfall 2). Mocks the public-key GET
    to return our test public key; captures the PUT body's encrypted_value;
    decrypts with the matching private key; asserts the plaintext matches.

    A shape-only assertion (e.g. "encrypted_value is base64") would NOT catch
    a wrong-key-encoding bug -- the only correct test is to actually decrypt.
    """
    private_key, public_key_b64 = _make_keypair()
    plaintext = "the-real-plaintext-value-42"

    captured_put: dict = {}

    with respx.mock(base_url="https://api.github.com") as router:
        router.get("/repos/owner/repo/actions/secrets/public-key").mock(
            return_value=httpx.Response(200, json={"key": public_key_b64, "key_id": "test-kid-001"})
        )

        def _put_handler(request: httpx.Request) -> httpx.Response:
            captured_put["body"] = json.loads(request.content)
            return httpx.Response(204)

        router.put("/repos/owner/repo/actions/secrets/MY_KEY").mock(side_effect=_put_handler)

        store = GithubSecretsSecretStore(owner="owner", repo="repo", pat="fake-pat-test")
        store.set("MY_KEY", plaintext)

    assert "body" in captured_put, "PUT was never issued"
    encrypted_b64 = captured_put["body"]["encrypted_value"]
    assert captured_put["body"]["key_id"] == "test-kid-001"

    # Decrypt with the matching private key -- THIS proves SealedBox correctness.
    sealed_box = public.SealedBox(private_key)
    decrypted = sealed_box.decrypt(b64decode(encrypted_b64)).decode("utf-8")
    assert decrypted == plaintext, (
        f"libsodium round-trip failed: decrypted={decrypted!r} != plaintext={plaintext!r}"
    )


def test_set_two_step_flow_pubkey_then_put() -> None:
    """set() issues exactly one GET on public-key + one PUT on the secret resource."""
    _, public_key_b64 = _make_keypair()

    with respx.mock(base_url="https://api.github.com") as router:
        pk_route = router.get("/repos/o/r/actions/secrets/public-key").mock(
            return_value=httpx.Response(200, json={"key": public_key_b64, "key_id": "kid"})
        )
        put_route = router.put("/repos/o/r/actions/secrets/SOME_KEY").mock(
            return_value=httpx.Response(204)
        )

        store = GithubSecretsSecretStore(owner="o", repo="r", pat="ghp")
        store.set("SOME_KEY", "v")

    assert pk_route.call_count == 1, f"expected 1 GET on /public-key; got {pk_route.call_count}"
    assert put_route.call_count == 1, f"expected 1 PUT on /SOME_KEY; got {put_route.call_count}"


def test_set_emits_secret_change_record_without_value(
    tmp_audit_dir: Path,
) -> None:
    """set() emits a SecretChangeRecord on success; the record carries NO value field."""
    _, public_key_b64 = _make_keypair()

    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_audit_dir
    try:
        with respx.mock(base_url="https://api.github.com") as router:
            router.get("/repos/o/r/actions/secrets/public-key").mock(
                return_value=httpx.Response(200, json={"key": public_key_b64, "key_id": "kid"})
            )
            router.put("/repos/o/r/actions/secrets/MY_KEY").mock(return_value=httpx.Response(204))

            store = GithubSecretsSecretStore(owner="o", repo="r", pat="ghp")
            store.set("MY_KEY", "the-plaintext")
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    jsonl = tmp_audit_dir / "secret_changes.jsonl"
    assert jsonl.is_file()
    record = json.loads(jsonl.read_text("utf-8").splitlines()[0])
    assert record["operation"] == "set"
    assert record["key"] == "MY_KEY"
    assert record["store_name"] == "github_secrets"
    # CRITICAL anti-pattern guard: the secret value MUST NOT appear in the record.
    assert "value" not in record
    assert "the-plaintext" not in jsonl.read_text("utf-8"), (
        "the secret plaintext leaked into the audit jsonl"
    )


def test_delete_emits_secret_change_record(tmp_audit_dir: Path) -> None:
    """delete() emits a SecretChangeRecord with operation='delete' after DELETE succeeds."""
    audit_mod = importlib.import_module("sigantry_core.governance.audit")
    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_audit_dir
    try:
        with respx.mock(base_url="https://api.github.com") as router:
            router.delete("/repos/o/r/actions/secrets/OBSOLETE").mock(
                return_value=httpx.Response(204)
            )
            store = GithubSecretsSecretStore(owner="o", repo="r", pat="ghp")
            store.delete("OBSOLETE")
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    record = json.loads((tmp_audit_dir / "secret_changes.jsonl").read_text("utf-8").splitlines()[0])
    assert record["operation"] == "delete"
    assert record["key"] == "OBSOLETE"
    assert record["store_name"] == "github_secrets"


def test_satisfies_secret_store_protocol() -> None:
    """GithubSecretsSecretStore satisfies the runtime_checkable SecretStore Protocol."""
    store = GithubSecretsSecretStore(owner="o", repo="r", pat="ghp")
    assert isinstance(store, SecretStore)
    assert store.name == "github_secrets"


def test_list_keys_filters_by_prefix() -> None:
    """list_keys returns the names of secrets whose name starts with the given prefix."""
    with respx.mock(base_url="https://api.github.com") as router:
        router.get("/repos/o/r/actions/secrets").mock(
            return_value=httpx.Response(
                200,
                json={
                    "total_count": 3,
                    "secrets": [
                        {"name": "DEPLOY_KEY_A", "created_at": "2026-01-01T00:00:00Z"},
                        {"name": "DEPLOY_KEY_B", "created_at": "2026-01-02T00:00:00Z"},
                        {"name": "OTHER_SECRET", "created_at": "2026-01-03T00:00:00Z"},
                    ],
                },
            )
        )
        store = GithubSecretsSecretStore(owner="o", repo="r", pat="ghp")
        keys = store.list_keys(prefix="DEPLOY_")
    assert sorted(keys) == ["DEPLOY_KEY_A", "DEPLOY_KEY_B"]
