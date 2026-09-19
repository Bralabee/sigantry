"""Shared fixtures for tests/sigantry_core/notifications/.

Wave 0 (Plan 16-00) provides:
  - ``respx_router`` -- respx Router-as-fixture for mocking webhook POSTs
  - ``mock_event`` -- a representative NotificationEvent for snapshot tests
  - ``tmp_audit_dir`` -- pytest tmp_path-backed audit dir (Plan 16-02 reuses)

Plan 16-01 lands the real implementations the fixtures exercise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def respx_router() -> Iterator[object]:
    """Router for mocking outbound HTTP from notification sinks."""
    respx = pytest.importorskip("respx")
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_event() -> object:
    """Representative NotificationEvent for snapshot tests."""
    from sigantry_core.protocols import NotificationEvent

    return NotificationEvent(
        title="Sigantry release approved",
        body="release-2026-04-28-001 approved by alice@example.com",
        level="info",
        properties={"release_id": "release-2026-04-28-001", "env": "prod"},
    )


@pytest.fixture
def tmp_audit_dir(tmp_path: Path) -> Path:
    d = tmp_path / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d
