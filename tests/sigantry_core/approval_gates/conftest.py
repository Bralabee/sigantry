"""Shared fixtures for tests/sigantry_core/approval_gates/.

Wave 0 (Plan 16-00) provides:
  - ``respx_router`` -- for ADO/GitHub/OPA REST mocking
  - ``mock_token_provider`` -- MagicMock(spec=TokenProvider)
  - ``tmp_audit_dir`` -- pytest tmp_path-backed audit dir for ApprovalRecord assertions
  - ``freeze_clock`` -- freezegun-friendly clock for poll-loop deterministic tests (Plan 16-03)
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
    return mp


@pytest.fixture
def tmp_audit_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def freeze_clock() -> Iterator[object]:
    freezegun = pytest.importorskip("freezegun")
    with freezegun.freeze_time("2026-04-28T12:00:00+00:00") as fct:
        yield fct
