"""Shared fixtures for sigantry_core.capacity tests (Plan 03-02 Task 2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricArmRestClient, FabricRestClient


@pytest.fixture
def mock_token_provider() -> MagicMock:
    tp = MagicMock(spec=TokenProvider)
    tp.last_credential_class.return_value = "MockCredential"
    tp.get_token.return_value = "test-token-xyz"
    tp.tenant_id = "11111111-2222-3333-4444-555555555555"
    return tp


@pytest.fixture
def mock_fabric_client() -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.list_paginated.return_value = iter([])
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=None)
    return c


@pytest.fixture
def mock_arm_client() -> MagicMock:
    c = MagicMock(spec=FabricArmRestClient)
    c.send_arm_lro.return_value = None
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=None)
    return c


@pytest.fixture(autouse=True)
def _reset_correlation_context():
    """Prevent correlation-id leak across tests (Pitfall 3)."""
    from sigantry_core.client import logging as client_logging

    client_logging._CORRELATION_ID.set(None)
    client_logging._OPERATION_ID.set(None)
    yield
    client_logging._CORRELATION_ID.set(None)
    client_logging._OPERATION_ID.set(None)
