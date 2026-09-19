"""Project-root conftest.

Pytest already adds the repo root to ``sys.path`` via the
``[tool.pytest.ini_options].pythonpath = ["."]`` entry in pyproject.toml,
so tests can import both ``sigantry_core`` (installed package) and
``scripts`` (repo-root sibling that is not installed as a distribution)
without needing a conftest-level ``sys.path`` hack.

Phase 7 Plan 07-01 removes the historical ``sys.path.insert`` call to
satisfy the Pitfall 6 invariant enforced by
``scripts/ci/check-no-sys-path.py``.

Also skips smoke tests during collection unless ``PYTEST_RUN_SMOKE=1``,
because the smoke module imports ``scripts.smoke.deploy_matrix`` which is
picked up correctly at runtime but can surprise offline lint passes.
"""

from __future__ import annotations

import os

collect_ignore: list[str] = []
if os.environ.get("PYTEST_RUN_SMOKE") != "1":
    collect_ignore.append("tests/smoke/test_deploy_matrix.py")
