"""Shared fixtures for docs structural tests."""

from __future__ import annotations

from pathlib import Path

import pytest

RUNBOOK_ROOT = Path("docs/runbooks")


@pytest.fixture(scope="session")
def runbook_files() -> list[Path]:
    return sorted(RUNBOOK_ROOT.glob("*.md"))
