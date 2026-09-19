"""Shared fixtures for tests/sigantry_core/secrets/.

Wave 0 (Plan 16-00) provides:
  - ``respx_router`` -- for GitHub/ADO REST mocking (KeyVault uses Azure SDK mocks instead)
  - ``mock_token_provider`` -- a MagicMock(spec=TokenProvider) for BaseRestClient subclasses
  - ``tmp_audit_dir`` -- pytest tmp_path-backed audit dir for SecretChangeRecord assertions
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def respx_router() -> Iterator[object]:
    respx = pytest.importorskip("respx")
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_token_provider() -> object:
    from sigantry_core.auth import TokenProvider

    mp = MagicMock(spec=TokenProvider)
    mp.get_token.return_value = "test-token"
    mp.tenant_id = "test-tenant-id"
    mp.last_credential_class.return_value = "MockCredential"
    return mp


@pytest.fixture
def tmp_audit_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d
