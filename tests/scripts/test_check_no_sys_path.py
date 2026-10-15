"""Unit tests for scripts/ci/check-no-sys-path.py.

Tokenize-based scanner MUST:
  * Detect sys.path.append / sys.path.insert in `.py` CODE.
  * Detect sys.path.append / sys.path.insert in `.ipynb` code cells.
  * IGNORE matches inside docstrings, comments, and string literals.
  * IGNORE matches inside markdown / raw cells of a notebook.
  * SKIP excluded directories (.venv, build, .planning, etc.).
  * Exit 0 on this repository (regression guard).

Phase 8 Plan 08-04 sanitised the script's docstring: a client-specific
Pitfall-6 narrative was replaced with a vendor-agnostic description.
test_docstring_sanitised asserts the narrative is gone while the
sys.path topic + the scanner's public function signatures are
preserved.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
    """REGRESSION GUARD: this repository MUST pass clean.

    Resolves the repo root via `Path(__file__).parents[2]` -
    tests/scripts/test_check_no_sys_path.py -> parents[2] is the repo root.
    """
    chk = _load_checker()
    repo_root = Path(__file__).resolve().parents[2]
    assert chk.main(str(repo_root)) == 0


def test_docstring_sanitised() -> None:
    """PROD-14: scripts/ci/check-no-sys-path.py docstring is vendor-agnostic.

    Phase 8 Plan 08-04 replaced a client-specific Pitfall-6 narrative.
    Asserts:
      * Top-of-file docstring (first 30 lines) carries no 'Pitfall' narrative
        (organisation names are the repo-wide name gate's job).
      * The 'sys.path' topic is retained (the guardrail's purpose).
      * The module still exposes its scanning API unchanged.
    """
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check-no-sys-path.py"
    header = "\n".join(script_path.read_text(encoding="utf-8").splitlines()[:30])

    assert "Pitfall" not in header, "Pitfall narrative must not appear in docstring"
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


def _git_env() -> dict[str, str]:
    """No git state from the caller, and an identity for the commits below."""
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=_git_env())


def _git_init(root: Path) -> None:
    """Make ``root`` a real git work tree. No commit needed.

    ``git ls-files --others --exclude-standard`` reports untracked,
    non-ignored files without any history, which is all these tests need.
    """
    _git(root, "init", "-q")


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
    assert any(p.name == "a.py" for p, _walked in tracked)


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


def test_inventory_that_filters_down_to_nothing_also_refuses(tmp_path: Path) -> None:
    """A NON-empty listing whose files are all unreadable is still a vacuous scan.

    ``git ls-files --cached`` names index entries whose working-tree file may
    be gone. Checking the refusal against the raw listing rather than against
    what survives ``is_file()`` lets that case through: the listing is
    non-empty, so the refusal does not fire, and the filtered result is ``[]``
    -- which is not ``None``, so no filesystem fallback runs either. The guard
    then reports clean having read zero files.
    """
    chk = _load_checker()
    _git_init(tmp_path)
    staged = tmp_path / "gone.py"
    staged.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "gone.py"], cwd=tmp_path, check=True, capture_output=True)
    staged.unlink()

    # Precondition: git still names it, so the pre-filter check would pass.
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert [rel for rel in listed.stdout.split("\0") if rel] == ["gone.py"]

    with pytest.raises(RuntimeError, match="read nothing"):
        chk._git_tracked_paths(tmp_path)


def test_refusal_reaches_main_as_exit_2_not_a_traceback(tmp_path: Path) -> None:
    """``main()`` must honour its exit-code contract when the guard refuses.

    ``_iter_candidate_files`` is a generator, so a refusal raised while the
    inventory is built escapes ``main``'s for-loop. Unhandled, the process
    exits 1 with a traceback -- the same code as a real violation, which is
    precisely the distinction a consumer pipeline needs. Asserting on the
    private helper alone would leave that regression green.
    """
    chk = _load_checker()
    _git_init(tmp_path)
    assert chk.main(str(tmp_path)) == 2


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


# ---------------------------------------------------------------------------
# Issue #14: no vacuous pass on the fallback, and nothing git lists goes unread.
# ---------------------------------------------------------------------------

_BAD = "import sys\nsys.path.append('x')\n"


def test_a_root_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """A mistyped or unexpanded path argument read nothing; it must not pass."""
    chk = _load_checker()
    assert chk.main(str(tmp_path / "no-such-dir")) == 2


def test_an_empty_directory_outside_git_is_refused(tmp_path: Path) -> None:
    chk = _load_checker()
    assert chk._git_tracked_paths(tmp_path) is None, "tmp_path must not be a git work tree"
    assert chk.main(str(tmp_path)) == 2


def test_a_root_that_is_one_file_scans_that_file(tmp_path: Path) -> None:
    chk = _load_checker()
    bad = tmp_path / "bad.py"
    bad.write_text(_BAD, encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    assert chk.main(str(bad)) == 1
    assert chk.main(str(good)) == 0
    other = tmp_path / "notes.txt"
    other.write_text("sys.path.append('x')\n", encoding="utf-8")
    assert chk.main(str(other)) == 2


def test_a_filename_that_is_not_utf8_is_listed_not_a_crash() -> None:
    """``git ls-files`` output is bytes; one invalid UTF-8 name crashed the decode."""
    chk = _load_checker()
    rels = chk._decode_listing(b"ok.py\0bad\xff.py\0")
    assert rels[0] == "ok.py"
    assert rels[1].encode("utf-8", "surrogateescape") == b"bad\xff.py"


def test_a_tracked_file_under_an_excluded_name_is_scanned(tmp_path: Path) -> None:
    """In git mode the denylist could only hide real content: ``.gitignore`` already ran."""
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "tracked.py").write_text(_BAD, encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_the_excluded_names_still_apply_to_a_filesystem_walk(tmp_path: Path) -> None:
    """Control for the test above: outside git, ``build/`` is still skipped."""
    chk = _load_checker()
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "tracked.py").write_text(_BAD, encoding="utf-8")
    assert chk.main(str(tmp_path)) == 0


def test_a_nested_repository_is_walked(tmp_path: Path) -> None:
    """git lists an untracked nested repository as one directory entry."""
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    _git_init(nested)
    (nested / "bad.py").write_text(_BAD, encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1


def test_a_submodule_is_walked(tmp_path: Path) -> None:
    """A gitlink is listed as a path that is a directory; its files are read."""
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    _git_init(sub)
    (sub / "bad.py").write_text(_BAD, encoding="utf-8")
    _git(sub, "add", "bad.py")
    _git(sub, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "c")
    _git(tmp_path, "add", "sub")
    staged = subprocess.run(
        ["git", "ls-files", "-s", "sub"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    assert staged.stdout.startswith("160000"), "precondition: sub is a gitlink"
    assert chk.main(str(tmp_path)) == 1


def test_a_file_that_cannot_be_read_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file was scored clean; it is now "could not evaluate"."""
    chk = _load_checker()
    target = tmp_path / "locked.py"
    target.write_text(_BAD, encoding="utf-8")
    real = Path.read_text

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == target:
            raise PermissionError("denied")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", read_text)
    assert chk.main(str(tmp_path)) == 2


def test_a_missing_git_is_reported_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without git the guard walks the filesystem, and says so."""
    chk = _load_checker()
    _git_init(tmp_path)
    (tmp_path / "bad.py").write_text(_BAD, encoding="utf-8")
    monkeypatch.setattr(chk.shutil, "which", lambda name: None)
    assert chk.main(str(tmp_path)) == 1
    assert "git is not on PATH" in capsys.readouterr().err


def test_a_filesystem_walk_reports_hits_in_a_stable_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    chk = _load_checker()
    for name in ("b.py", "a.py", "c.py"):
        (tmp_path / name).write_text(_BAD, encoding="utf-8")
    assert chk.main(str(tmp_path)) == 1
    names = [line.split(":", 1)[0] for line in capsys.readouterr().out.splitlines()[:3]]
    assert names == ["a.py", "b.py", "c.py"]
