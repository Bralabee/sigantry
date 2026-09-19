"""Shared fixtures for the sigantry_core test tree.

Plan 01-01 ships only the structure + version fixtures; plan 01-04 adds the
credential + SecretClient + respx fixtures under tests/sigantry_core/auth/.
"""

from __future__ import annotations

import pathlib

import pytest


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    """Absolute path to the repository root (one level above tests/)."""
    return pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def package_root(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / "sigantry_core"
