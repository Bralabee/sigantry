"""KeyVaultSecretStore tests (Plan 16-02).

Azure SDK (azure.keyvault.secrets) round-trip with mocks; emits
SecretChangeRecord audit lines on set/delete per RESEARCH §3 + Open-Q-3.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Audit-2026-05-07 W1.10: ``sigantry_core.secrets`` imports pynacl
# transitively (via the GitHub-secrets sibling). A contributor running
# pytest without the documented conda env would otherwise hit a hard
# ``ModuleNotFoundError: No module named 'nacl'`` on collection.
# ``importorskip`` converts the missing-dep cliff into a clean skip.
pytest.importorskip("nacl", reason="pynacl required for SecretStore tests")

from azure.core.exceptions import ResourceNotFoundError

from sigantry_core.protocols import SecretStore
from sigantry_core.secrets.key_vault import KeyVaultSecretStore


def _make_store(client_mock: MagicMock, *, actor: str = "test-actor") -> KeyVaultSecretStore:
    """Build a KeyVaultSecretStore with the SDK client replaced by a mock."""
    store = KeyVaultSecretStore.__new__(KeyVaultSecretStore)
    store._client = client_mock  # type: ignore[attr-defined]
    store._vault_url = "https://mock.vault.azure.net/"  # type: ignore[attr-defined]
    store._credential = MagicMock()  # type: ignore[attr-defined]
    store._actor = actor  # type: ignore[attr-defined]
    return store


def test_get_set_delete_roundtrip(tmp_audit_dir: Path) -> None:
    """Round-trip: set('K','v') -> get('K') == 'v' -> delete('K') -> get('K') is None.

    Uses the in-memory SDK mock as a stand-in for KeyVault. The point is that
    the KeyVaultSecretStore method calls translate to the documented SDK calls
    (set_secret / get_secret / begin_delete_secret) -- not that KeyVault
    actually round-trips.
    """
    state: dict[str, str] = {}

    def _set_secret(name: str, value: str) -> MagicMock:
        state[name] = value
        return MagicMock(name=name, value=value)

    def _get_secret(name: str) -> MagicMock:
        if name not in state:
            raise ResourceNotFoundError(message=f"Secret {name} not found")
        m = MagicMock()
        m.value = state[name]
        return m

    def _begin_delete(name: str) -> MagicMock:
        state.pop(name, None)
        poller = MagicMock()
        poller.wait.return_value = None
        return poller

    client = MagicMock()
    client.set_secret.side_effect = _set_secret
    client.get_secret.side_effect = _get_secret
    client.begin_delete_secret.side_effect = _begin_delete

    store = _make_store(client)

    # Patch emit_secret_change_record to use tmp_audit_dir.
    # Note: ``import sigantry_core.governance.audit as audit_mod`` is shadowed
    # by the ``audit`` function re-exported from rbac.py in
    # ``sigantry_core/governance/__init__.py``; ``importlib.import_module``
    # bypasses that name binding and returns the actual ``audit.py`` module.
    audit_mod = importlib.import_module("sigantry_core.governance.audit")

    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_audit_dir
    try:
        store.set("MY_SECRET", "the-value")
        assert store.get("MY_SECRET") == "the-value"
        store.delete("MY_SECRET")
        assert store.get("MY_SECRET") is None
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    client.set_secret.assert_called_once_with("MY_SECRET", "the-value")
    client.begin_delete_secret.assert_called_once_with("MY_SECRET")


def test_list_keys_with_prefix_filter() -> None:
    """list_keys uses list_properties_of_secrets() and filters by prefix client-side."""
    client = MagicMock()
    sp_a = MagicMock()
    sp_a.name = "PREFIX_ONE"
    sp_b = MagicMock()
    sp_b.name = "PREFIX_TWO"
    sp_c = MagicMock()
    sp_c.name = "OTHER"
    client.list_properties_of_secrets.return_value = iter([sp_a, sp_b, sp_c])

    store = _make_store(client)
    keys = store.list_keys(prefix="PREFIX_")
    assert sorted(keys) == ["PREFIX_ONE", "PREFIX_TWO"]


def test_set_emits_secret_change_record(tmp_audit_dir: Path) -> None:
    """set() writes a SecretChangeRecord with operation='set' to secret_changes.jsonl."""
    client = MagicMock()
    client.set_secret.return_value = MagicMock(name="K", value="v")
    store = _make_store(client, actor="alice@example.com")

    # Note: ``import sigantry_core.governance.audit as audit_mod`` is shadowed
    # by the ``audit`` function re-exported from rbac.py in
    # ``sigantry_core/governance/__init__.py``; ``importlib.import_module``
    # bypasses that name binding and returns the actual ``audit.py`` module.
    audit_mod = importlib.import_module("sigantry_core.governance.audit")

    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_audit_dir
    try:
        store.set("MY_KEY", "plaintext")
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    jsonl = tmp_audit_dir / "secret_changes.jsonl"
    assert jsonl.is_file()
    record = json.loads(jsonl.read_text("utf-8").splitlines()[0])
    assert record["operation"] == "set"
    assert record["key"] == "MY_KEY"
    assert record["store_name"] == "key_vault"
    assert record["actor"] == "alice@example.com"
    assert record["audit_hash"]  # non-empty
    # Anti-pattern guard: no `value` field.
    assert "value" not in record


def test_delete_emits_secret_change_record(tmp_audit_dir: Path) -> None:
    """delete() writes a SecretChangeRecord with operation='delete'."""
    client = MagicMock()
    poller = MagicMock()
    poller.wait.return_value = None
    client.begin_delete_secret.return_value = poller
    store = _make_store(client)

    # Note: ``import sigantry_core.governance.audit as audit_mod`` is shadowed
    # by the ``audit`` function re-exported from rbac.py in
    # ``sigantry_core/governance/__init__.py``; ``importlib.import_module``
    # bypasses that name binding and returns the actual ``audit.py`` module.
    audit_mod = importlib.import_module("sigantry_core.governance.audit")

    original = audit_mod._DEFAULT_AUDIT_DIR
    audit_mod._DEFAULT_AUDIT_DIR = tmp_audit_dir
    try:
        store.delete("OBSOLETE")
    finally:
        audit_mod._DEFAULT_AUDIT_DIR = original

    # LROPoller.wait was called -- this is the soft-delete contract.
    poller.wait.assert_called_once()
    record = json.loads((tmp_audit_dir / "secret_changes.jsonl").read_text("utf-8").splitlines()[0])
    assert record["operation"] == "delete"
    assert record["key"] == "OBSOLETE"
    assert record["store_name"] == "key_vault"


def test_ping_uses_list_one_page_strategy() -> None:
    """ping() consumes one item from list_properties_of_secrets() and returns None."""
    client = MagicMock()
    client.list_properties_of_secrets.return_value = iter([MagicMock()])
    store = _make_store(client)

    assert store.ping() is None
    # The cheapest reachability check -- one call to list_properties_of_secrets.
    client.list_properties_of_secrets.assert_called_once()


# --- WR-06: audit on SDK failure -------------------------------------------


def test_set_emits_failure_warning_when_sdk_raises(
    tmp_audit_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """SDK exception during ``set`` -> structured WARNING + re-raise; JSONL stays silent.

    Audit-2026-05-08 review follow-up (WR-06): pre-fix an SDK
    exception during ``set`` skipped the audit record below, leaving
    the operator with no signal that a set was attempted. Post-fix
    the WARNING carries the same operation/key/store/actor payload
    the JSONL would have written, plus ``outcome=failed`` +
    ``exc_type``, on the same audit-plane logger so existing log
    pipelines pick it up. The JSONL ledger stays canonical (only
    successful writes; ``outcome`` field is a v3.x schema bump).
    """
    import logging as _logging

    client = MagicMock()
    client.set_secret.side_effect = RuntimeError("simulated 500 from KeyVault")
    store = _make_store(client, actor="alice@example.com")

    caplog.set_level(_logging.WARNING, logger="sigantry_core.governance.audit")
    with pytest.raises(RuntimeError, match="simulated 500"):
        store.set("PII_KEY", "value")

    jsonl = tmp_audit_dir / "secret_changes.jsonl"
    # JSONL ledger stays canonical -- only successful writes land there.
    assert not jsonl.exists() or not jsonl.read_text("utf-8").strip(), (
        "WR-06 contract: failed SDK calls must NOT write a success-shaped "
        "JSONL line (the record schema has no outcome field today)"
    )
    # The WARNING event carries the failure-shaped payload.
    matched = [r for r in caplog.records if r.msg == "secret_change_failed"]
    assert matched, "expected a secret_change_failed WARNING event"
    rec = matched[0]
    assert getattr(rec, "operation", None) == "set"
    assert getattr(rec, "key", None) == "PII_KEY"
    assert getattr(rec, "store_name", None) == "key_vault"
    assert getattr(rec, "actor", None) == "alice@example.com"
    assert getattr(rec, "outcome", None) == "failed"
    assert getattr(rec, "exc_type", None) == "RuntimeError"


def test_delete_emits_failure_warning_when_sdk_raises(
    tmp_audit_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """SDK exception during ``delete`` -> structured WARNING + re-raise."""
    import logging as _logging

    client = MagicMock()
    poller = MagicMock()
    poller.wait.side_effect = PermissionError("delete forbidden by policy")
    client.begin_delete_secret.return_value = poller
    store = _make_store(client)

    caplog.set_level(_logging.WARNING, logger="sigantry_core.governance.audit")
    with pytest.raises(PermissionError):
        store.delete("PII_KEY")

    jsonl = tmp_audit_dir / "secret_changes.jsonl"
    assert not jsonl.exists() or not jsonl.read_text("utf-8").strip()
    matched = [r for r in caplog.records if r.msg == "secret_change_failed"]
    assert matched
    rec = matched[0]
    assert getattr(rec, "operation", None) == "delete"
    assert getattr(rec, "outcome", None) == "failed"
    assert getattr(rec, "exc_type", None) == "PermissionError"


def test_get_returns_none_on_resource_not_found() -> None:
    """KeyVault's missing-secret API returns 404 -> ResourceNotFoundError -> None."""
    client = MagicMock()
    client.get_secret.side_effect = ResourceNotFoundError(message="404")
    store = _make_store(client)

    assert store.get("ABSENT_KEY") is None


def test_satisfies_secret_store_protocol() -> None:
    """KeyVaultSecretStore satisfies the runtime_checkable SecretStore Protocol."""
    client = MagicMock()
    store = _make_store(client)
    assert isinstance(store, SecretStore)
    assert store.name == "key_vault"
