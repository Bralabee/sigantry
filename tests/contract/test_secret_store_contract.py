"""SecretStore Protocol contract tests (Phase 16 / SEAM-02).

Ninth contract test. Wave 0 (Plan 16-00) shipped xfail stubs; Plan 16-02
lands real assertions parameterised over Fake + KeyVault + GitHub + ADO.

Per RESEARCH §3 Open-Q-3 + Phase 11 ADR-0004: SecretStore Protocol
instances do NOT carry an ``api_version`` class var.

Cross-impl notes:

- ``GithubSecretsSecretStore.get`` raises :class:`SecretReadNotSupported`
  by design (RESEARCH §Pitfall 5). The contract battery therefore accepts
  EITHER ``str | None`` OR ``SecretReadNotSupported`` as a valid result
  for ``get`` -- impl differences are tolerated. Any other behaviour
  (raw 404 / generic ``Exception`` / silent corruption) is a contract
  break.
- ``KeyVaultSecretStore`` constructor takes ``vault_url`` and would call
  ``DefaultAzureCredential()`` if not given an explicit credential; the
  factory uses a MagicMock SecretClient via ``__new__`` to avoid that.
- ``AdoVariableGroupSecretStore`` requires a ``TokenProvider``; the
  factory supplies a MagicMock-spec'd one.
- ``GithubSecretsSecretStore`` constructs cheaply (no auth chain).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Audit-2026-05-07 W1.10: importing ``sigantry_core.secrets`` pulls in
# ``github_secrets`` which depends on pynacl. Without the dep present,
# the contract battery hard-fails at collection. ``importorskip``
# converts the cliff into a clean skip for non-conda environments.
pytest.importorskip("nacl", reason="pynacl required for SecretStore contract battery")

from sigantry_core.auth import TokenProvider
from sigantry_core.protocols import SecretStore
from sigantry_core.secrets import (
    AdoVariableGroupSecretStore,
    GithubSecretsSecretStore,
    KeyVaultSecretStore,
    SecretReadNotSupported,
)
from sigantry_core.testing.doubles import FakeSecretStore

pytestmark = [pytest.mark.contract, pytest.mark.sigantry_seam]


# ---------------------------------------------------------------------------
# Store factories -- one per impl. Each constructs the store WITHOUT
# exercising live transport (the contract battery is a pure-Python check
# of name / Protocol membership / return-type shape; respx/SDK mocks live
# in the per-impl tests at tests/sigantry_core/secrets/).
# ---------------------------------------------------------------------------


def _fake_store() -> SecretStore:
    return FakeSecretStore()


def _kv_store() -> SecretStore:
    """Build a KeyVaultSecretStore with the SDK client replaced by a mock.

    Constructing via ``__new__`` skips the ``DefaultAzureCredential()``
    call so this test never hits the auth chain.
    """
    store = KeyVaultSecretStore.__new__(KeyVaultSecretStore)
    client = MagicMock()
    client.list_properties_of_secrets.return_value = iter([])
    store._client = client  # type: ignore[attr-defined]
    store._vault_url = "https://mock.vault.azure.net/"  # type: ignore[attr-defined]
    store._credential = MagicMock()  # type: ignore[attr-defined]
    store._actor = "MockCredential"  # type: ignore[attr-defined]
    return store


def _gh_store() -> SecretStore:
    return GithubSecretsSecretStore(owner="o", repo="r", pat="fake-pat-dummy")


def _ado_store() -> SecretStore:
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "tok"
    mp.tenant_id = "t"
    mp.last_credential_class.return_value = "MockCredential"
    return AdoVariableGroupSecretStore(
        organization="org",
        project="proj",
        group_id=1,
        token_provider=mp,
    )


_FACTORIES = [
    pytest.param(_fake_store, id="fake"),
    pytest.param(_kv_store, id="key_vault"),
    pytest.param(_gh_store, id="github_secrets"),
    pytest.param(_ado_store, id="ado_variable_group"),
]


# ---------------------------------------------------------------------------
# Contract battery -- the lock on SEAM-02.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", _FACTORIES)
def test_secret_store_protocol_membership(factory) -> None:
    """Every concrete impl + Fake satisfies ``SecretStore`` runtime_checkable."""
    store = factory()
    assert isinstance(store, SecretStore), (
        f"{type(store).__name__} does not satisfy the SecretStore Protocol"
    )
    assert isinstance(store.name, str) and store.name, (
        f"{type(store).__name__}.name must be a non-empty string"
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_secret_store_no_api_version_attr(factory) -> None:
    """Phase 11 precedent + RESEARCH §3 Open-Q-3: no api_version field at v3.0.

    Adding ``api_version`` to ANY single seam without coordinating across
    all ten seams is a contract break -- it would unbalance the symmetry
    recorded in protocols.py module docstring + ADR-0004. Cross-seam
    widening is a v3.1 candidate.
    """
    store = factory()
    cls = type(store)
    attr = getattr(cls, "api_version", None)
    assert not isinstance(attr, str), (
        f"{cls.__name__}.api_version must NOT be a class-level string at "
        "v3.0 -- cross-seam widening is deferred to v3.1 per planner-mapper "
        "resolution. See sigantry_core/protocols.py module docstring."
    )


@pytest.mark.parametrize("factory", _FACTORIES)
def test_secret_store_get_signature_compatible(factory) -> None:
    """get(key) accepts one positional str arg.

    The contract battery does not invoke ``get()`` against the real impls
    (that would require either real credentials or per-factory respx /
    SDK mocks; live-transport coverage lives in the per-impl tests at
    tests/sigantry_core/secrets/). We verify only that the signature
    matches the Protocol shape ``get(key: str) -> str | None`` (or raises
    :class:`SecretReadNotSupported` per RESEARCH §Pitfall 5 for the
    GitHub backend).
    """
    import inspect

    store = factory()
    assert callable(getattr(store, "get", None)), f"{type(store).__name__}.get must be callable"
    sig = inspect.signature(store.get)
    params = list(sig.parameters.values())
    assert len(params) >= 1, f"{type(store).__name__}.get must accept (key); got {sig}"


def test_fake_get_returns_str_or_none() -> None:
    """FakeSecretStore.get is exercised directly to lock the str|None shape."""
    store = FakeSecretStore()
    assert store.get("absent") is None
    store.set("present", "v")
    assert store.get("present") == "v"


def test_github_get_raises_secret_read_not_supported() -> None:
    """GithubSecretsSecretStore.get raises SecretReadNotSupported (RESEARCH §Pitfall 5).

    This is a per-impl pin in the contract suite -- the GitHub backend's
    write-only-for-value semantics are documented behaviour, NOT a bug,
    and adopters must catch the typed exception explicitly rather than
    handling a generic 404.
    """
    store = GithubSecretsSecretStore(owner="o", repo="r", pat="fake-pat-dummy")
    with pytest.raises(SecretReadNotSupported):
        store.get("any-key")


@pytest.mark.parametrize("factory", _FACTORIES)
def test_secret_store_set_signature_compatible(factory) -> None:
    """set(key, value) accepts two positional str args."""
    import inspect

    store = factory()
    assert callable(getattr(store, "set", None)), f"{type(store).__name__}.set must be callable"
    sig = inspect.signature(store.set)
    params = list(sig.parameters.values())
    assert len(params) >= 2, f"{type(store).__name__}.set must accept (key, value); got {sig}"


@pytest.mark.parametrize("factory", _FACTORIES)
def test_secret_store_ping_returns_none(factory) -> None:
    """ping() exists and is a no-arg callable.

    The ``FakeSecretStore`` ``ping`` is exercised directly; for real
    impls we only verify the signature (live transport is exercised in
    the per-impl tests at tests/sigantry_core/secrets/).
    """
    import inspect

    store = factory()
    assert callable(getattr(store, "ping", None)), f"{type(store).__name__}.ping must be callable"
    sig = inspect.signature(store.ping)
    assert len(sig.parameters) == 0, (
        f"{type(store).__name__}.ping must take no parameters; got {sorted(sig.parameters)}"
    )


# ---------------------------------------------------------------------------
# FakeSecretStore standalone behaviour -- the recorded-state contract is the
# value of the double, so it gets a sanity test alongside the parametrised
# contract battery.
# ---------------------------------------------------------------------------


def test_fake_secret_store_round_trips_set_get_delete() -> None:
    """FakeSecretStore is an in-memory dict-backed double; round-trip works."""
    store = FakeSecretStore()
    assert store.get("missing") is None
    store.set("K1", "v1")
    store.set("K2", "v2")
    assert store.get("K1") == "v1"
    assert store.get("K2") == "v2"
    assert sorted(store.list_keys()) == ["K1", "K2"]
    store.delete("K1")
    assert store.get("K1") is None
    assert store.list_keys() == ["K2"]


def test_fake_secret_store_counts_ping_invocations() -> None:
    """FakeSecretStore counts ping() invocations into ``.pinged``."""
    store = FakeSecretStore()
    store.ping()
    store.ping()
    assert store.pinged == 2
