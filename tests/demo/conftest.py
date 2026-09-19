"""Shared fixtures + path helpers for tests/demo/.

Wave 0 (Plan 15-00) stamps this file with REPO / STARTER_DIR /
DEMO_DIR / FABRIC_ITEMS_DIR helpers so Plans 15-01..15-04 can rely on
a single import surface. Mirror of tests/starter/conftest.py.

Note (per Phase 14 deviation 2): the repo does not ship a top-level
``tests/__init__.py``, so ``from tests.demo.conftest import DEMO_DIR``
does not resolve as a normal Python import path. Test files that
need these constants either consume them via pytest fixtures
(possible because conftest.py is auto-discovered) or replicate the
local ``Path(__file__).resolve().parents[2] / "templates" / "demo"``
idiom from ``tests/starter/test_starter_content.py``. The constants
remain public for downstream plans + IDE navigation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

REPO: Final[Path] = Path(__file__).resolve().parents[2]
"""Repository root (3_DATAOPS_FABRIC_2026)."""

STARTER_DIR: Final[Path] = REPO / "templates" / "starter"
"""Phase 14 starter source tree (byte-extension parent)."""

DEMO_DIR: Final[Path] = REPO / "templates" / "demo"
"""Phase 15 demo source tree (extends STARTER_DIR per CONTEXT D-02)."""

FABRIC_ITEMS_DIR: Final[Path] = DEMO_DIR / "fabric_items"
"""The 4 sample-item subtree (Sales.Lakehouse, LoadOrders.Notebook,
RefreshOrdersDaily.DataPipeline, OrdersAnalytics.SemanticModel)."""

DEMO_DIVERGENCE_ALLOWED: Final[frozenset[str]] = frozenset(
    {
        "README.md",
        "parameters.yml",
        "docs/QUICKSTART.md",
    }
)
"""Files that may diverge from STARTER_DIR (CONTEXT D-02 + RESEARCH §Pattern 1)."""
