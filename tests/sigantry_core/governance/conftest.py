"""Shared fixtures for sigantry_core.governance tests."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from sigantry_core.auth import TokenProvider


@pytest.fixture
def mock_token_provider() -> MagicMock:
    """TokenProvider spec'd mock - last_credential_class returns 'MockCredential' by default."""
    tp = MagicMock(spec=TokenProvider)
    tp.last_credential_class.return_value = "MockCredential"
    tp.get_token.return_value = "test-token-xyz"
    tp.tenant_id = "11111111-2222-3333-4444-555555555555"
    return tp


@pytest.fixture
def capture_audit_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Bind caplog to the governance.audit logger at INFO level."""
    caplog.set_level(logging.INFO, logger="sigantry_core.governance.audit")
    return caplog
