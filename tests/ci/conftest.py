"""Shared fixtures for tests/ci/.

Provides the `repo_root` session fixture so the CI structural tests can
locate `azure-pipelines.yml` at the repo root independently of the
`tests/sigantry_core/conftest.py` fixture (pytest scopes conftests per
directory tree).

(Conftest-path reference renamed from ``tests/fabric_dataops_toolkits/``
in Plan 10-02 per ADR-0011.)
"""

from __future__ import annotations

import pathlib

import pytest


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    """Absolute path to the repository root (two levels above tests/ci/)."""
    return pathlib.Path(__file__).resolve().parents[2]
