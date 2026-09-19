"""Shared fixtures for sigantry_core.workitems tests.

Created as Phase 11 Plan 11-00 Wave 0 cement. Mirrors
tests/sigantry_core/client/conftest.py verbatim for state isolation
(Pitfall 3: contextvar leakage; Pitfall 5: pyrate-limiter bucket state),
and appends the two Phase-11-specific fixtures (`fake_jwt_signing_key`,
`sample_deploy_record`) used by Plans 11-04, 11-05, 11-06, 11-07.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import respx

from sigantry_core.auth import TokenProvider


@pytest.fixture
def mock_token_provider() -> MagicMock:
    """MagicMock(spec=TokenProvider) returning a deterministic token per scope."""
    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "test-token-xyz"
    mp.last_credential_class.return_value = "MockCredential"
    mp.tenant_id = "test-tenant-id"
    return mp


@pytest.fixture
def respx_router():
    """respx router for mocking httpx at the transport layer."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture(autouse=True)
def _reset_correlation_context():
    """Prevent correlation-id leak across tests (Pitfall 3)."""
    try:
        from sigantry_core.client import logging as client_logging
    except ImportError:
        yield
        return
    client_logging._CORRELATION_ID.set(None)
    client_logging._OPERATION_ID.set(None)
    yield
    client_logging._CORRELATION_ID.set(None)
    client_logging._OPERATION_ID.set(None)


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    """Prevent pyrate-limiter bucket state leaking across tests (Pitfall 5)."""
    try:
        from sigantry_core.client.rate_limit import reset_buckets
    except ImportError:
        yield
        return
    reset_buckets()
    yield
    reset_buckets()


# ---------------------------------------------------------------------------
# Phase 11-specific fixtures (consumed by Plans 11-04 / 11-05 / 11-06 / 11-07)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def fake_jwt_signing_key() -> str:
    """RSA 2048-bit private key in PEM (session scope -- generation is slow).

    Used by Plan 11-05 unit tests that exercise sigantry_core.auth.github_app
    JWT minting. Session-scoped because RSA keygen is ~50ms and the tests do
    not need fresh material per test.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture
def sample_deploy_record():
    """Fully-populated DeployRecord with .with_hash() applied.

    Used by Plans 11-04, 11-05, 11-06, 11-07 tests. Skips cleanly until
    sigantry_core.release.record exists (Plan 11-02 lands).
    """
    record_mod = pytest.importorskip("sigantry_core.release.record")
    from datetime import UTC, datetime

    return record_mod.DeployRecord(
        workspace="ws-prod",
        release_id="R-2026-04-26-1",
        work_items=["1234", "5678"],
        fabric_items_changed=["nb_silver_pipeline.Notebook", "lh_gold.Lakehouse"],
        test_evidence={"smoke": "passed", "integration": "passed"},
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    ).with_hash()
