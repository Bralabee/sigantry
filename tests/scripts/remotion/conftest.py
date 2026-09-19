"""Shared path helper for Node-side scaffold tests under
``tests/scripts/remotion/``.

Note: matches the ``tests/demo/conftest.py`` idiom -- the constant
is exposed for downstream plans + IDE navigation. Test files
typically replicate the local ``Path(__file__)`` resolution because
the repo lacks a top-level ``tests/__init__.py`` (see Phase 14
deviation 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

REMOTION_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "scripts" / "remotion"
"""The Remotion subproject root (scripts/remotion/)."""
