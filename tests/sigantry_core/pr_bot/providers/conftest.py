"""Shared fixtures + constants for tests/sigantry_core/pr_bot/providers/.

Plan 14-05 (Task 1) ships this conftest with constants used across the
GithubProvider and AdoProvider unit tests + the cross-provider parity
test. ``respx_mock`` is provided by the respx-pytest plugin (no fixture
needed here); the parent ``tests/sigantry_core/pr_bot/conftest.py``
exposes the four ``deterministic_*_payload`` factories used by the
parity tests in Task 4.
"""

from __future__ import annotations

from typing import Final

PR_ID: Final[str] = "42"
"""Canonical fake PR id used across all provider tests."""

GITHUB_OWNER: Final[str] = "sigantry"
GITHUB_REPO: Final[str] = "sigantry-test-repo"
"""GitHub coordinates -- match the cross-provider parity test mock URLs."""

ADO_ORG: Final[str] = "sigantry-test"
ADO_PROJECT: Final[str] = "sigantry-starter-test"
ADO_REPO_ID: Final[str] = "00000000-0000-0000-0000-000000000aaa"
"""ADO coordinates -- match the cross-provider parity test mock URLs."""
