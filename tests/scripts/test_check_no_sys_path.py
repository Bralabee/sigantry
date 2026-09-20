"""Unit tests for scripts/ci/check-no-sys-path.py.

Tokenize-based scanner MUST:
  * Detect sys.path.append / sys.path.insert in `.py` CODE.
  * Detect sys.path.append / sys.path.insert in `.ipynb` code cells.
  * IGNORE matches inside docstrings, comments, and string literals.
  * IGNORE matches inside markdown / raw cells of a notebook.
  * SKIP excluded directories (.venv, build, .planning, etc.).
  * Exit 0 on the canonical fabric-dataops repo (regression guard).

Phase 8 Plan 08-04 sanitised the script's docstring: the AIMS-specific
Pitfall-6 narrative was replaced with a vendor-agnostic description.
test_docstring_sanitised asserts the narrative is gone while the
sys.path topic + the scanner's public function signatures are
preserved.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


def _load_checker():
    """Load the hyphenated script as a Python module via importlib.util."""
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check-no-sys-path.py"
    spec = importlib.util.spec_from_file_location("_check_no_sys_path", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nb_json(cells: list[dict]) -> str:
    return json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 4,
            "metadata": {},
            "cells": cells,
        }
    )


def test_clean_tree_exits_zero(tmp_path: Path) -> None:
    """Tree with only clean code returns exit code 0."""
    chk = _load_checker()
    (tmp_path / "clean.py").write_text("import os\nprint(os.getcwd())\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 0


def test_sys_path_append_in_py_exits_one(tmp_path: Path) -> None:
    """sys.path.append in a .py file triggers exit code 1."""
    chk = _load_checker()
    (tmp_path / "bad.py").write_text("import sys\nsys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_sys_path_insert_in_py_exits_one(tmp_path: Path) -> None:
    """sys.path.insert(...) in a .py file triggers exit code 1."""
    chk = _load_checker()
    (tmp_path / "bad.py").write_text("import sys\nsys.path.insert(0, 'x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_sys_path_append_in_ipynb_exits_one(tmp_path: Path) -> None:
    """sys.path.append inside a notebook code cell triggers exit 1."""
    chk = _load_checker()
    nb = _nb_json(
        [
            {
                "cell_type": "code",
                "source": ["import sys\n", "sys.path.append('x')\n"],
                "outputs": [],
                "execution_count": None,
                "metadata": {},
            },
        ]
    )
    (tmp_path / "nb.ipynb").write_text(nb, encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_excluded_dirs_skipped(tmp_path: Path) -> None:
    """A sys.path.append under .venv/ does NOT fail the scan."""
    chk = _load_checker()
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "bad.py").write_text("import sys\nsys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 0


def test_ipynb_markdown_cells_ignored(tmp_path: Path) -> None:
    """A markdown cell mentioning sys.path.append is NOT a hit."""
    chk = _load_checker()
    nb = _nb_json(
        [
            {
                "cell_type": "markdown",
                "source": ["This documents sys.path.append('x') as forbidden.\n"],
                "metadata": {},
            },
            {
                "cell_type": "code",
                "source": ["print('hello')\n"],
                "outputs": [],
                "execution_count": None,
                "metadata": {},
            },
        ]
    )
    (tmp_path / "nb.ipynb").write_text(nb, encoding="utf-8")
    assert chk.main(str(tmp_path)) == 0


def test_py_docstring_mention_ignored(tmp_path: Path) -> None:
    """A `.py` file whose docstring MENTIONS sys.path.append is NOT a hit.

    This is the reason the scanner uses `tokenize` (not regex) - it must
    distinguish genuine code from string-literal mentions.
    """
    chk = _load_checker()
    (tmp_path / "doc.py").write_text(
        '"""This docstring mentions sys.path.append() but is not code."""\nprint(\'ok\')\n',
        encoding="utf-8",
    )
    assert chk.main(str(tmp_path)) == 0


def test_py_comment_mention_ignored(tmp_path: Path) -> None:
    """A `.py` file whose comment MENTIONS sys.path.append is NOT a hit."""
    chk = _load_checker()
    (tmp_path / "comment.py").write_text(
        "# Don't use sys.path.append() - prefer Fabric Environments.\nprint('ok')\n",
        encoding="utf-8",
    )
    assert chk.main(str(tmp_path)) == 0


def test_this_repo_is_clean() -> None:
    """REGRESSION GUARD: the fabric-dataops toolkit repo MUST pass clean.

    Resolves the repo root via `Path(__file__).parents[2]` -
    tests/scripts/test_check_no_sys_path.py -> parents[2] is the repo root.
    """
    chk = _load_checker()
    repo_root = Path(__file__).resolve().parents[2]
    assert chk.main(str(repo_root)) == 0


def test_docstring_sanitised() -> None:
    """PROD-14: scripts/ci/check-no-sys-path.py docstring is vendor-agnostic.

    Phase 8 Plan 08-04 stripped the AIMS-specific Pitfall-6 narrative.
    Asserts:
      * Top-of-file docstring (first 30 lines) contains no 'AIMS',
        'Pitfall', or 'aims_data_platform' literals.
      * The 'sys.path' topic is retained (the guardrail's purpose).
      * The module still exposes its scanning API unchanged.
    """
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check-no-sys-path.py"
    header = "\n".join(script_path.read_text(encoding="utf-8").splitlines()[:30])

    assert "AIMS" not in header, "AIMS must not appear in the docstring header"
    assert "Pitfall" not in header, "Pitfall narrative must not appear in docstring"
    assert "aims_data_platform" not in header, "aims_data_platform must not appear in docstring"
    assert "sys.path" in header, "sys.path topic must be retained in docstring"

    # Behaviour-preservation smoke: module still exposes its public
    # scanning API exactly as before.
    chk = _load_checker()
    assert hasattr(chk, "main"), "main() entrypoint must be preserved"
    assert hasattr(chk, "_scan_tokens"), "_scan_tokens helper must be preserved"
    assert hasattr(chk, "_iter_candidate_files"), "_iter_candidate_files helper must be preserved"
    assert hasattr(chk, "_check_py"), "_check_py helper must be preserved"
    assert hasattr(chk, "_check_ipynb"), "_check_ipynb helper must be preserved"
    assert callable(chk.main)


# ---------------------------------------------------------------------------
# Hermetic enumeration (STRUCT-04): the guard polices REPOSITORY CONTENT,
# not whatever happens to be sitting in a developer's working tree.
# ---------------------------------------------------------------------------


def _git_init(root: Path) -> None:
    """Make ``root`` a real git work tree. No commit needed.

    ``git ls-files --others --exclude-standard`` reports untracked,
    non-ignored files without any history, which is all these tests need.
    """
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)


def test_gitignored_violation_is_not_flagged(tmp_path: Path) -> None:
    """A violation inside a gitignored directory must NOT fail the scan.

    This is the defect. A local ``mkdocs build``, a scratch directory or an
    audit run's own probe scripts are not repository content; a guard that
    walks the filesystem fails on them and reports a clean repo dirty.
    """
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "probe.py").write_text("import sys\nsys.path.insert(0, 'x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 0


def test_untracked_but_unignored_violation_is_flagged(tmp_path: Path) -> None:
    """Positive control for the test above: not-ignored still gets caught.

    Without this, a change that simply stopped scanning anything would
    satisfy the gitignore test and look correct.
    """
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("import sys\nsys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_non_git_tree_falls_back_to_filesystem_walk(tmp_path: Path) -> None:
    """A tree that is not a git work tree is still scanned.

    REGRESSION GUARD for a known failed approach: converting this script to
    ``git ls-files`` unconditionally broke eight of its own tests, which
    exercise it against temp dirs that are not git work trees. A consumer who
    vendors the script into an unpacked source tree is in the same position.
    The fallback yields a superset of the git inventory, so it can only
    over-scan, never miss.
    """
    chk = _load_checker()
    assert chk._git_tracked_paths(tmp_path) is None, "tmp_path must not be a git work tree"
    (tmp_path / "bad.py").write_text("import sys\nsys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_git_mode_is_actually_used_in_a_work_tree(tmp_path: Path) -> None:
    """The git branch really fires -- the fix is not the fallback passing."""
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / "a.py").write_text("print('ok')\n", encoding="utf-8")
    tracked = chk._git_tracked_paths(tmp_path)
    assert tracked is not None
    assert any(p.name == "a.py" for p in tracked)


def test_empty_git_inventory_refuses_to_report_clean(tmp_path: Path) -> None:
    """An empty inventory raises instead of passing vacuously.

    ``git ls-files`` exits 0 with no output in a work tree it considers
    empty. Returning that silently would let the guard report clean having
    read no files at all -- an assertion that cannot fail certifies nothing.
    """
    chk = _load_checker()
    _git_init(tmp_path)
    with pytest.raises(RuntimeError, match="read nothing"):
        chk._git_tracked_paths(tmp_path)


def test_exclusion_matches_relative_path_not_checkout_ancestry(tmp_path: Path) -> None:
    """Excluded names are matched under the scan root, not in its ancestry.

    Matching on the absolute path meant a checkout living under a directory
    named ``build`` or ``dist`` excluded every file in itself and passed
    vacuously -- the guard would be silently off for that developer.
    """
    chk = _load_checker()
    nested = tmp_path / "build" / "checkout"
    nested.mkdir(parents=True)
    (nested / "bad.py").write_text("import sys\nsys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(nested)) == 1


def test_malformed_python_is_tolerated_not_fatal(tmp_path: Path) -> None:
    """An unparseable ``.py`` file is skipped, not a crash.

    The module docstring has always promised this. It did not hold: the
    handler named ``tokenize.TokenizeError``, which does not exist, so the
    except clause raised ``AttributeError`` the first time a real
    ``TokenError`` arrived. Python only evaluates an except clause when
    something is raised, which is why the typo survived -- and why a single
    malformed file anywhere in a repo took the whole guard down with a
    traceback instead of a verdict.
    """
    chk = _load_checker()
    # Unterminated bracket: tokenize raises TokenError on this.
    (tmp_path / "broken.py").write_text("import sys\nx = (1, 2\n", encoding="utf-8")
    assert chk._scan_tokens("import sys\nx = (1, 2\n") == []
    assert chk.main(str(tmp_path)) == 0


def test_malformed_file_does_not_hide_violations_in_sibling_files(tmp_path: Path) -> None:
    """Tolerating a broken file must not stop the scan reaching the others."""
    chk = _load_checker()
    (tmp_path / "broken.py").write_text("import sys\nx = (1, 2\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text("import sys\nsys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1
