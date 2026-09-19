#!/usr/bin/env python3
"""Pre-commit + CI lint: reject duplicate logicalId values across the item tree.

Exit 0 if every ``.platform`` file under the working directory has a unique
``config.logicalId`` UUID. Exit 1 with one diagnostic line per collision
(plus the Fix: hint) otherwise.

Usage
-----
    python scripts/pre_commit/check_logical_id.py [root_dir_or_platform_file]

When invoked by ``pre-commit`` with ``pass_filenames: false`` (our
configuration), ``sys.argv[1:]`` is empty and we walk ``cwd``. When a
developer points the hook at an explicit root (CI manual invocation), we
walk the given path. If the first positional arg is an individual
``.platform`` file (pre-commit's ``pass_filenames: true`` case), we fall
back to walking its grandparent to preserve context.

Stdlib-only (``json``, ``pathlib``, ``sys``, ``collections``) so the hook
can run in the minimal pre-commit ``language: system`` environment without
installing the ``sigantry_core`` wheel.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# Walking skip list — keep in sync with .gitignore common entries.
_SKIP_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"})


def main(root: str = ".") -> int:
    """Walk ``root`` and reject duplicate Fabric ``logicalId`` values.

    Returns 0 on success, 1 on any duplicate / malformed file.
    """
    root_path = Path(root)
    if root_path.is_file() and root_path.name == ".platform":
        # Pre-commit may pass an individual file; walk from its grandparent
        # so the full repo tree is checked in context.
        root_path = root_path.parent.parent
    if not root_path.exists():
        root_path = Path(".")

    ids: dict[str, list[str]] = defaultdict(list)
    for platform in root_path.rglob(".platform"):
        if any(part in _SKIP_PARTS for part in platform.parts):
            continue
        try:
            doc = json.loads(platform.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR {platform}: {exc}", file=sys.stderr)
            return 1
        logical = (doc.get("config") or {}).get("logicalId")
        if not logical:
            continue
        ids[logical].append(str(platform))

    fail = False
    for logical, paths in ids.items():
        if len(paths) > 1:
            fail = True
            print(f"DUPLICATE logicalId {logical}:", file=sys.stderr)
            for p in paths:
                print(f"  {p}", file=sys.stderr)
            print(
                "Fix: run `fabric-dataops fabric-item copy <src> <dst> "
                "--new-display-name <name>` to regenerate, or manually set "
                "config.logicalId to a fresh UUIDv4.",
                file=sys.stderr,
            )
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
