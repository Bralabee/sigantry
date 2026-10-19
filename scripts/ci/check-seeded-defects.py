#!/usr/bin/env python3
"""sigantry: every quality gate must FAIL on a tree with a known defect (#47, #22).

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

The ADO lane (#22): the ``Ruff check``, ``Ruff format check`` and ``mypy``
steps of ``templates/jobs/lint-python.yml`` get the same seeds over the same
roots and the same verdict. Each step's ``script:`` is read by its
``displayName``, its ``${{ parameters.X }}`` references are replaced with the
template's own defaults, and it runs as Azure Pipelines' CmdLine task runs it
on a Linux agent: the body written to a file and run as ``bash --noprofile
--norc <file>``, with no errexit, so the step's status is its LAST command's.
A second command after the tool, a narrowed default root, or the mypy step
switched off by default therefore fails this check. The check refuses
(exit 2) a step key, job key, template key or expression it does not model,
rather than ignoring it.

Known limits: a defect is planted per directory, not per file, so
configuration that excludes one existing file is not seen. The ADO steps run
with the tools of this check's own environment, not the template's
``pip install`` step, and on bash only: what the template expander, a
Windows agent (``cmd.exe``) or a real agent would do differently is not
modelled.

Usage:
  python scripts/ci/check-seeded-defects.py [--root .]

Exit codes: 0 every gate failed on its seeds; 1 at least one gate did not;
2 the check could not run (a gate's step is missing from ci.yml or the ADO
template, or uses something this check cannot reproduce, git or bash is not available, a
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
from dataclasses import dataclass, replace
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
    # "github": a step of .github/workflows/ci.yml, run under `bash -e`.
    # "ado": a step of templates/jobs/lint-python.yml, run as CmdLine runs it.
    lane: str = "github"


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


# The ADO lane's gates: the same tools, seeds, roots and reports as the
# GitHub lane's, so the template's default roots are compared with ci.yml's by
# running them. pytest is not here: its ADO step is inline in
# templates/stages/ci.yml, which tests/templates/test_ado_lint_lane_pinned.py
# pins whole.
ADO_TEMPLATE = PurePosixPath("templates/jobs/lint-python.yml")
_ADO_JOB = "python_lint"
_ADO_STEPS = {"ruff check": "Ruff check", "ruff format": "Ruff format check", "mypy": "mypy"}
ADO_GATES: tuple[Gate, ...] = tuple(
    replace(g, label=f"ADO {g.label}", job=_ADO_JOB, step=_ADO_STEPS[g.label], lane="ado")
    for g in GATES
    if g.label in _ADO_STEPS
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


# What the ADO side models. Anything else on the path from the template to
# the step is refused: `condition`, `continueOnError`, `enabled`, `env`,
# `workingDirectory`, `target`, `failOnStderr`, a job `pool` or `variables`,
# a template `variables` or `steps`, and every other expression.
_ADO_TEMPLATE_KEYS = {"parameters", "jobs"}
_ADO_JOB_KEYS = {"job", "displayName", "steps"}
_ADO_STEP_KEYS = {"script", "displayName"}
_ADO_TYPE_CHECK_IF = "${{ if eq(parameters.enableTypeCheck, true) }}"
_ADO_PARAM = re.compile(r"\$\{\{\s*parameters\.([A-Za-z_]\w*)\s*\}\}")


def _ado_defaults(template: dict) -> dict[str, object]:
    params = template.get("parameters")
    if not isinstance(params, list):
        raise SetupError(f"{ADO_TEMPLATE} has no parameters list")
    out: dict[str, object] = {}
    for param in params:
        if not isinstance(param, dict) or "name" not in param or "default" not in param:
            raise SetupError(f"{ADO_TEMPLATE}: parameter {param!r} has no name and default")
        out[str(param["name"])] = param["default"]
    return out


def _ado_expand(steps: object, params: dict[str, object]) -> list[dict]:
    """The steps the template expander emits with the defaults, in order.

    The one insertion the template uses, the mypy step's ``${{ if }}``, is
    evaluated; any other expression used as a key is refused.
    """
    if not isinstance(steps, list):
        raise SetupError(f"{ADO_TEMPLATE}: steps is not a list")
    out: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            raise SetupError(f"{ADO_TEMPLATE}: step {step!r} is not a mapping")
        if list(step) == [_ADO_TYPE_CHECK_IF]:
            flag = params.get("enableTypeCheck")
            if not isinstance(flag, bool):
                raise SetupError(f"{ADO_TEMPLATE}: enableTypeCheck's default is not a boolean")
            if flag:
                out.extend(_ado_expand(step[_ADO_TYPE_CHECK_IF], params))
            continue
        if any("${{" in str(key) for key in step):
            raise SetupError(f"{ADO_TEMPLATE}: step {step!r} uses an expression as a key")
        out.append(step)
    return out


def ado_command_for(template: dict, gate: Gate) -> tuple[str, dict[str, str], str]:
    """The gate's ``script:`` body from the ADO job template, with its defaults substituted."""
    extra = set(template) - _ADO_TEMPLATE_KEYS
    if extra:
        raise SetupError(f"{ADO_TEMPLATE} sets {sorted(extra)}, which this check does not model")
    params = _ado_defaults(template)
    jobs = [
        j for j in template.get("jobs") or [] if isinstance(j, dict) and j.get("job") == gate.job
    ]
    if len(jobs) != 1:
        raise SetupError(f"{ADO_TEMPLATE} has {len(jobs)} jobs named {gate.job!r}; expected 1")
    job = jobs[0]
    extra = set(job) - _ADO_JOB_KEYS
    if extra:
        raise SetupError(
            f"{ADO_TEMPLATE} job {gate.job!r} sets {sorted(extra)}, which this check does not model"
        )
    steps = [s for s in _ado_expand(job.get("steps"), params) if s.get("displayName") == gate.step]
    if len(steps) != 1:
        raise SetupError(
            f"{ADO_TEMPLATE} job {gate.job!r} has {len(steps)} steps named {gate.step!r} "
            "with the defaults; expected 1"
        )
    step = steps[0]
    extra = set(step) - _ADO_STEP_KEYS
    if extra:
        raise SetupError(
            f"{ADO_TEMPLATE} step {gate.step!r} sets {sorted(extra)}, "
            "which this check does not model"
        )
    body = step.get("script")
    if not isinstance(body, str) or not body.strip():
        raise SetupError(f"{ADO_TEMPLATE} step {gate.step!r} has no script")

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise SetupError(f"{ADO_TEMPLATE} step {gate.step!r} names an undeclared {name!r}")
        value = params[name]
        if not isinstance(value, str):
            raise SetupError(f"{ADO_TEMPLATE} step {gate.step!r} substitutes non-string {name!r}")
        return value

    run = _ADO_PARAM.sub(substitute, body)
    # `$(name)` is an ADO macro as well as bash's command substitution, and
    # this check cannot tell which the agent would make of it.
    if "${{" in run or "$[" in run or "$(" in run:
        raise SetupError(
            f"{ADO_TEMPLATE} step {gate.step!r} uses an expression this check cannot run"
        )
    return run, {}, "."


def run_command(gate: Gate, run: str, cwd: Path, env: dict[str, str], scratch: Path):
    """Run a gate's command the way its lane's CI runs it."""
    bash = shutil.which("bash")
    if not bash:
        raise SetupError("bash is not on PATH; both lanes' steps run under bash")
    if gate.lane == "ado":
        # CmdLine on Linux: the body in a file, `bash --noprofile --norc
        # <file>`, no errexit; the step fails only on the last exit status.
        body = scratch / f"{gate.label.replace(' ', '-')}.sh"
        body.write_text(run, encoding="utf-8")
        argv = [bash, "--noprofile", "--norc", str(body)]
    else:
        # `bash -e`, as GitHub runs a `run:` step on Linux.
        argv = [bash, "-e", "-c", run]
    return subprocess.run(
        argv,
        cwd=cwd,
        env={**_clean_env(), **env},
        capture_output=True,
        text=True,
        check=False,
    )


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


def run_gate(root: Path, sources: dict[str, dict], gate: Gate, workdir: Path) -> list[str]:
    if gate.lane == "ado":
        run, env, cwd = ado_command_for(sources["ado"], gate)
    else:
        run, env, cwd = command_for(sources["github"], gate)
    names = tracked_files(root)
    seeds = seeds_for(gate, names)
    copy = workdir / gate.label.replace(" ", "-")
    scratch = workdir / f"{copy.name}-scratch"
    scratch.mkdir()
    copy.mkdir()
    copy_tree(root, copy, names)
    plant(copy, seeds)
    # A commit, as in a CI checkout: repository guards in the suite read git.
    _git(["init", "-q"], copy)
    _git(["add", "-A"], copy)
    _git(["-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "seeded"], copy)
    done = run_command(gate, run, copy / cwd, env, scratch)
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
        sources = {}
        for lane, path in (
            ("github", PurePosixPath(".github/workflows/ci.yml")),
            ("ado", ADO_TEMPLATE),
        ):
            doc = yaml.safe_load((root / path).read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise SetupError(f"{path} did not parse as a mapping")
            sources[lane] = doc
        with tempfile.TemporaryDirectory(prefix="seeded-defects-") as tmp:
            for gate in (*GATES, *ADO_GATES):
                if run_gate(root, sources, gate, Path(tmp)):
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
    print(f"seeded-defects: all {len(GATES) + len(ADO_GATES)} gates failed on every planted defect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
