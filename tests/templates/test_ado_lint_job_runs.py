"""The shipped ADO lint job fails on a lint error and passes on a clean adopter tree (#22).

These tests run the ``script:`` steps of ``templates/jobs/lint-python.yml``
the way Azure Pipelines runs them on a Linux agent. ``script:`` is the
CmdLine task: it writes the body to a file, runs ``bash --noprofile --norc
<file>`` with no errexit, and fails the step only on the exit status of the
body's LAST command. The job stops at the first failing step.

Two trees, both adopter-shaped (no ``scripts/``, no ``.docs-freshness.yml``):

- clean: every step must pass, so the job cannot call a file only this repo has;
- seeded with an unused import (ruff F401) that is otherwise formatted and
  typed: the job must fail, at ``ruff check``. A ``ruff check`` sharing a step
  with a passing command after it would pass here.

What this does not model: the dependency installs (``pip`` and ``python -m
pip`` are stubbed to exit 0; the tools come from the test's own environment),
the ADO template expander beyond the one ``${{ if }}`` insertion the template
uses, Windows agents, and any step key other than ``script`` and
``displayName``. The runner refuses what it does not model rather than
skipping it. Planting a defect in every covered root, as
``scripts/ci/check-seeded-defects.py`` does for the GitHub lane, is a later
change under #22.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# A Linux agent's bash is what this models. On a Windows runner `bash` can
# resolve to WSL's launcher with no distribution installed, which runs nothing.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="models a Linux agent's bash; Windows' bash.exe may be WSL"
)

_TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "jobs" / "lint-python.yml"
_TYPE_CHECK_IF = "${{ if eq(parameters.enableTypeCheck, true) }}"
_PARAM_REF = re.compile(r"\$\{\{\s*parameters\.(\w+)\s*\}\}")
_SCRIPT_KEYS = {"script", "displayName"}

# An adopter's roots: the template's sigantry_core/ and scripts/ defaults name
# directories an adopter does not have, so an adopter sets these parameters.
_ADOPTER_PARAMS = {"sourcePaths": "src/", "typeCheckPaths": "src/"}

_CLEAN_MODULE = 'def greet(name: str) -> str:\n    return f"hello {name}"\n'
_SEEDED_MODULE = "import os\n\n\n" + _CLEAN_MODULE


def _defaults(doc: dict[str, Any]) -> dict[str, Any]:
    return {p["name"]: p["default"] for p in doc["parameters"]}


def _script_steps(steps: list[Any], params: dict[str, Any]) -> list[dict[str, str]]:
    """The ``script:`` steps the expander would emit, in order."""
    out: list[dict[str, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise AssertionError(f"unmodelled step: {step!r}")
        if list(step) == [_TYPE_CHECK_IF]:
            if params["enableTypeCheck"] is True:
                out.extend(_script_steps(step[_TYPE_CHECK_IF], params))
            continue
        if step.get("task") == "UsePythonVersion@0":
            continue
        unmodelled = set(step) - _SCRIPT_KEYS
        if "script" not in step or unmodelled:
            raise AssertionError(f"unmodelled step keys {sorted(unmodelled)}: {step!r}")
        out.append(step)
    return out


def _expand(body: str, params: dict[str, Any]) -> str:
    text = _PARAM_REF.sub(lambda m: str(params[m.group(1)]), body)
    if "${{" in text or "$(" in text:
        raise AssertionError(f"unmodelled expression left in step body: {text!r}")
    return text


def _stub_bin(tmp: Path) -> Path:
    """``pip`` and ``python -m pip`` exit 0; every other ``python`` is this one."""
    bin_dir = tmp / "stub-bin"
    bin_dir.mkdir()
    (bin_dir / "pip").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "python").write_text(
        '#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi\n'
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    for name in ("pip", "python"):
        (bin_dir / name).chmod(0o755)
    return bin_dir


def _run_job(
    template: Path, tree: Path, tmp: Path, overrides: dict[str, Any]
) -> list[tuple[str, int, str]]:
    """Run the job's script steps as ADO does; return (displayName, rc, output) per step run."""
    doc = yaml.safe_load(template.read_text(encoding="utf-8"))
    params = {**_defaults(doc), **overrides}
    (job,) = doc["jobs"]
    env = {
        "PATH": os.pathsep.join(
            [str(_stub_bin(tmp)), str(Path(sys.executable).parent), "/usr/bin", "/bin"]
        ),
        "HOME": str(tmp),
        "NO_COLOR": "1",
    }
    results: list[tuple[str, int, str]] = []
    for index, step in enumerate(_script_steps(job["steps"], params)):
        body_file = tmp / f"step-{index}.sh"
        body_file.write_text(_expand(step["script"], params), encoding="utf-8")
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", str(body_file)],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        results.append((step["displayName"], proc.returncode, proc.stdout + proc.stderr))
        if proc.returncode != 0:
            break
    return results


def _adopter_tree(root: Path, module: str) -> Path:
    tree = root / "adopter"
    package = tree / "src" / "adopter"
    package.mkdir(parents=True)
    # A [tool.ruff] table stops ruff searching the scratch dir's parents for config.
    (tree / "pyproject.toml").write_text(
        '[project]\nname = "adopter"\nversion = "0.0.0"\n\n[tool.ruff]\nline-length = 100\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(module, encoding="utf-8")
    return tree


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_the_job_passes_on_a_clean_adopter_tree(tmp_path: Path, scratch: Path) -> None:
    tree = _adopter_tree(tmp_path, _CLEAN_MODULE)
    results = _run_job(_TEMPLATE, tree, scratch, _ADOPTER_PARAMS)
    failed = [(name, rc, out) for name, rc, out in results if rc != 0]
    assert not failed, f"a step failed on a clean adopter tree: {failed}"
    ran = {name: out for name, _, out in results}
    # The tools ran and said so: a stub or a skipped step prints nothing.
    assert "All checks passed!" in ran.get("Ruff check", ""), results
    assert "already formatted" in ran.get("Ruff format check", ""), results
    assert "Success: no issues found" in ran.get("mypy", ""), results


def test_the_job_fails_at_ruff_check_on_a_lint_error(tmp_path: Path, scratch: Path) -> None:
    tree = _adopter_tree(tmp_path, _SEEDED_MODULE)
    results = _run_job(_TEMPLATE, tree, scratch, _ADOPTER_PARAMS)
    name, rc, out = results[-1]
    assert rc != 0, f"the job passed with an unused import in the tree: {results}"
    assert name == "Ruff check", results
    assert "F401" in out, out
