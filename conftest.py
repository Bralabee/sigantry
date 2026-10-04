"""Project-root conftest.

Pytest already adds the repo root to ``sys.path`` via the
``[tool.pytest.ini_options].pythonpath = ["."]`` entry in pyproject.toml,
so tests can import both ``sigantry_core`` (installed package) and
``scripts`` (repo-root sibling that is not installed as a distribution)
without needing a conftest-level ``sys.path`` hack.

Phase 7 Plan 07-01 removes the historical ``sys.path.insert`` call to
satisfy the Pitfall 6 invariant enforced by
``scripts/ci/check-no-sys-path.py``.

Hosts the ``repo_files`` fixture: the single file inventory every repo-wide
guard test scans (see :func:`repo_tracked_files`).

Isolates every test from the real audit ledgers (see
:func:`_isolate_home_and_audit_dir`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Enforce clean non-color CLI output across all runner invocations and CI platforms
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"
os.environ.pop("FORCE_COLOR", None)

_REPO_ROOT = Path(__file__).resolve().parent


def repo_tracked_files(root: Path | None = None) -> tuple[tuple[Path, str], ...]:
    """Return every file this repository is responsible for, newest-state.

    Yields ``(absolute_path, relative_posix_path)`` pairs sorted by relative
    path, taken from ``git ls-files --cached --others --exclude-standard``:

    * ``--cached``  — everything git tracks;
    * ``--others``  — untracked files too, so a violation introduced in a file
      that has been created but not yet staged is still caught;
    * ``--exclude-standard`` — minus everything ``.gitignore`` /
      ``.git/info/exclude`` excludes.

    The repo-wide guards police *repository content*. Walking the filesystem
    with ``Path.rglob`` plus a hand-written denylist of directory basenames
    made them police *a developer's working tree* instead: they fired on the
    gitignored ``site/`` tree a local ``mkdocs build`` produces and on
    untracked scratch directories, and the per-guard denylists were free to
    drift apart. ``git ls-files`` is the precise expression of the intent, and
    it is the same set on every machine and in CI.

    Fails closed twice over: if git is unavailable or this is not a work tree,
    and if the inventory comes back EMPTY. Quietly scanning some other set of
    files is the defect this helper exists to remove; quietly scanning NO
    files is worse, because every guard then reports green having read nothing.
    """
    base = _REPO_ROOT if root is None else Path(root).resolve()
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=base,
            capture_output=True,
            check=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - env failure
        raise RuntimeError(
            f"repo_tracked_files: `git ls-files` failed in {base}. The repo-wide guards "
            "enumerate the git index; they cannot run outside a git work tree."
        ) from exc

    files: list[tuple[Path, str]] = []
    for rel in completed.stdout.split("\0"):
        if not rel:
            continue
        path = base / rel
        # --cached lists index entries whose working-tree file may be deleted.
        if not path.is_file():
            continue
        files.append((path, rel))

    # Fail closed on an EMPTY inventory, not only on a git error. `git ls-files`
    # exits 0 with no output in a work tree it considers empty, and every guard
    # that iterates this helper would then pass without reading a single file --
    # a whole battery green on a repository full of violations. An assertion that
    # cannot fail certifies nothing, so refuse the empty answer rather than
    # returning it.
    if not files:
        raise RuntimeError(
            f"repo_tracked_files: `git ls-files` returned no files in {base}. "
            "The repo-wide guards would pass vacuously on an empty inventory; "
            "refusing to report a clean scan that read nothing."
        )
    return tuple(sorted(files, key=lambda item: item[1]))


@pytest.fixture(scope="session")
def repo_files() -> tuple[tuple[Path, str], ...]:
    """Session-wide file inventory for the repo-wide guard tests."""
    return repo_tracked_files()


# Module attributes that hold a default audit location. Each is bound when
# its module is imported -- ``Path.home()`` evaluated once -- and several
# modules re-bind ``_DEFAULT_AUDIT_DIR`` by name, so patching the defining
# module alone leaves the copies pointing at the real directory.
_AUDIT_DEFAULT_ATTRS: tuple[tuple[str, str], ...] = (
    ("_DEFAULT_AUDIT_DIR", ""),
    ("DEFAULT_BOOTSTRAP_LEDGER", "bootstraps.jsonl"),
)


@pytest.fixture(autouse=True)
def _isolate_home_and_audit_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point HOME and every default audit location at a per-test temp dir.

    Any code path that falls back to the default ledger -- an ``emit_*``
    call without ``audit_dir``, a CLI run without ``--audit-dir`` --
    otherwise appends test records to the maintainer's REAL
    ``~/.sigantry/audit/`` ledgers, which are the only evidence of real
    deploys. A full run did exactly that: approvals, destructive-op and
    secret-change records from fixture principals, on every run.

    Setting HOME alone is not enough in-process: the package is imported
    before this conftest (the ``sigantry_core`` pytest11 plugin loads
    first), so the import-time defaults already hold the real home. Hence
    both: HOME / USERPROFILE for subprocesses and for code that calls
    ``Path.home()`` at run time, and a patch of every loaded
    ``sigantry_core`` module attribute that holds a default location.
    ``tests/sigantry_core/governance/test_audit_home_isolation.py`` fails if
    either is dropped.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    audit_dir = home / ".sigantry" / "audit"
    for name, module in list(sys.modules.items()):
        if module is None or not (name == "sigantry_core" or name.startswith("sigantry_core.")):
            continue
        for attr, leaf in _AUDIT_DEFAULT_ATTRS:
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, audit_dir / leaf if leaf else audit_dir)
    return home
