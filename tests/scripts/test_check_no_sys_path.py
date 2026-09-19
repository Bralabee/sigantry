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
from pathlib import Path


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
