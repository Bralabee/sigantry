#!/usr/bin/env python3
"""sigantry: every quality gate in ci.yml must FAIL on a tree with a known defect (#47).

The tests in ``tests/ci/test_quality_gates_run.py`` read each gate's command
and reject ``|| true``, ``continue-on-error`` and the like. They cannot see the
configuration the command reads: a ``[[tool.mypy.overrides]]`` with
``ignore_errors``, a ruff ``per-file-ignores`` of ``ALL``, a ruff format
``exclude``, or a ``--deselect`` in pytest's ``addopts`` turns a gate off while
its command stays byte-for-byte the same. The ways to write such a
configuration are as open-ended as the spellings of an upload were (#33), so
this does not read the configuration. It runs the gates.

For each gate it copies the tracked tree and plants one defect in every
directory the gate covers: each directory under the gate's roots that holds a
tracked ``.py`` file (for pytest, a tracked ``test_*.py`` file), so that
configuration scoped to one sub-package is seen as well as configuration
scoped to a root. It then runs the gate's command exactly as ci.yml spells it
(read from ci.yml by job and step name, with that job's and step's ``env``),
in the copy, against the copy's own configuration. The gate passes this check
only if its command exits non-zero AND its output carries the tool's own
report of every planted defect: the rule code and location for ruff, the
"Would reformat" line for ruff format, the error line for mypy, the FAILED
line for pytest. A gate that exits 0, crashes, or reports a seed as anything
but a failure fails the check.

Known limit: a defect is planted per directory, not per file, so
configuration that excludes one existing file is not seen.

Usage:
  python scripts/ci/check-seeded-defects.py [--root .]

Exit codes: 0 every gate failed on its seeds; 1 at least one gate did not;
2 the check could not run (a gate's step is missing from ci.yml or uses
something this check cannot reproduce, git or bash is not available, a
seed path already exists, a file could not be read or copied).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

_MYPY_SEED = 'def seeded_defect() -> int:\n    return "not an int"\n'
_LINT_SEED = "import os\n"
_FORMAT_SEED = "SEEDED_DEFECT = 'single quotes'\n"
_TEST_SEED = "def test_seeded_defect() -> None:\n    assert 1 == 2\n"


def _ruff_check_report(path: str) -> str:
    # The full output format puts the location on the line after the code;
    # the concise one puts it first. Either is a report of this seed.
    where = re.escape(path) + r":1:8"
    return rf"F401[^\n]*\n\s*--> {where}\b|^{where}: F401\b"


@dataclass(frozen=True)
class Gate:
    """One quality gate: where ci.yml runs it, where to plant, how it reports a seed."""

    label: str
    job: str
    step: str
    roots: tuple[str, ...]
    seed_name: str
    content: str
    # Which tracked files mark a directory as one the gate covers.
    covers: Callable[[str], bool]
    report: Callable[[str], str]


def _is_py(name: str) -> bool:
    return name.endswith(".py")


def _is_test(name: str) -> bool:
    base = PurePosixPath(name).name
    return base.startswith("test_") and base.endswith(".py")


_LINTED = ("sigantry_core", "tests", "scripts")
_TYPED = ("sigantry_core", "scripts")

GATES: tuple[Gate, ...] = (
    Gate(
        "ruff check",
        "lint",
        "Ruff check",
        _LINTED,
        "_seeded_defect_lint",
        _LINT_SEED,
        _is_py,
        _ruff_check_report,
    ),
    Gate(
        "ruff format",
        "lint",
        "Ruff format check",
        _LINTED,
        "_seeded_defect_format",
        _FORMAT_SEED,
        _is_py,
        lambda path: rf"^Would reformat: {re.escape(path)}$",
    ),
    Gate(
        "mypy",
        "types",
        "Mypy",
        _TYPED,
        "_seeded_defect_types",
        _MYPY_SEED,
        _is_py,
        lambda path: rf"^{re.escape(path)}:2: error: Incompatible return value type",
    ),
    Gate(
        "pytest",
        "test",
        "Run pytest",
        ("tests",),
        "test_seeded_defect",
        _TEST_SEED,
        _is_test,
        lambda path: rf"^FAILED {re.escape(path)}::test_seeded_defect\b",
    ),
)


class SetupError(Exception):
    """The check cannot run; exit 2, never a pass."""


def seeds_for(gate: Gate, tracked: list[str]) -> dict[str, str]:
    """One seed per directory under the gate's roots that holds a file the gate covers.

    Each seed's name carries its directory, so no two share a module name
    (mypy refuses duplicates) or a test file basename (pytest does too).
    """
    dirs = {root for root in gate.roots}
    for name in tracked:
        path = PurePosixPath(name)
        if path.parts and path.parts[0] in gate.roots and gate.covers(name):
            dirs.add(str(path.parent))
    seeds = {}
    for directory in sorted(dirs):
        tag = re.sub(r"\W", "_", directory)
        seeds[f"{directory}/{gate.seed_name}_{tag}.py"] = gate.content
    return seeds


def _env_strings(block: object, where: str) -> dict[str, str]:
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise SetupError(f"{where}: env is not a mapping")
    out = {}
    for key, value in block.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        if "${{" in text:
            raise SetupError(f"{where}: env {key} is an expression this check cannot evaluate")
        out[str(key)] = text
    return out


def command_for(workflow: dict, gate: Gate) -> tuple[str, dict[str, str], str]:
    """The gate's ``run:`` text, its env, and its working directory, from ci.yml.

    Anything that changes how GitHub runs the step and that this check does
    not reproduce (``defaults``, a step ``shell``) is refused rather than
    ignored, so the check never runs a different command from CI's.
    """
    jobs = workflow.get("jobs") or {}
    job = jobs.get(gate.job)
    if not isinstance(job, dict):
        raise SetupError(f"ci.yml has no job {gate.job!r} for {gate.label}")
    for where, node in (("ci.yml", workflow), (f"ci.yml job {gate.job}", job)):
        if node.get("defaults"):
            raise SetupError(f"{where} sets defaults, which this check does not reproduce")
    steps = [
        s for s in job.get("steps") or [] if isinstance(s, dict) and s.get("name") == gate.step
    ]
    if len(steps) != 1:
        raise SetupError(
            f"ci.yml job {gate.job!r} has {len(steps)} steps named {gate.step!r}; expected 1"
        )
    step = steps[0]
    if "shell" in step:
        raise SetupError(
            f"ci.yml step {gate.step!r} sets a shell, which this check does not reproduce"
        )
    run = step.get("run")
    if not isinstance(run, str) or not run.strip():
        raise SetupError(f"ci.yml step {gate.step!r} has no run command")
    if "${{" in run:
        raise SetupError(f"ci.yml step {gate.step!r} uses an expression this check cannot run")
    env = {
        **_env_strings(workflow.get("env"), "ci.yml"),
        **_env_strings(job.get("env"), f"ci.yml job {gate.job}"),
        **_env_strings(step.get("env"), f"ci.yml step {gate.step}"),
    }
    return run, env, str(step.get("working-directory") or ".")


def judge(gate: Gate, seeds: dict[str, str], returncode: int, output: str) -> list[str]:
    """What is wrong with a gate's run; empty when it failed and reported every seed."""
    problems = []
    if returncode == 0:
        problems.append("exited 0 on a tree with planted defects")
    missing = [name for name in seeds if not re.search(gate.report(name), output, re.MULTILINE)]
    if missing:
        problems.append(f"did not report {len(missing)} planted defect(s) as failures: {missing}")
    return problems


def _clean_env() -> dict[str, str]:
    """This process's environment without git's own variables.

    A ``GIT_DIR`` or ``GIT_INDEX_FILE`` inherited from a hook would point the
    copy's git, and the suite's git calls inside it, at the real repository.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(args: list[str], cwd: Path) -> str:
    git = shutil.which("git")
    if not git:
        raise SetupError("git is not on PATH")
    env = {
        **_clean_env(),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "seeded-defects",
        "GIT_AUTHOR_EMAIL": "seeded-defects@example.invalid",
        "GIT_COMMITTER_NAME": "seeded-defects",
        "GIT_COMMITTER_EMAIL": "seeded-defects@example.invalid",
    }
    done = subprocess.run(
        [git, *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise SetupError(f"git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}")
    return done.stdout


def tracked_files(root: Path) -> list[str]:
    names = [n for n in _git(["ls-files", "-z"], root).split("\0") if n]
    if not names:
        raise SetupError(f"git tracks no files in {root}")
    return names


def copy_tree(root: Path, dest: Path, names: list[str]) -> None:
    """The tracked ``names`` in ``root``, as the working tree has them, into ``dest``."""
    for name in names:
        src = root / name
        if not src.exists() and not src.is_symlink():
            continue
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target, follow_symlinks=False)


def plant(dest: Path, seeds: dict[str, str]) -> None:
    for name, content in seeds.items():
        target = dest / name
        if target.exists():
            raise SetupError(f"seed path {name} already exists in the tree")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def run_gate(root: Path, workflow: dict, gate: Gate, workdir: Path) -> list[str]:
    run, env, cwd = command_for(workflow, gate)
    names = tracked_files(root)
    seeds = seeds_for(gate, names)
    copy = workdir / gate.label.replace(" ", "-")
    copy.mkdir()
    copy_tree(root, copy, names)
    plant(copy, seeds)
    # A commit, as in a CI checkout: repository guards in the suite read git.
    _git(["init", "-q"], copy)
    _git(["add", "-A"], copy)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "seeded"], copy)
    bash = shutil.which("bash")
    if not bash:
        raise SetupError("bash is not on PATH; ci.yml's steps run under bash")
    # `bash -e`, as GitHub runs a `run:` step on Linux.
    done = subprocess.run(
        [bash, "-e", "-c", run],
        cwd=copy / cwd,
        env={**_clean_env(), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    output = done.stdout + done.stderr
    problems = judge(gate, seeds, done.returncode, output)
    status = "FAILED as required" if not problems else "DID NOT FAIL AS REQUIRED"
    print(
        f"{gate.label}: `{run.strip()}` rc={done.returncode}, "
        f"{len(seeds)} defect(s) planted -> {status}"
    )
    for problem in problems:
        print(f"  {problem}")
    if problems:
        tail = output.strip().splitlines()[-15:]
        print("  last lines of output:")
        for line in tail:
            print(f"    {line}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failed = []
    try:
        workflow = yaml.safe_load(
            (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        )
        if not isinstance(workflow, dict):
            raise SetupError("ci.yml did not parse as a mapping")
        with tempfile.TemporaryDirectory(prefix="seeded-defects-") as tmp:
            for gate in GATES:
                if run_gate(root, workflow, gate, Path(tmp)):
                    failed.append(gate.label)
    # Every way the check itself can break is "could not run" (2), never
    # "a gate was switched off" (1), and never a pass.
    except (SetupError, OSError, yaml.YAMLError, AttributeError, TypeError) as exc:
        print(f"seeded-defects: cannot run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if failed:
        print(
            f"seeded-defects: {len(failed)} gate(s) passed a tree they should have failed: "
            f"{failed}. Something in the configuration they read switches them off."
        )
        return 1
    print(f"seeded-defects: all {len(GATES)} gates failed on every planted defect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
