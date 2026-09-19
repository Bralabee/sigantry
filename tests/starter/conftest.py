"""Shared fixtures for tests/starter/.

STARTER_DIR resolves to ``<repo-root>/templates/starter/`` which is the
in-tree source-of-truth for the public ``sigantry-starter`` template
(mirrored out-of-band by an operator per CONTEXT D-01..D-03).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

STARTER_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "templates" / "starter"
"""The committed source-of-truth tree for the sigantry-starter template."""
