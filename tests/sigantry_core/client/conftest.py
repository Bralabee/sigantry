"""Shared fixtures for sigantry_core.client tests.

Created in Plan 02-01 Task 1. Keeps per-test state isolation (Pitfall 3: contextvar
leakage across tests; Pitfall 5: pyrate-limiter bucket state surviving tests).
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
    """Prevent pyrate-limiter bucket state leaking across tests (Pitfall 5).

    No-op until rate_limit.py is created in Task 2; keeping the fixture here
    for call-site stability.
    """
    try:
        from sigantry_core.client.rate_limit import reset_buckets
    except ImportError:
        yield
        return
    reset_buckets()
    yield
    reset_buckets()
