"""Shared fixtures for sigantry_core.workspace tests."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from sigantry_core.auth import TokenProvider
from sigantry_core.client import FabricRestClient, HttpResponse


@pytest.fixture
def mock_token_provider() -> MagicMock:
    tp = MagicMock(spec=TokenProvider)
    tp.last_credential_class.return_value = "MockCredential"
    tp.get_token.return_value = "test-token-xyz"
    tp.tenant_id = "11111111-2222-3333-4444-555555555555"
    return tp


@pytest.fixture
def mock_fabric_client() -> MagicMock:
    """MagicMock spec'd to FabricRestClient - supports .send, .send_lro, .list_paginated."""
    c = MagicMock(spec=FabricRestClient)
    # Default: send returns a 2xx HttpResponse with empty body
    c.send.return_value = HttpResponse(
        status_code=200,
        json_body={},
        headers={},
        request_id="req-test",
        operation_id=None,
        elapsed_ms=1.0,
    )
    c.send_lro.return_value = None
    c.list_paginated.return_value = iter([])
    # Support `with _client_factory(...) as client:` usage in CLI.
    c.__enter__ = MagicMock(return_value=c)
    c.__exit__ = MagicMock(return_value=None)
    return c


@pytest.fixture
def fresh_workspace_id() -> str:
    return str(uuid.uuid4())
