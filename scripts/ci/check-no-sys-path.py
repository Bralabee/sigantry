#!/usr/bin/env python3
"""Block ``sys.path.append`` / ``sys.path.insert`` in Python files and Jupyter notebooks.

Detects attempts to hack import paths at runtime. Scans every ``.py``
and ``.ipynb`` under the root argument (default ``.``) and fails the
build on any ``sys.path.append(...)`` or ``sys.path.insert(...)`` call
that appears in actual Python CODE (not comments, not docstrings, not
string literals, not markdown cells).

Rationale:
    Ad-hoc ``sys.path`` manipulation produces fragile deployments that
    break when files move. Runtime dependencies must be installed via
    the project's package manager (pip / conda / fabric-cicd wheel).
    This CI guard locks in the invariant: no ``.py`` or ``.ipynb`` in
    this repo (or in any consumer repo extending
    ``templates/extends/secure-pipeline.yml``) may introduce a
    regression.

Parser choice:
    Uses the standard ``tokenize`` module so docstrings, comments, and
    string literals do NOT produce false positives. Only genuine
    NAME/OP token sequences (``sys``, ``.``, ``path``, ``.``,
    ``append`` | ``insert``, ``(``) count as hits.

Excluded directories:
    ``.venv``, ``venv``, ``build``, ``dist``, ``__pycache__``,
    ``.planning``, ``.git``, ``node_modules``, ``.mypy_cache``,
    ``.ruff_cache``, ``.pytest_cache``, ``.tox``, ``.eggs``.

Output on any hit:
    ``<path>:<lineno>: <offending line>``

Exit codes:
    0 -- clean (no hits found).
    1 -- at least one hit; CI should fail.

Usage:
    python scripts/ci/check-no-sys-path.py [path]
    (default: current directory)
"""

from __future__ import annotations

import io
import json
import sys
import token
import tokenize
from pathlib import Path

_EXCLUDE_DIR_NAMES = frozenset(
    {
        ".venv",
        "venv",
        "build",
        "dist",
        "__pycache__",
        ".planning",
        ".git",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".eggs",
    }
)

_FORBIDDEN_ATTR = frozenset({"append", "insert"})

_ERROR_MSG = (
    "sys.path hacks are not allowed. Install runtime dependencies via the "
    "project's package manager (pip / conda / fabric-cicd wheel) instead of "
    "sys.path.append / sys.path.insert."
)


def _iter_candidate_files(root: Path):
    """Yield `.py` and `.ipynb` files under root, skipping excluded dirs."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDE_DIR_NAMES for part in path.parts):
            continue
        if path.suffix in (".py", ".ipynb"):
            yield path


def _scan_tokens(source: str) -> list[int]:
    """Return 1-based line numbers containing genuine sys.path.append/insert calls.

    Uses `tokenize` so string literals, comments, and docstrings never
    produce a hit. Malformed Python is tolerated: a `tokenize.TokenizeError`
    results in zero hits (the file will be flagged by lint/pytest elsewhere).
    """
    hit_lines: list[int] = []
    try:
        readline = io.BytesIO(source.encode("utf-8")).readline
        # Sliding window of 6 tokens: sys . path . (append|insert) (
        window: list[tokenize.TokenInfo] = []
        for tok in tokenize.tokenize(readline):
            if tok.type in (
                token.NEWLINE,
                token.NL,
                token.INDENT,
                token.DEDENT,
                token.COMMENT,
                token.ENCODING,
            ):
                continue
            window.append(tok)
            if len(window) > 6:
                window.pop(0)
            if len(window) == 6 and _matches_sys_path_call(window):
                hit_lines.append(window[4].start[0])
    except (tokenize.TokenizeError, IndentationError, SyntaxError):
        # Malformed source: let the real linters flag it.
        return hit_lines
    return hit_lines


def _matches_sys_path_call(window: list[tokenize.TokenInfo]) -> bool:
    """True if `window` encodes `sys . path . append(` or `sys . path . insert(`."""
    if len(window) != 6:
        return False
    return (
        window[0].type == token.NAME
        and window[0].string == "sys"
        and window[1].type == token.OP
        and window[1].string == "."
        and window[2].type == token.NAME
        and window[2].string == "path"
        and window[3].type == token.OP
        and window[3].string == "."
        and window[4].type == token.NAME
        and window[4].string in _FORBIDDEN_ATTR
        and window[5].type == token.OP
        and window[5].string == "("
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _check_py(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, line_text) hits in a .py file."""
    text = _read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for lineno in _scan_tokens(text):
        snippet = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
        hits.append((lineno, snippet.strip()))
    return hits


def _check_ipynb(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, line_text) hits inside code cells of a notebook.

    Only `cell_type == "code"` cells are scanned. Each code cell's source
    is tokenised in isolation; lineno is 1-based within the cell.
    """
    text = _read_text(path)
    if not text:
        return []
    try:
        nb = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return []
    cells = nb.get("cells") if isinstance(nb, dict) else None
    if not isinstance(cells, list):
        return []
    hits: list[tuple[int, str]] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source")
        if isinstance(source, list):
            cell_src = "".join(source)
        elif isinstance(source, str):
            cell_src = source
        else:
            continue
        cell_lines = cell_src.splitlines()
        for lineno in _scan_tokens(cell_src):
            snippet = cell_lines[lineno - 1] if 1 <= lineno <= len(cell_lines) else ""
            hits.append((lineno, snippet.strip()))
    return hits


def main(root_arg: str = ".") -> int:
    """Return 0 on clean, 1 on any hit."""
    root = Path(root_arg).resolve()
    all_hits: list[tuple[Path, int, str]] = []
    for path in _iter_candidate_files(root):
        hits = _check_py(path) if path.suffix == ".py" else _check_ipynb(path)
        for lineno, line in hits:
            all_hits.append((path, lineno, line))

    if not all_hits:
        return 0

    for path, lineno, line in all_hits:
        try:
            display = path.relative_to(root)
        except ValueError:
            display = path
        print(f"{display}:{lineno}: {line}")
    print(_ERROR_MSG)
    return 1


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "."
    raise SystemExit(main(arg))
