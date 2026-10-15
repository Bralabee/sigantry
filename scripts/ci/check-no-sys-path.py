#!/usr/bin/env python3
"""Block ``sys.path.append`` / ``sys.path.insert`` in Python files and Jupyter notebooks.

Detects attempts to hack import paths at runtime. Scans the ``.py`` and
``.ipynb`` files that make up REPOSITORY CONTENT under the root argument
(default ``.``) and fails the build on any ``sys.path.append(...)`` or
``sys.path.insert(...)`` call that appears in actual Python CODE (not
comments, not docstrings, not string literals, not markdown cells).

File inventory:
    Inside a git work tree the inventory comes from ``git ls-files``
    (tracked plus untracked-but-not-ignored), so a gitignored local
    artifact -- a ``mkdocs build``, a scratch directory, an audit run --
    is not repository content and cannot fail the build. A submodule or a
    nested repository, which git lists as a directory, is walked. Outside a
    work tree, or when git cannot be used (a note on stderr says so), the
    scan walks the filesystem instead. A root that does not exist, an
    inventory that ends up empty, and a file that cannot be read are all
    REFUSED rather than reported clean. A root that is a single ``.py`` or
    ``.ipynb`` file scans that file.

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

Excluded directories (filesystem walk only):
    ``.venv``, ``venv``, ``build``, ``dist``, ``__pycache__``,
    ``.planning``, ``.git``, ``node_modules``, ``.mypy_cache``,
    ``.ruff_cache``, ``.pytest_cache``, ``.tox``, ``.eggs``.
    In git mode ``.gitignore`` has already removed build output, so a file
    git lists under one of these names is repository content and is
    scanned.

Output on any hit:
    ``<path>:<lineno>: <offending line>``

Exit codes:
    0 -- clean (no hits found).
    1 -- at least one hit; CI should fail.
    2 -- the guard could not evaluate (the root does not exist, the file
         inventory is empty, or a file could not be read). NEVER read
         this as clean; it is not the same answer as 0, and keeping it
         distinct from 1 is what lets a consumer tell "the guard is
         broken" from "the guard found something".

Usage:
    python scripts/ci/check-no-sys-path.py [path]
    (default: current directory)
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import token
import tokenize
from collections.abc import Iterator
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


class GuardError(RuntimeError):
    """The guard could not evaluate; ``main`` exits 2, never 0."""


def _note(message: str) -> None:
    print(f"check-no-sys-path: {message}", file=sys.stderr)


def _decode_listing(raw: bytes) -> list[str]:
    """``git ls-files -z`` output as paths, whatever bytes a filename holds.

    Decoding as text failed with ``UnicodeDecodeError`` on a filename that
    is not valid UTF-8, which escaped ``main`` as a traceback and exit 1.
    ``surrogateescape`` keeps every byte, so the path still opens.
    """
    return [rel.decode("utf-8", "surrogateescape") for rel in raw.split(b"\0") if rel]


def _walk(directory: Path) -> list[Path]:
    """Every file under ``directory``, in a stable order."""
    return sorted(p for p in directory.rglob("*") if p.is_file())


def _git_tracked_paths(root: Path) -> list[tuple[Path, bool]] | None:
    """The files git is responsible for under ``root``, or None.

    Each entry is ``(path, walked)``: ``walked`` is True for a file found by
    walking a directory git listed (a submodule, or a nested repository),
    which the excluded-directory names then apply to, as in a filesystem
    walk. ``git ls-files`` lists those as one directory entry, and a
    directory does not survive the file filter, so their content went
    unread.

    ``None`` means "``root`` is not a git work tree, or git is unusable here"
    -- the caller then walks the filesystem instead. That fallback is safe in
    the direction that matters: walking yields a SUPERSET of the git
    inventory, so the guard can only ever scan more than it needs to, never
    miss a violation. Unless ``root`` is simply not a work tree, the reason
    is printed on stderr, so a fallback is never silent.

    Uses ``git ls-files --cached --others --exclude-standard``: everything
    tracked, plus untracked files that are not gitignored, so a violation in a
    file created but not yet staged is still caught. Honouring ``.gitignore``
    is the entire point -- a local ``mkdocs build``, a scratch directory, or
    an audit run's own probe scripts are not repository content, and a guard
    that fails on them is policing the developer's working tree rather than
    the repo.

    Fails closed on an EMPTY inventory rather than returning it. ``git
    ls-files`` exits 0 with no output in a work tree it considers empty, and
    the scan would then report clean having read nothing at all.
    """
    git = shutil.which("git")
    if git is None:
        _note("git is not on PATH; walking the filesystem instead of the git inventory")
        return None
    try:
        inside = subprocess.run(
            [git, "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _note(f"git rev-parse failed ({exc}); walking the filesystem instead")
        return None
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        error = inside.stderr.decode("utf-8", "replace").strip()
        if inside.returncode != 0 and "not a git repository" not in error:
            _note(f"git rev-parse failed ({error}); walking the filesystem instead")
        return None

    try:
        listed = subprocess.run(
            [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _note(f"git ls-files failed ({exc}); walking the filesystem instead")
        return None

    rels = _decode_listing(listed.stdout)
    # --cached lists index entries whose working-tree file may be deleted, so the
    # inventory that matters is what survives the filter, not what git named. A
    # staged-then-deleted file (or a sparse checkout) makes `rels` non-empty while
    # the scan still reads nothing -- checking before the filter would let exactly
    # the vacuous pass this refusal exists to prevent through.
    paths: list[tuple[Path, bool]] = []
    for rel in rels:
        path = root / rel
        if path.is_file():
            paths.append((path, False))
        elif path.is_dir():
            paths.extend((inner, True) for inner in _walk(path))
    if not paths:
        raise GuardError(
            f"check-no-sys-path: `git ls-files` returned no readable files in {root}. "
            "Refusing to report a clean scan that read nothing."
        )
    return paths


def _is_excluded(path: Path, root: Path) -> bool:
    """True if ``path`` sits under an excluded directory *relative to root*.

    Matching on the relative path matters: the absolute path carries the
    checkout's own ancestry, so a repository cloned under a directory that
    happens to be named ``build`` or ``dist`` would otherwise exclude every
    file in itself and pass vacuously.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(part in _EXCLUDE_DIR_NAMES for part in rel.parts)


def _iter_candidate_files(root: Path) -> Iterator[Path]:
    """Yield the `.py` and `.ipynb` files under root that the guard polices.

    Prefers the git inventory (see :func:`_git_tracked_paths`) and falls back
    to a filesystem walk when ``root`` is not a git work tree -- which is the
    normal case for this script's own unit tests, and for a consumer who
    vendors it into an unpacked source tree.

    The excluded-directory names apply to what is walked, never to a file
    git lists: in git mode ``.gitignore`` has already removed build output,
    so the names could only hide real repository content.
    """
    if not root.exists():
        raise GuardError(f"check-no-sys-path: {root} does not exist; nothing to scan.")
    if root.is_file():
        if root.suffix not in (".py", ".ipynb"):
            raise GuardError(f"check-no-sys-path: {root} is not a .py or .ipynb file.")
        yield root
        return
    tracked = _git_tracked_paths(root)
    if tracked is None:
        walked = _walk(root)
        if not walked:
            raise GuardError(
                f"check-no-sys-path: no files under {root}. "
                "Refusing to report a clean scan that read nothing."
            )
        tracked = [(path, True) for path in walked]
    for path, is_walked in tracked:
        if path.suffix not in (".py", ".ipynb"):
            continue
        if not path.is_file():
            continue
        if is_walked and _is_excluded(path, root):
            continue
        yield path


def _scan_tokens(source: str) -> list[int]:
    """Return 1-based line numbers containing genuine sys.path.append/insert calls.

    Uses `tokenize` so string literals, comments, and docstrings never
    produce a hit. Malformed Python is tolerated: a `tokenize.TokenError`
    results in zero hits (the file will be flagged by lint/pytest elsewhere).

    The handler used to name `tokenize.TokenizeError`, which does not exist.
    Python only evaluates an except clause when something is raised, so the
    typo lay dormant until a genuinely malformed file appeared -- and then
    raised `AttributeError` from inside the handler instead of tolerating the
    file. One unparseable `.py` anywhere in a repo crashed the whole guard.
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
    except (tokenize.TokenError, IndentationError, SyntaxError):
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
    # An unreadable file is not a clean one: scoring it "" passed it unread.
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GuardError(f"check-no-sys-path: cannot read {path}: {exc}") from exc


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
    """Return 0 on clean, 1 on any hit, 2 when the guard could not evaluate.

    2 is distinct on purpose. Collapsing "the guard is broken" onto 1 makes a
    guard that read nothing indistinguishable from a guard that found a real
    violation, and a consumer pipeline cannot tell which it is looking at.
    Both are non-zero, so either still fails the build.
    """
    root = Path(root_arg).resolve()
    all_hits: list[tuple[Path, int, str]] = []
    try:
        # _iter_candidate_files is a generator, so a refusal raised while the
        # inventory is being built escapes the for-loop below unless it is
        # caught here -- exiting 1 with a traceback rather than saying why.
        # Only GuardError means "could not evaluate"; any other error is a
        # bug in the guard and keeps its traceback.
        for path in list(_iter_candidate_files(root)):
            hits = _check_py(path) if path.suffix == ".py" else _check_ipynb(path)
            for lineno, line in hits:
                all_hits.append((path, lineno, line))
    except GuardError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

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
