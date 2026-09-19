#!/usr/bin/env python3
"""Pre-commit + CI lint: reject CRLF in ``.platform`` and sibling item-body files.

Fabric silently corrupts items whose ``.platform`` file (or item-folder
sibling ``.py`` / ``.json`` bodies) contains CRLF line endings. A Windows
editor round-trip can flip every file on every save. This hook rejects any
``b"\\r\\n"`` byte-sequence in:

  1. Every ``.platform`` under the working directory.
  2. Every ``.py`` / ``.json`` INSIDE a directory that also contains a
     ``.platform`` (i.e. inside a Fabric item folder). Files outside any
     item folder are intentionally ignored to avoid false positives on
     general-purpose repo ``.py`` / ``.json`` files that may legitimately
     use platform-specific line endings.

Usage
-----
    python scripts/pre_commit/check_crlf.py [root_dir_or_file]

Stdlib-only (``pathlib``, ``sys``) so the hook can run in the minimal
pre-commit ``language: system`` environment.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

_SKIP_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})
_SIBLING_SUFFIXES = frozenset({".py", ".json"})


def _iter_targets(root: Path) -> Iterator[Path]:
    """Yield every ``.platform`` plus every ``.py`` / ``.json`` INSIDE an item folder."""
    item_dirs: set[Path] = set()
    for platform in root.rglob(".platform"):
        if any(part in _SKIP_PARTS for part in platform.parts):
            continue
        yield platform
        item_dirs.add(platform.parent)

    for item_dir in item_dirs:
        for body in item_dir.rglob("*"):
            if body.is_dir():
                continue
            if any(part in _SKIP_PARTS for part in body.parts):
                continue
            if body.suffix in _SIBLING_SUFFIXES:
                yield body


def main(root: str = ".") -> int:
    """Walk ``root`` and reject CRLF in ``.platform`` + item-body ``.py`` / ``.json``.

    Returns 0 on success, 1 on any CRLF hit.
    """
    root_path = Path(root)
    if root_path.is_file():
        # Pre-commit may hand us a single file; walk from its parent.
        root_path = root_path.parent
    if not root_path.exists():
        root_path = Path(".")

    fail = False
    for target in _iter_targets(root_path):
        try:
            data = target.read_bytes()
        except OSError as exc:
            print(f"ERROR {target}: {exc}", file=sys.stderr)
            return 1
        if b"\r\n" in data:
            fail = True
            print(
                f"CRLF in {target} - re-save with LF endings or run "
                f"`sed -i 's/\\r$//' {target}` (on Windows use "
                f"`dos2unix {target}` OR re-checkout with .gitattributes "
                f"`* text=auto eol=lf` applied).",
                file=sys.stderr,
            )
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
