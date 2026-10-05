"""A quality tool the project configures must actually be RUN by CI.

STRUCT-02. ``mypy`` was configured under ``[tool.mypy]`` in pyproject.toml
and invoked by no workflow at all, so it reported nothing for as long as that
was true -- including a real ``attr-defined`` bug in
``scripts/ci/check-no-sys-path.py`` that crashed the guard on any malformed
``.py`` file. Ruff, meanwhile, ran over ``sigantry_core/`` and ``tests/`` but
not ``scripts/``, leaving the CI guards themselves unlinted.

A configured-but-unrun tool is worse than an absent one: the config file
advertises a gate that does not exist, and everyone downstream believes the
tree is covered.

This file has now been caught being vacuous TWICE, which is why it is written
the way it is. Both rounds were measured clean -> arms -> clean against a
parsed copy of the real workflows.

Round 1 -- the substring checks passed when:

* the ``types`` job body was replaced with ``pip install mypy ruff`` -- the
  substring is present and nothing executes it;
* the mypy call became a comment plus an ``echo``;
* ``continue-on-error: true`` was added to the mypy step;
* both ruff steps were rewritten with ``--exclude scripts/`` -- the root is
  named in the command precisely because it is being excluded from it.

Round 2 -- after tokenising, review found the rewrite still passed when:

* ``if: false`` was put on the mypy step, or a never-matching ``if:`` on the
  ``types`` job (which makes it SKIPPED -- and a skipped run is counted by
  GitHub as SATISFYING its required context, the exact laundering route this
  file exists to close);
* ``mypy ... | tee mypy.log`` -- steps run under ``bash -e {0}`` with pipefail
  OFF, so the step exits with tee's 0 whatever mypy found;
* ``mypy ... || echo ignored``, and ``set +e`` with a trailing ``exit 0``;
* ``continue-on-error`` or ``if: false`` on the publish workflow's gating job.

So "enforcing" is no longer a denylist of neutering suffixes. An invocation
counts only when it is the WHOLE command -- no shell operator on the line at
all -- inside a step and job that are unconditional, not ``continue-on-error``,
and in a ``run:`` block that neither disables ``errexit`` nor forces ``exit 0``.
Anything cleverer than that fails and asks to be looked at.

Known limit, deliberately left failing loudly: only inline ``run:`` strings are
inspected. Moving a tool into a composite action or a reusable workflow would
read here as "not invoked" and fail. That is the safe direction -- a false
alarm demanding this file be updated, never a silent pass.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass

import pytest
import yaml

# Source roots the project owns and therefore expects its linters to cover.
_LINTED_ROOTS = ("sigantry_core/", "tests/", "scripts/")

# Roots mypy must type check. `tests/` is absent on purpose: it fails module
# resolution before type checking begins (duplicate `conftest` basenames with
# no `__init__.py`), so demanding coverage here would assert a thing that
# cannot currently be true. Widening it is its own piece of work.
_TYPED_ROOTS = ("sigantry_core/", "scripts/")

# The job `name:` that branch protection knows as a required status check.
# Tying the assertion to this string means renaming the job -- which would
# silently stop the required context ever reporting -- fails here too.
_TYPE_CHECK_JOB_NAME = "Type Check (mypy)"
_LINT_JOB_NAME = "Lint & Formatting"

# The gate a publish job must depend on, matched EXACTLY. `.endswith("ci.yml")`
# accepted `noop-ci.yml`, `publish-ci.yml` and `other/repo/.../ci.yml@main` --
# measured -- so a job could report as gated while depending on a workflow
# that runs nothing.
_CI_WORKFLOW_USES = "./.github/workflows/ci.yml"

# A publish that goes through a repo script is still a publish. This matches
# the script's PATH on a run line -- any `scripts/**` `.sh`/`.py` whose path
# says publish or release -- not what the script runs, so it is a guess by name
# in both directions: see `_publish_jobs` for what that misses and misreads.
_PUBLISH_SCRIPT_RE = re.compile(r"scripts/[\w/.-]*(?:publish|release)[\w/.-]*\.(?:sh|py)")

# Any shell operator: an invocation sharing its line with one is not, on its
# own, what decides the step's exit status. A bare `&` is in the list because
# `mypy ... &` backgrounds it and the step exits on the shell, not the tool.
# Order matters: `||` before `|`, `&&` before `&`.
_OPERATOR_RE = re.compile(r"\|\||&&|;|\||&")

# `set +e` / `set +o errexit` turn off the shell's abort-on-failure.
_ERREXIT_OFF_RE = re.compile(r"(?m)^\s*set\s+(?:\+e\b|\+o\s+errexit\b)")

# A bare `exit 0` forces success regardless of what ran before it, and
# `trap 'exit 0' ERR` does the same thing without ever sitting on its own line.
_FORCED_SUCCESS_RE = re.compile(r"(?m)^\s*exit\s+0\s*$")
_TRAP_RE = re.compile(r"(?m)^\s*trap\b.*\bexit\s+0")

# GitHub's default for `run` is `bash -e {0}`: errexit ON. `shell: bash` maps
# to `bash --noprofile --norc -eo pipefail {0}`, which is stricter. Any other
# value -- above all a custom `{0}` command line such as `bash {0}` -- drops
# errexit, and then the step exits on its LAST command rather than the tool.
_ERREXIT_SHELLS = ("bash",)

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# GitHub expression wrapper, e.g. `${{ false }}`.
_EXPRESSION_RE = re.compile(r"^\$\{\{(.*)\}\}$", re.S)

_FALSEY = ("", "false", "0", "off", "no")


def _norm_root(root: str) -> str:
    """Compare roots without tripping over a trailing slash."""
    return root.rstrip("/")


def _load(path: pathlib.Path) -> dict:
    # Explicit encoding: the test matrix includes windows-latest, where the
    # default encoding is not UTF-8, so one non-ASCII character in a comment
    # would otherwise red three legs for a reason unrelated to the code.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".github" / "workflows"


@pytest.fixture(scope="module")
def ci_workflow(workflow_dir: pathlib.Path) -> dict:
    return _load(workflow_dir / "ci.yml")


@pytest.fixture(scope="module")
def all_workflows(workflow_dir: pathlib.Path) -> dict[str, dict]:
    """Every workflow in the repo, so a publish path cannot hide in a new file."""
    out: dict[str, dict] = {}
    for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
        parsed = _load(path)
        # Not `if isinstance(...)`: silently dropping a workflow that did not
        # parse as a mapping would hide any publish job inside it, and this is
        # a guard built to be noisy.
        assert isinstance(parsed, dict), (
            f"{path.name} did not parse as a mapping ({type(parsed).__name__}); "
            "a publish job inside it would be invisible to the scan below."
        )
        out[path.name] = parsed
    assert out, "no workflows parsed; the scan below would be vacuous"
    return out


def _triggers(workflow: dict) -> dict:
    """The ``on:`` block.

    YAML 1.1 resolves a bare ``on`` key to the boolean ``True``, so reading
    ``workflow["on"]`` finds nothing on a real GitHub workflow.
    """
    if True in workflow:
        return workflow[True] or {}
    return workflow.get("on") or {}


def _is_truthy(value: object) -> bool:
    """Whether a YAML ``continue-on-error`` value actually enables it.

    ``0`` and ``${{ false }}`` are disabled forms. Reading them as truthy would
    red a workflow that is in fact enforcing -- a false alarm on correct code,
    which erodes trust in the guard as surely as a silent pass.
    """
    if value is None or value is False:
        return False
    text = str(value).strip()
    match = _EXPRESSION_RE.match(text)
    if match:
        text = match.group(1).strip()
    return text.lower() not in _FALSEY


def _condition(node: dict) -> str:
    """The ``if:`` guarding a job or step, normalised to a string.

    A falsey literal is still returned verbatim: an `if:` on a quality gate is
    reported whatever it says, because "this gate runs only sometimes" is a
    claim a reader must see rather than a value this file should adjudicate.
    """
    value = node.get("if")
    return "" if value is None else str(value).strip()


def _command_lines(run: str) -> list[str]:
    """The executable lines of a ``run:`` block.

    Blank and comment lines are dropped -- a commented-out mypy call does not
    run mypy. Backslash continuations are rejoined first: without that,
    ``shlex.split("mypy \\\\")`` raises and the tool reads as "not invoked".
    """
    lines: list[str] = []
    pending = ""
    for raw in run.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        lines.append((pending + line).strip())
        pending = ""
    if pending.strip():
        lines.append(pending.strip())
    return lines


def _strip_prefix(tokens: list[str]) -> list[str]:
    """Drop a leading ``env`` and any ``VAR=value`` assignments."""
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
    while tokens and _ASSIGNMENT_RE.match(tokens[0]):
        tokens = tokens[1:]
    return tokens


def _split_segments(line: str) -> list[list[str]] | None:
    """Tokenise, THEN split on operator tokens. None if unparseable.

    Splitting the raw string first broke `mypy --exclude 'a|b' ...` mid-quote:
    both halves failed to tokenise, no invocation was recorded, and the suite
    reported "no CI step executes mypy" -- a false alarm that misdescribed its
    own cause, which is the kind that gets a guard switched off. ``shlex``
    strips the quotes, so a quoted metacharacter arrives as ordinary data and
    only a real operator token splits.
    """
    try:
        tokens = shlex.split(line)
    except ValueError:
        return None
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in ("|", "||", "&&", ";", "&"):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return segments


def _argv(segment: str) -> list[str]:
    """Tokenise one command segment, stripping env-assignment prefixes."""
    try:
        tokens = shlex.split(segment)
    except ValueError:  # unbalanced quotes -- not a command we can reason about
        return []
    if tokens and tokens[0] == "env":
        tokens = tokens[1:]
    while tokens and _ASSIGNMENT_RE.match(tokens[0]):
        tokens = tokens[1:]
    return tokens


def _shell_for(workflow: dict, job: dict, step: dict) -> str:
    """The shell a step runs under, following GitHub's precedence.

    Workflow-level ``defaults.run.shell`` was not read at all, so a top-level
    ``bash {0}`` turned errexit off for every step of every job and the guard
    passed clean. ``or {}`` at each hop because a bare ``defaults:`` key parses
    to None, and ``.get`` on None raised an AttributeError out of the guard.
    """
    if step.get("shell"):
        return str(step["shell"]).strip()
    for source in (job, workflow):
        run_defaults = (source.get("defaults") or {}).get("run") or {}
        if run_defaults.get("shell"):
            return str(run_defaults["shell"]).strip()
    return ""


def _normalise_tool_argv(argv: list[str], tool: str) -> tuple[str, ...] | None:
    """Return argv with ``tool`` at position 0, or None if it is not executed.

    Tokenised rather than substring-matched: ``pip install mypy ruff`` MENTIONS
    both tools and executes neither, and that is exactly what the first version
    of this file accepted as proof the tool was wired up.
    """
    if not argv:
        return None
    if argv[0] == tool:
        return tuple(argv)
    if argv[0] in ("python", "python3") and argv[1:3] == ["-m", tool]:
        return tuple(argv[2:])
    return None


@dataclass(frozen=True)
class _Invocation:
    job: str
    job_name: str
    step: str
    line: str
    argv: tuple[str, ...]  # tuple, not list: `frozen=True` generates __hash__
    run_block: str
    shell: str
    compound: bool
    condition: str
    continue_on_error: bool

    def describe(self) -> str:
        return f"job {self.job!r} step {self.step!r}: {self.line!r}"


def _invocations(workflow: dict, tool: str) -> list[_Invocation]:
    """Every place the workflow actually EXECUTES ``tool``."""
    found: list[_Invocation] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        job_coe = _is_truthy(job.get("continue-on-error"))
        job_if = _condition(job)
        for step in job.get("steps") or []:
            run = step.get("run")
            if not isinstance(run, str):
                continue
            step_if = _condition(step)
            step_coe = _is_truthy(step.get("continue-on-error"))
            shell = _shell_for(workflow, job, step)
            for line in _command_lines(run):
                segments = _split_segments(line)
                if segments is None:
                    continue
                compound = len(segments) > 1
                for tokens in segments:
                    argv = _normalise_tool_argv(_strip_prefix(tokens), tool)
                    if argv is None:
                        continue
                    found.append(
                        _Invocation(
                            job=job_id,
                            job_name=str(job.get("name", job_id)),
                            step=str(step.get("name", "<unnamed>")),
                            line=line,
                            argv=argv,
                            run_block=run,
                            shell=shell,
                            compound=compound,
                            condition=job_if or step_if,
                            continue_on_error=job_coe or step_coe,
                        )
                    )
    return found


def _assert_enforcing(call: _Invocation) -> None:
    """A tool that runs but cannot fail the build is not a gate.

    Deliberately strict: the invocation must be the whole command. A legitimate
    compound form would fail here and have to be justified, which is the safe
    direction -- `| tee`, `|| echo` and `&& ...` all leave the step's exit
    status decided by something other than the tool.
    """
    assert not call.compound, (
        f"{call.describe()} shares its line with a shell operator, so the step's "
        "exit status is not the tool's. Steps run under `bash -e {0}` with "
        "pipefail OFF, so `| tee` exits 0 whatever the tool found."
    )
    assert not call.continue_on_error, (
        f"{call.describe()} runs under continue-on-error, so the job reports "
        "success whatever the tool finds."
    )
    assert not call.condition, (
        f"{call.describe()} is guarded by `if: {call.condition}`, so it does not "
        "always run. A job skipped by its own `if:` still reports a check run, "
        "and GitHub counts a skipped run as SATISFYING its required context."
    )
    assert not _ERREXIT_OFF_RE.search(call.run_block), (
        f"{call.describe()} sits in a run block that disables errexit (`set +e`), "
        "so a failure does not fail the step."
    )
    assert not _FORCED_SUCCESS_RE.search(call.run_block), (
        f"{call.describe()} sits in a run block containing a bare `exit 0`, "
        "which forces success regardless of what ran before it."
    )
    assert not _TRAP_RE.search(call.run_block), (
        f"{call.describe()} sits in a run block that traps to `exit 0`, which "
        "forces success without ever putting `exit 0` on a line of its own."
    )
    assert call.shell in ("", *_ERREXIT_SHELLS), (
        f"{call.describe()} overrides the shell to {call.shell!r}. GitHub's "
        "default is `bash -e {0}` (errexit ON); a custom command line such as "
        "`bash {0}` drops it, so the step exits on its LAST command and the "
        "tool's result is discarded."
    )


def _assert_job_enforcing(jobs: dict, job_id: str, why: str) -> None:
    """A gating JOB must be able to fail the thing that depends on it."""
    assert job_id in jobs, (
        f"job {job_id!r} is depended on but does not exist in this workflow "
        "(jobs: {sorted(jobs)}). A renamed or deleted job would otherwise "
        "surface as a bare KeyError from inside the guard meant to explain it."
    )
    job = jobs[job_id]
    assert not _is_truthy(job.get("continue-on-error")), (
        f"job {job_id!r} runs under continue-on-error, so {why}"
    )
    condition = _condition(job)
    assert not condition, (
        f"job {job_id!r} is guarded by `if: {condition}`, so it can be skipped "
        f"-- and a skipped job satisfies `needs:` rather than blocking it, so {why}"
    )


def _excluded_roots(argv: tuple[str, ...]) -> set[str]:
    """Roots removed from the run by ``--exclude`` / ``--extend-exclude``."""
    out: set[str] = set()
    flags = ("--exclude", "--extend-exclude")
    index = 0
    while index < len(argv):
        token = argv[index]
        for flag in flags:
            if token == flag and index + 1 < len(argv):
                out.update(v.strip() for v in argv[index + 1].split(","))
                index += 1
            elif token.startswith(f"{flag}="):
                out.update(v.strip() for v in token[len(flag) + 1 :].split(","))
        index += 1
    return {_norm_root(v) for v in out if v}


# ---------------------------------------------------------------------------
# expiring carve-outs
# ---------------------------------------------------------------------------
#
# Both maps below are job/file -> (reason, review-by date). A carve-out with no
# expiry outlives its reason in silence: `types` stayed exempt from `build`'s
# `needs` after `Type Check (mypy)` became required, because nothing was
# watching. `_assert_not_expired` is unit-tested directly rather than only
# through these maps, so the check is proven able to fail even while a map is
# empty.


def _assert_not_expired(key: str, entry: object, today: dt.date, where: str) -> None:
    assert isinstance(entry, tuple) and len(entry) == 2, (
        f"{where}[{key!r}] must be (reason, 'YYYY-MM-DD'), got {entry!r}"
    )
    reason, review_by = entry
    assert isinstance(reason, str) and reason.strip(), f"{where}[{key!r}] has no reason"
    try:
        deadline = dt.date.fromisoformat(str(review_by))
    except ValueError as exc:
        raise AssertionError(
            f"{where}[{key!r}] has an unparseable review-by date "
            f"{review_by!r}: {exc}. Use YYYY-MM-DD."
        ) from exc
    assert deadline >= today, (
        f"{where}[{key!r}] was due for review on {review_by} "
        f"({(today - deadline).days} day(s) ago). Reason given: {reason!r}. "
        "Either remove the carve-out now that its reason has lapsed, or restate "
        "the reason with a new date."
    )


def _today() -> dt.date:
    # UTC, not local: a local-time deadline flips a day earlier or later
    # depending on which runner picks the job up.
    return dt.datetime.now(dt.UTC).date()


# Quality jobs deliberately NOT gating the artifact build.
#
# A job skipped because a dependency failed still reports a check run, and
# GitHub counts a skipped run as SATISFYING its required context. So a job
# belongs in `build`'s `needs` ONLY IF its own context is required on main;
# adding an unrequired job there converts `build` from a gate into a way to
# hand branch protection a green "Build & Verify Artifacts" on a tree that
# failed that job.
#
# So this map is not "empty and should stay empty" -- a NEW quality job that
# is not a required context belongs HERE, with a date, until it is made one.
# What must never live here is an excuse whose reason has lapsed, which is
# what `types` became once `Type Check (mypy)` was required.
_BUILD_DEPS_EXEMPT: dict[str, tuple[str, str]] = {}

# Publish paths not yet gated by a quality job.
#
# NOTE: `test_carve_outs_have_not_expired` reads the wall clock inside the
# `test` job, which gates `build` and is what `publish-pypi.yml` runs as its
# quality gate. So a lapsed date in EITHER map reds every merge to main and
# blocks every release on a calendar date, with no code change. That is not
# intended; it is issue #23, and it is why no dated entry remains. Whoever adds
# the next one should move the expiry check off those paths first.
# Keyed "<workflow>::<job>", not by filename: a filename key would go on
# excusing any publish job added to that file later, including a production
# one, on a reason recorded about a different job.
_PUBLISH_GATE_EXEMPT: dict[str, tuple[str, str]] = {}


def test_mypy_is_configured(repo_root: pathlib.Path) -> None:
    """Precondition: the project really does configure mypy.

    Without this, the test below could pass vacuously on a repo that had
    simply dropped mypy altogether.
    """
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.mypy]" in pyproject


def test_mypy_is_actually_invoked_by_ci(ci_workflow: dict) -> None:
    """mypy is EXECUTED by a workflow, not merely named in one."""
    calls = _invocations(ci_workflow, "mypy")
    assert calls, (
        "no CI step executes mypy. Naming it in a `pip install` line does not "
        "count -- that mention is what made the first version of this "
        "assertion pass on a job that ran nothing."
    )
    for call in calls:
        _assert_enforcing(call)


def test_mypy_runs_in_the_job_branch_protection_requires(ci_workflow: dict) -> None:
    """The required status check is the job that actually type checks.

    The required context is matched by job NAME. If mypy moves to some other
    job, ``Type Check (mypy)`` keeps reporting green while checking nothing.
    """
    calls = [c for c in _invocations(ci_workflow, "mypy") if c.job_name == _TYPE_CHECK_JOB_NAME]
    assert calls, (
        f"no mypy invocation lives in the job named {_TYPE_CHECK_JOB_NAME!r}, "
        "which is the required status check on main. Either the job was "
        "renamed (the required context will never report again) or mypy moved "
        "out of it (the context reports green having checked nothing)."
    )


def test_mypy_covers_every_typed_root(ci_workflow: dict) -> None:
    """The REQUIRED job checks every root, and excludes none of them.

    Scoped to the job branch protection requires, not to the workflow:
    splitting `mypy sigantry_core/` into `types` and `mypy scripts/` into some
    other job unions to full coverage across the file while the required
    context checks half the tree -- "reports green having checked nothing"
    once more.
    """
    calls = [c for c in _invocations(ci_workflow, "mypy") if c.job_name == _TYPE_CHECK_JOB_NAME]
    assert calls, f"no mypy invocation in the job named {_TYPE_CHECK_JOB_NAME!r}"
    covered: set[str] = set()
    for call in calls:
        operands = {_norm_root(t) for t in call.argv[1:] if not t.startswith("-")}
        covered |= operands - _excluded_roots(call.argv)
    missing = sorted(_norm_root(r) for r in _TYPED_ROOTS if _norm_root(r) not in covered)
    assert not missing, (
        f"the {_TYPE_CHECK_JOB_NAME!r} job does not type check {missing}. "
        f"Commands were: {[c.line for c in calls]}"
    )


def test_ruff_covers_every_source_root(ci_workflow: dict) -> None:
    """Both ruff invocations cover all the roots the project owns.

    ``scripts/`` was missing, so the CI guard scripts -- the files whose whole
    job is to police the repo -- were themselves unchecked.
    """
    # Scoped to the required job, exactly as the mypy assertion is: moving
    # ruff into a non-required job (or renaming `lint`) would otherwise
    # leave every assertion here true while the required
    # `Lint & Formatting` context reported green having linted nothing.
    calls = [c for c in _invocations(ci_workflow, "ruff") if c.job_name == _LINT_JOB_NAME]
    assert calls, f"no ruff invocation in the job named {_LINT_JOB_NAME!r}"
    subcommands = {c.argv[1] for c in calls if len(c.argv) > 1}
    assert "check" in subcommands, f"no `ruff check` is executed; found {sorted(subcommands)}"
    assert "format" in subcommands, f"no `ruff format` is executed; found {sorted(subcommands)}"

    for call in calls:
        _assert_enforcing(call)
        excluded = _excluded_roots(call.argv)
        operands = {_norm_root(t) for t in call.argv[2:] if not t.startswith("-")}
        for root in _LINTED_ROOTS:
            name = _norm_root(root)
            assert name in operands, f"{call.describe()} does not pass {root} as an operand"
            assert name not in excluded, (
                f"{call.describe()} names {root} only to exclude it from the run"
            )


def test_ruff_format_check_does_not_rewrite_files(ci_workflow: dict) -> None:
    """``ruff format`` without ``--check`` reformats and exits 0 -- not a gate."""
    formats = [
        c
        for c in _invocations(ci_workflow, "ruff")
        if c.job_name == _LINT_JOB_NAME and len(c.argv) > 1 and c.argv[1] == "format"
    ]
    # Without this, deleting the format step entirely would leave the loop with
    # nothing to iterate and report PASS -- the condition being policed ("CI can
    # never fail on formatting") is most true exactly when it is absent.
    assert formats, "no `ruff format` invocation to inspect"
    for call in formats:
        assert "--check" in call.argv or "--diff" in call.argv, (
            f"{call.describe()} rewrites files instead of failing on "
            "misformatted ones, so CI can never fail on formatting."
        )


def test_pytest_is_actually_invoked_and_can_fail(ci_workflow: dict) -> None:
    """The `Test (...)` legs are required contexts and now gate the release.

    Nothing policed pytest at all: measured, appending `|| true` to the test
    step passed every assertion in this file while six required contexts went
    green on a failing suite -- and, since publish-pypi depends on this
    workflow, shipped a wheel behind them.
    """
    calls = _invocations(ci_workflow, "pytest")
    assert calls, "no CI step executes pytest"
    for call in calls:
        _assert_enforcing(call)


def test_config_files_do_not_exclude_a_covered_root(repo_root: pathlib.Path) -> None:
    """A root can be dropped from config without touching the CI command.

    `_excluded_roots` reads only `--exclude` on the command line. Putting
    `scripts` in `[tool.ruff] extend-exclude` or `[tool.mypy] exclude` removes
    it just as effectively while every assertion above stays true -- the
    mirror image of the defect this file exists to close.
    """
    with open(repo_root / "pyproject.toml", "rb") as handle:
        config = tomllib.load(handle)
    tools = config.get("tool", {})
    checks = (
        ("ruff", tools.get("ruff", {}), _LINTED_ROOTS),
        ("mypy", tools.get("mypy", {}), _TYPED_ROOTS),
    )
    for name, section, roots in checks:
        patterns = []
        for key in ("exclude", "extend-exclude"):
            value = section.get(key) or []
            patterns.extend(value if isinstance(value, list) else [value])
        for root in roots:
            bare = _norm_root(root)
            hits = [p for p in patterns if _norm_root(str(p).strip("^$/")) == bare]
            assert not hits, (
                f"[tool.{name}] excludes {root} via {hits}, so CI lints or type "
                "checks a tree that silently omits it while the command still "
                "names the root."
            )


def test_build_job_invariants_are_pinned_not_just_commented(ci_workflow: dict) -> None:
    """Two things this PR established in `build` were prose only.

    Measured, both passed the whole suite: reverting `twine check --strict` to
    `twine check`, and replacing `python -m build` with `echo skip`, after
    which `twine check dist/*` matches nothing and the required
    `Build & Verify Artifacts` context goes green having built nothing. A
    release publishes this job's `dist` artifact and runs no twine of its
    own, so this is the strict metadata check on the published files.
    """
    builds = [c for c in _invocations(ci_workflow, "build") if c.job == "build"]
    assert builds, "the build job does not run `python -m build`"
    twine = [c for c in _invocations(ci_workflow, "twine") if c.job == "build"]
    assert twine, "the build job does not run `twine check`"
    for call in twine:
        _assert_enforcing(call)
        assert "--strict" in call.argv, (
            f"{call.describe()} is not --strict. A release publishes this job's "
            "artifact, so a metadata defect that is only a warning here passes "
            "every quality job and surfaces at upload."
        )


def _as_list(needs: object) -> list[str]:
    """``needs:`` is a list OR a bare scalar; ``set("lint")`` is a set of letters."""
    if needs is None:
        return []
    if isinstance(needs, str):
        return [needs]
    return list(needs)


def test_artifact_build_depends_on_every_quality_job(ci_workflow: dict) -> None:
    """Artifacts are not built from a tree that skipped a BLOCKING quality gate."""
    jobs = ci_workflow["jobs"]
    needs = set(_as_list(jobs["build"].get("needs")))
    # A job downstream of build cannot also gate build -- that is a cycle.
    # Transitively: `publish: needs [build]` then `notify: needs [publish]`
    # leaves `notify` downstream too, and demanding it gate `build` is
    # unsatisfiable. Walk the closure rather than only direct dependents.
    downstream: set[str] = set()
    frontier = {"build"}
    while frontier:
        frontier = {
            n
            for n, j in jobs.items()
            if n not in downstream and frontier & set(_as_list(j.get("needs")))
        }
        downstream |= frontier
    quality_jobs = set(jobs) - {"build"} - downstream
    missing = quality_jobs - needs - set(_BUILD_DEPS_EXEMPT)
    assert not missing, (
        f"build does not depend on quality job(s): {sorted(missing)}. "
        "If its context is required on main, add it to build's `needs`. If it "
        "is NOT required, do NOT -- a skipped run satisfies a required context, "
        "so that would launder a failure into a green build. Record it in "
        "_BUILD_DEPS_EXEMPT with a review-by date instead."
    )
    # A dependency that cannot fail is not a gate either -- and neither is
    # `build` itself. `Build & Verify Artifacts` is a required context, so
    # `if: always()` on it reports green on a tree that failed `types`: the
    # laundering route this file is named after, left open on its own job.
    for job_id in [*needs, "build"]:
        _assert_job_enforcing(jobs, job_id, "it cannot block the artifact build")


def test_build_deps_exemptions_still_name_real_jobs(ci_workflow: dict) -> None:
    """An exemption for a job that no longer exists is a stale excuse.

    Without this, renaming or deleting an exempted job leaves a carve-out in
    place that goes on silently excusing whatever later takes that name.
    """
    jobs = ci_workflow["jobs"]
    stale = sorted(set(_BUILD_DEPS_EXEMPT) - set(jobs))
    assert not stale, f"_BUILD_DEPS_EXEMPT names job(s) not in ci.yml: {stale}"

    # And an exemption for a job build ALREADY depends on is dead weight that
    # would silently excuse it if the dependency were later removed.
    needs = set(_as_list(jobs["build"].get("needs")))
    redundant = sorted(set(_BUILD_DEPS_EXEMPT) & needs)
    assert not redundant, f"_BUILD_DEPS_EXEMPT needlessly excuses depended-on job(s): {redundant}"


def test_expiry_check_rejects_a_lapsed_carve_out() -> None:
    """The expiry helper is proven able to fail, not just able to pass.

    Both carve-out maps can be empty, and a loop over an empty map asserts
    nothing. This exercises the helper directly so the guard is never dormant.
    """
    today = dt.date(2026, 9, 22)
    _assert_not_expired("ok", ("still true", "2099-01-01"), today, "_TEST")

    with pytest.raises(AssertionError, match="due for review"):
        _assert_not_expired("lapsed", ("was true once", "2020-01-01"), today, "_TEST")
    with pytest.raises(AssertionError, match="must be"):
        _assert_not_expired("shape", "not a tuple", today, "_TEST")
    with pytest.raises(AssertionError, match="no reason"):
        _assert_not_expired("blank", ("   ", "2099-01-01"), today, "_TEST")
    with pytest.raises(AssertionError, match="unparseable review-by date"):
        _assert_not_expired("typo", ("real reason", "31-12-2026"), today, "_TEST")


def test_carve_outs_have_not_expired() -> None:
    """Every live carve-out is still within its review-by date."""
    today = _today()
    for key, entry in _BUILD_DEPS_EXEMPT.items():
        _assert_not_expired(key, entry, today, "_BUILD_DEPS_EXEMPT")
    for key, entry in _PUBLISH_GATE_EXEMPT.items():
        _assert_not_expired(key, entry, today, "_PUBLISH_GATE_EXEMPT")


def _publish_jobs(workflow: dict) -> list[str]:
    """Jobs that push a distribution to an index, by the mechanisms below only.

    Seen: ``twine upload`` run as ``twine``, ``python -m twine`` or ``python3
    -m twine`` with ``upload`` as its first argument; the action
    ``pypa/gh-action-pypi-publish`` spelled in lower case; and a run line that
    names a ``scripts/**`` ``.sh``/``.py`` path containing ``publish`` or
    ``release``. That last rule reads a name, not what the script runs, so it
    also flags a script that publishes nothing. ``test_publish_job_detection``
    pins the known misses and misreadings as strict xfails; the list is the
    known ones, not all of them. Closing them one spelling at a time does not
    converge, so they wait on a detection that does not depend on how a command
    is spelled (issue #33).

    Uses ``_invocations`` rather than a second hand-rolled tokeniser: the
    duplicate copy had already drifted once (it compared raw tokens to
    ``["twine", "upload"]`` and so missed ``python -m twine upload``), and
    every future tokeniser fix would have had to be made twice.
    """
    uploads = {
        call.job
        for call in _invocations(workflow, "twine")
        if len(call.argv) > 1 and call.argv[1] == "upload"
    }
    out: list[str] = []
    for name, job in (workflow.get("jobs") or {}).items():
        if name in uploads:
            out.append(name)
            continue
        steps = job.get("steps") or []
        if any("pypa/gh-action-pypi-publish" in str(s.get("uses", "")) for s in steps):
            out.append(name)
            continue
        # A publish routed through a repo script is still a publish: moving a
        # job's inline upload into `bash scripts/release/<name>.sh` must not
        # make it vanish from the scan -- the one direction this file must
        # never fail in.
        if any(
            _PUBLISH_SCRIPT_RE.search(line)
            for s in steps
            if isinstance(s.get("run"), str)
            for line in _command_lines(s["run"])
        ):
            out.append(name)
    return out


def _assert_publish_jobs_gated(
    exempt: dict[str, tuple[str, str]], workflows: dict[str, dict]
) -> bool:
    """Every publish job depends on an enforcing ci.yml job unless ``exempt`` names it.

    Only the exact key ``<workflow>::<job-id>`` excuses a job. Returns whether
    any publish job was seen, so the caller can refuse a vacuous scan. The map
    is a parameter so the lookup stays under test while the live map is empty.
    """
    found_any = False
    for filename, workflow in workflows.items():
        jobs = workflow.get("jobs") or {}
        for job_id in _publish_jobs(workflow):
            found_any = True
            if f"{filename}::{job_id}" in exempt:
                continue
            # `needs:` alone is not a gate. `if: always()` on the publish job
            # keeps the dependency and publishes anyway once the gate fails.
            _assert_job_enforcing(
                jobs, job_id, f"{filename}:{job_id} could publish after its gate failed"
            )
            needs = set(_as_list(jobs[job_id].get("needs")))
            gates = [n for n in needs if str(jobs.get(n, {}).get("uses", "")) == _CI_WORKFLOW_USES]
            assert gates, (
                f"{filename}: publish job {job_id!r} does not depend on a job "
                f"that runs ci.yml; its needs are {sorted(needs)}. The "
                "distribution would ship without lint, type or test having run."
            )
            for gate in gates:
                _assert_job_enforcing(
                    jobs, gate, f"{filename}:{job_id} would publish an ungated distribution"
                )
    return found_any


def test_every_publish_path_is_gated_by_the_quality_jobs(
    all_workflows: dict[str, dict], ci_workflow: dict
) -> None:
    """The distribution users install is gated by the quality jobs.

    ``publish-pypi.yml`` ran checkout -> build -> twine -> publish with no
    dependency on lint, test or types. It now depends on ci.yml, and
    publishes the ``dist`` artifact ci.yml's ``build`` job made (see
    ``test_the_release_builds_nothing``).

    Every workflow is scanned, not just ``publish-pypi.yml``: a removed alpha
    workflow once published via ``twine upload`` in an ungated job, and a
    check that looked only at one filename and one action could not see it.
    """
    found_any = _assert_publish_jobs_gated(_PUBLISH_GATE_EXEMPT, all_workflows)
    assert found_any, (
        "no publish path found in any workflow; this scan would be vacuous. "
        "Either the detection is broken or publishing moved somewhere unseen."
    )

    # And the gate must be callable, or the `uses:` reference is broken.
    assert "workflow_call" in _triggers(ci_workflow), (
        "ci.yml is referenced as a reusable workflow but has no "
        "`workflow_call` trigger, so the publish gate cannot run."
    )


# Tools that build a distribution or install one from an index. A workflow
# that publishes runs none of them outside its ci.yml call (issue #20).
_BUILD_OR_INSTALL = ("build", "pyproject-build", "hatch", "pip", "pip3", "uv", "poetry", "flit")


def _action(step: dict) -> str:
    """A step's action without its ``@<ref>``; empty for a ``run:`` step."""
    return str(step.get("uses", "")).split("@", 1)[0]


def test_the_release_builds_nothing(all_workflows: dict[str, dict]) -> None:
    """Issue #20: nothing in a workflow that publishes builds or installs a distribution.

    ``publish-pypi.yml`` rebuilt the sdist and wheel inside its publish job,
    after the quality gate had passed, so the bytes uploaded to PyPI were
    never the bytes ci.yml had checked. The build half is ci.yml's ``build``
    job, run as the workflow's quality gate; the publish job only takes its
    artifact (``test_the_release_publishes_the_artifact_ci_built``).
    """
    found_any = False
    for filename, workflow in all_workflows.items():
        if not _publish_jobs(workflow):
            continue
        found_any = True
        for tool in _BUILD_OR_INSTALL:
            calls = _invocations(workflow, tool)
            assert not calls, (
                f"{filename}: {calls[0].describe()} builds or installs a distribution in a "
                "workflow that publishes. Publish the `dist` artifact ci.yml's `build` job "
                "built and checked, instead of building again after the gate."
            )
    assert found_any, "no publishing workflow found; this check would be vacuous"


def test_the_release_publishes_the_artifact_ci_built(
    all_workflows: dict[str, dict], ci_workflow: dict
) -> None:
    """The publish job uploads ci.yml's ``dist`` artifact, verified against its recorded SHA-256.

    ci.yml's ``build`` job uploads ``dist`` and exposes the SHA-256 of each
    file as the reusable workflow's ``dist-sha256`` output. Each publish job
    downloads that artifact into ``dist/``, checks it against the output of
    the ci.yml job it depends on, and lets the publish action upload that
    directory (its default) rather than another one.
    """
    build = ci_workflow["jobs"]["build"]
    uploads = [s for s in build["steps"] if _action(s) == "actions/upload-artifact"]
    assert [(s.get("with") or {}).get("name") for s in uploads] == ["dist"], (
        "ci.yml's build job must upload exactly one artifact, named `dist`"
    )
    outputs = (_triggers(ci_workflow).get("workflow_call") or {}).get("outputs") or {}
    assert (outputs.get("dist-sha256") or {}).get("value") == (
        "${{ jobs.build.outputs.dist-sha256 }}"
    ), "ci.yml must expose the build job's SHA-256 record to the workflow that calls it"
    found_any = False
    for filename, workflow in all_workflows.items():
        jobs = workflow.get("jobs") or {}
        for job_id in _publish_jobs(workflow):
            found_any = True
            job = jobs[job_id]
            steps = job.get("steps") or []
            downloads = [
                s.get("with") or {} for s in steps if _action(s) == "actions/download-artifact"
            ]
            assert downloads == [{"name": "dist", "path": "dist/"}], (
                f"{filename}:{job_id} must download the `dist` artifact into dist/: {downloads}"
            )
            gates = [
                n
                for n in _as_list(job.get("needs"))
                if str(jobs.get(n, {}).get("uses", "")) == _CI_WORKFLOW_USES
            ]
            records = {f"${{{{ needs.{gate}.outputs.dist-sha256 }}}}" for gate in gates}
            verified = [
                s
                for s in steps
                if set((s.get("env") or {}).values()) & records
                and "sha256sum --check --strict" in str(s.get("run", ""))
            ]
            assert verified, (
                f"{filename}:{job_id} does not check the downloaded files against the "
                "SHA-256 its ci.yml gate recorded"
            )
            for step in steps:
                if _action(step) == "pypa/gh-action-pypi-publish":
                    assert "packages-dir" not in (step.get("with") or {}), (
                        f"{filename}:{job_id} uploads another directory than the verified dist/"
                    )
    assert found_any, "no publish job found; this check would be vacuous"


# ci.yml's `build` job, each action's `@<ref>` dropped
# (tests/prereqs/test_workflow_sha_pinning.py checks the pins). A release
# publishes what this job builds and records, so it is pinned whole, as
# tests/ci/test_name_gate.py pins the publish job that checks the record.
_RECORD_SHA256 = (
    'sha256sum -- * > "$RUNNER_TEMP/dist.sha256"\n'
    'cat "$RUNNER_TEMP/dist.sha256"\n'
    "{\n"
    "  echo 'sha256<<DIST_SHA256'\n"
    '  cat "$RUNNER_TEMP/dist.sha256"\n'
    "  echo 'DIST_SHA256'\n"
    '} >> "$GITHUB_OUTPUT"\n'
)
_EXPECTED_BUILD_JOB = {
    "name": "Build & Verify Artifacts",
    "runs-on": "ubuntu-latest",
    "needs": ["lint", "test", "types"],
    "outputs": {"dist-sha256": "${{ steps.record.outputs.sha256 }}"},
    "steps": [
        {"uses": "actions/checkout"},
        {"uses": "actions/setup-python", "with": {"python-version": "3.11"}},
        {"name": "Install build tooling", "run": "pip install build twine"},
        {"name": "Build sdist & wheel", "run": "python -m build"},
        {"name": "Twine check", "run": "twine check --strict dist/*"},
        {
            "name": "Record the SHA-256 of the distributions",
            "id": "record",
            "working-directory": "dist",
            "run": _RECORD_SHA256,
        },
        {
            "name": "Upload wheel artifact",
            "uses": "actions/upload-artifact",
            "with": {"name": "dist", "path": "dist/*"},
        },
    ],
}


def test_the_build_half_is_pinned_whole(ci_workflow: dict) -> None:
    """ci.yml's ``build`` job is compared whole: a release publishes what it builds and records.

    Measured: deleting the record step, making it record nothing, pointing
    the job output at a step id that does not exist, and uploading only the
    wheel each left every other test green, and each would have failed only
    at the release's verify step. A change to the job updates
    ``_EXPECTED_BUILD_JOB`` in the same commit, where review sees it.
    """
    job = ci_workflow["jobs"]["build"]
    steps = [{**s, "uses": s["uses"].split("@", 1)[0]} if "uses" in s else s for s in job["steps"]]
    assert {**job, "steps": steps} == _EXPECTED_BUILD_JOB


_EXPRESSION = r"^\$\{\{\s*%s\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}$"


def _reference(value: object, context: str) -> tuple[str, str]:
    """``(id, output)`` of an ``${{ <context>.<id>.outputs.<output> }}`` expression."""
    match = re.fullmatch(_EXPRESSION % re.escape(context), str(value))
    assert match, f"{value!r} is not a {context}.<id>.outputs.<name> expression"
    return match[1], match[2]


def _step_outputs(path: pathlib.Path) -> dict[str, str]:
    """``$GITHUB_OUTPUT`` as the runner reads it: ``name=value`` and ``name<<DELIMITER`` blocks."""
    outputs: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").split("\n") if path.exists() else []
    i = 0
    while i < len(lines):
        name, heredoc, delimiter = lines[i].partition("<<")
        if heredoc:
            end = lines.index(delimiter, i + 1)
            outputs[name] = "\n".join(lines[i + 1 : end])
            i = end + 1
            continue
        name, equals, value = lines[i].partition("=")
        if equals:
            outputs[name] = value
        i += 1
    return outputs


def _run_step(step: dict, workspace: pathlib.Path, env: dict[str, str]) -> int:
    """A ``run:`` step as a Linux runner runs one with no ``shell:`` (``bash -e {0}``)."""
    assert "shell" not in step, f"step {step.get('name')!r} sets a shell; simulate that one"
    script = workspace.parent / f"{workspace.name}-step.sh"
    script.write_text(step["run"], encoding="utf-8")
    runner_temp = workspace.parent / f"{workspace.name}-runner-temp"
    runner_temp.mkdir(exist_ok=True)
    base = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(runner_temp), "LC_ALL": "C"}
    for value in (step.get("env") or {}).values():
        assert "${{" not in str(value), "resolve step env expressions before running the step"
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", str(script)],
        cwd=workspace / step.get("working-directory", "."),
        env={**base, **env, **(step.get("env") or {})},
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode


_RELEASE_CASES = [
    "as built",
    "a changed byte",
    "an extra file",
    "a dot file",
    "no sdist",
    "no record",
]


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None,
    reason="these steps run on ubuntu-latest, with GNU find and coreutils",
)
@pytest.mark.parametrize("case", _RELEASE_CASES)
def test_the_release_publishes_the_files_the_build_recorded(
    ci_workflow: dict, all_workflows: dict[str, dict], tmp_path: pathlib.Path, case: str
) -> None:
    """The build job's record and the publish job's check, run from the workflow text.

    The expressions are followed as GitHub resolves them: the verify step's
    ``needs.<gate>.outputs`` -> ci.yml's ``workflow_call`` output -> the
    ``build`` job's output -> the step whose id it names, which is run and
    its ``$GITHUB_OUTPUT`` read. The upload is the ``path`` glob of the
    ``dist`` upload step (hidden files excluded, as upload-artifact does),
    stored relative to the matched files' common directory, and downloaded
    into the download step's ``path``. Only the files as built may pass.
    """
    release = all_workflows["publish-pypi.yml"]["jobs"]
    publish = release["publish"]
    verify = [
        s
        for s in publish["steps"]
        if any("needs." in str(v) for v in (s.get("env") or {}).values())
    ]
    assert len(verify) == 1, "the publish job must check the record in exactly one step"
    ((variable, expression),) = verify[0]["env"].items()
    gate, output = _reference(expression, "needs")
    assert gate in _as_list(publish.get("needs")) and release[gate].get("uses") == _CI_WORKFLOW_USES
    call_outputs = _triggers(ci_workflow)["workflow_call"]["outputs"]
    job_id, job_output = _reference(call_outputs[output]["value"], "jobs")
    build = ci_workflow["jobs"][job_id]
    step_id, step_output = _reference(build["outputs"][job_output], "steps")
    record = [s for s in build["steps"] if s.get("id") == step_id]
    assert len(record) == 1, f"the build job has no single step with id {step_id!r}"

    built = tmp_path / "build"
    (built / "dist").mkdir(parents=True)
    (built / "dist" / "demo-1.2.3-py3-none-any.whl").write_bytes(b"PK\x03\x04 a wheel\n")
    (built / "dist" / "demo-1.2.3.tar.gz").write_bytes(b"\x1f\x8b an sdist\n")
    github_output = tmp_path / "github-output"
    assert _run_step(record[0], built, {"GITHUB_OUTPUT": str(github_output)}) == 0
    recorded = _step_outputs(github_output).get(step_output, "")

    uploads = [
        s
        for s in build["steps"]
        if _action(s) == "actions/upload-artifact" and (s.get("with") or {}).get("name") == "dist"
    ]
    downloads = [s for s in publish["steps"] if _action(s) == "actions/download-artifact"]
    assert len(uploads) == len(downloads) == 1
    matched = [p for p in built.glob(uploads[0]["with"]["path"]) if not p.name.startswith(".")]
    assert matched, "the upload path matches nothing the build wrote"
    stored = pathlib.Path(os.path.commonpath([p.parent for p in matched]))
    published = tmp_path / "publish"
    target = published / downloads[0]["with"]["path"]
    for path in matched:
        (target / path.relative_to(stored)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target / path.relative_to(stored))

    if case == "a changed byte":
        wheel = target / "demo-1.2.3-py3-none-any.whl"
        wheel.write_bytes(wheel.read_bytes().replace(b"a wheel", b"A wheel"))
    elif case == "an extra file":
        (target / "demo-1.2.3-cp311-none-any.whl").write_bytes(b"PK\x03\x04 another\n")
    elif case == "a dot file":
        (target / ".demo-1.2.3.tar.gz").write_bytes(b"\x1f\x8b hidden\n")
    elif case == "no sdist":
        (target / "demo-1.2.3.tar.gz").unlink()
    elif case == "no record":
        recorded = ""
    step = {**verify[0], "env": {}}
    rc = _run_step(step, published, {variable: recorded})
    if case == "as built":
        assert rc == 0, f"the files as built fail the release's check (record: {recorded!r})"
    else:
        assert rc != 0, f"the release's check passes with {case}"


def test_the_release_workflow_runs_only_on_a_published_release(
    all_workflows: dict[str, dict],
) -> None:
    """SEC-14: no manual trigger can start a publish.

    ``workflow_dispatch`` let a run publish whatever ref it was given (the
    ``pypi`` environment's tag policy and reviewer were the only stop). A
    failed release is recovered by re-running all jobs of its run.
    """
    assert _triggers(all_workflows["publish-pypi.yml"]) == {"release": {"types": ["published"]}}


def _grants_id_token(permissions: object) -> bool:
    if permissions == "write-all":
        return True
    return isinstance(permissions, dict) and str(permissions.get("id-token")) == "write"


def test_only_the_publish_job_holds_the_oidc_token_or_the_token_list(
    all_workflows: dict[str, dict], workflow_dir: pathlib.Path
) -> None:
    """The publishing credential and the token list each reach only their pinned jobs.

    ``id-token: write`` is what PyPI trusted publishing accepts, so only the
    publish job may ask for it, and that job restores no cache: a restored
    cache is content written by an earlier run, held in the same job as the
    token. The build half (ci.yml, called with ``contents: read`` and no
    ``secrets:``) holds no secret at all. The token list goes to the two jobs
    ``tests/ci/test_name_gate.py`` pins whole, and nowhere else.
    """
    holders = []
    for filename, workflow in all_workflows.items():
        if _grants_id_token(workflow.get("permissions")):
            holders.append(f"{filename}::<workflow>")
        for job_id, job in (workflow.get("jobs") or {}).items():
            if _grants_id_token(job.get("permissions")):
                holders.append(f"{filename}::{job_id}")
    assert holders == ["publish-pypi.yml::publish"], (
        f"only the publish job may request an OIDC token: {holders}"
    )
    for step in all_workflows["publish-pypi.yml"]["jobs"]["publish"]["steps"]:
        assert _action(step) != "actions/cache", "the job holding the OIDC token restores a cache"
        assert "cache" not in (step.get("with") or {}), (
            f"step {step.get('name')!r} restores a cache in the job holding the OIDC token"
        )
    quality = all_workflows["publish-pypi.yml"]["jobs"]["quality"]
    assert "secrets" not in quality, "the build half must not be handed secrets"
    assert "secrets." not in (workflow_dir / "ci.yml").read_text(encoding="utf-8"), (
        "ci.yml is the build half of a release and must reference no secret"
    )
    readers = {
        path.name: path.read_text(encoding="utf-8").count("secrets.NAME_GATE_TOKENS")
        for path in sorted(workflow_dir.glob("*.y*ml"))
    }
    assert {name: n for name, n in readers.items() if n} == {
        "name-gate.yml": 1,
        "publish-pypi.yml": 1,
    }, f"the token list reaches a job that is not pinned: {readers}"


# GitHub's rule for a job id: a letter or `_`, then letters, digits, `-` or `_`.
_PUBLISH_GATE_KEY_RE = re.compile(r"[^:/]+\.ya?ml::[A-Za-z_][A-Za-z0-9_-]*")


def _assert_exemptions_name_real_publish_jobs(
    exempt: dict[str, tuple[str, str]], workflows: dict[str, dict], where: str
) -> None:
    for key in exempt:
        assert _PUBLISH_GATE_KEY_RE.fullmatch(key), (
            f"{where} key {key!r} must be '<workflow>.yml::<job-id>'. A filename "
            "alone excuses every publish job in that file, now and later, and "
            "any other shape matches no job at all."
        )
        filename, _, job_id = key.partition("::")
        assert filename in workflows, f"{where} names a workflow that does not exist: {filename}"
        assert job_id in _publish_jobs(workflows[filename]), (
            f"{where} excuses {key!r}, which no longer publishes "
            "anything -- the carve-out is dead weight."
        )


def test_publish_gate_exemptions_name_real_publish_jobs(
    all_workflows: dict[str, dict],
) -> None:
    """A carve-out must still name a job that really publishes."""
    _assert_exemptions_name_real_publish_jobs(
        _PUBLISH_GATE_EXEMPT, all_workflows, "_PUBLISH_GATE_EXEMPT"
    )


def test_exemption_check_rejects_a_stale_carve_out() -> None:
    """The check above loops over a map that is empty today; exercise it directly."""
    workflows = {
        "pub.yml": {"jobs": {"up": {"steps": [{"run": "python -m twine upload dist/*"}]}}},
        "quiet.yml": {"jobs": {"lint": {"steps": [{"run": "ruff check ."}]}}},
    }

    def check(key: str) -> None:
        _assert_exemptions_name_real_publish_jobs({key: ("r", "2099-01-01")}, workflows, "_TEST")

    check("pub.yml::up")
    # Each malformed shape fails on its format, not on a later check that
    # happens to reject it with a message about something else.
    for key in ("pub.yml", "pub.yml::", "pub.yml::up::extra", "pub.yml:::up", "::up"):
        with pytest.raises(AssertionError, match=r"^_TEST key .* must be"):
            check(key)
    with pytest.raises(AssertionError, match=r"^_TEST names a workflow that does not exist"):
        check("gone.yml::up")
    with pytest.raises(AssertionError, match=r"^_TEST excuses .* no longer publishes"):
        check("quiet.yml::lint")


# One publish job that no ci.yml job gates: the carve-out alone decides its fate.
_UNGATED_PUBLISH = {"pub.yml": {"jobs": {"up": {"steps": [{"run": "twine upload dist/*"}]}}}}
_UNGATED = "does not depend on a job that runs ci.yml"


def test_publish_gate_carve_out_excuses_the_exact_job() -> None:
    """The gate's carve-out lookup runs although the live map is empty.

    Without the key the scan fails, so the pass with it is the key's doing.
    """
    with pytest.raises(AssertionError, match=_UNGATED):
        _assert_publish_jobs_gated({}, _UNGATED_PUBLISH)
    assert _assert_publish_jobs_gated({"pub.yml::up": ("r", "2099-01-01")}, _UNGATED_PUBLISH)


@pytest.mark.parametrize(
    "key",
    ["pub.yml:up", "pub.yml::other", "pub.yml", "other.yml::up", "pub.yml::up::extra"],
    ids=["single-colon", "other-job", "filename-only", "other-workflow", "extra-suffix"],
)
def test_publish_gate_carve_out_near_miss_excuses_nothing(key: str) -> None:
    """A key that is not exactly ``<workflow>::<job-id>`` leaves the job gated."""
    with pytest.raises(AssertionError, match=_UNGATED):
        _assert_publish_jobs_gated({key: ("r", "2099-01-01")}, _UNGATED_PUBLISH)


def _known_gap(reason: str) -> pytest.MarkDecorator:
    # `raises=AssertionError`: a crash in the detector must not pass for the
    # gap it is pinned as.
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


def _run(line: str) -> list[dict]:
    return [{"run": line}]


@pytest.mark.parametrize(
    ("steps", "publishes"),
    [
        pytest.param(_run("twine upload dist/*"), True, id="twine"),
        pytest.param(_run("python -m twine upload dist/*"), True, id="python-m-twine"),
        pytest.param([{"uses": "pypa/gh-action-pypi-publish@v1"}], True, id="pypa-action"),
        pytest.param(_run("bash scripts/release/publish-thing.sh"), True, id="repo-script"),
        pytest.param(_run("twine check dist/*"), False, id="twine-check"),
        pytest.param(_run("python -m build"), False, id="build"),
        # Known gaps (issue #33). An ungated job spelled like this passes
        # the gate; each would need its own arm, and arms do not converge.
        pytest.param(
            _run("uv publish"),
            True,
            id="gap-uv-publish",
            marks=_known_gap("uv is not a tool the scan knows"),
        ),
        pytest.param(
            _run("python3.12 -m twine upload dist/*"),
            True,
            id="gap-versioned-python",
            marks=_known_gap("only python and python3 are launchers"),
        ),
        pytest.param(
            _run("sudo -E twine upload dist/*"),
            True,
            id="gap-wrapper",
            marks=_known_gap("twine must be the first word"),
        ),
        pytest.param(
            _run("bash -c 'twine upload dist/*'"),
            True,
            id="gap-inline-program",
            marks=_known_gap("a -c program is one quoted word"),
        ),
        pytest.param(
            [{"uses": "PyPA/gh-action-pypi-publish@v1"}],
            True,
            id="gap-action-case",
            marks=_known_gap("the action match is case-sensitive"),
        ),
        pytest.param(
            _run("./release.sh"),
            True,
            id="gap-script-outside-scripts",
            marks=_known_gap("the script rule needs a scripts/ path"),
        ),
        pytest.param(
            _run("make publish"),
            True,
            id="gap-make-target",
            marks=_known_gap("a make target is not read"),
        ),
        pytest.param(
            _run("python scripts/ci/check-release-notes.py"),
            False,
            id="gap-release-named-check",
            marks=_known_gap("the script rule reads a name, not what the script runs"),
        ),
    ],
)
def test_publish_job_detection(steps: list[dict], publishes: bool) -> None:
    """The mechanisms ``_publish_jobs`` sees, seen; its known gaps, pinned.

    No workflow in the repo publishes through a script any more, so without
    the ``repo-script`` case the script branch would go untested. The gaps are
    strict xfails, so closing one fails here until its docstring is updated.
    """
    workflow = {"jobs": {"j": {"steps": steps}}}
    assert (_publish_jobs(workflow) == ["j"]) is publishes


def test_ci_concurrency_group_does_not_cancel_the_release_gate(ci_workflow: dict) -> None:
    """A called run must not share a cancel-in-progress group with its caller.

    ``ci.yml`` is reusable now. Publishing a release that creates tag `v1.2.3`
    fires both `push: tags` on ci.yml and `release: published` on
    publish-pypi.yml, which calls ci.yml. In a called workflow `github.ref` is
    the CALLER's ref, so a group keyed on ref alone puts both in the same group
    and `cancel-in-progress` cancels one. If that is the release's gate, the
    publish job is skipped and nothing ships -- a cancellation, not a red X.
    """
    concurrency = ci_workflow.get("concurrency")
    if not isinstance(concurrency, dict):
        return
    cancel = concurrency.get("cancel-in-progress")
    if not _is_truthy(cancel):
        return
    # Cancellation must be scoped to pull requests. Two runs of the release
    # path on the SAME ref share both concurrency keys, so unconditional
    # cancellation lets the newer kill the older's quality gate; the publish
    # job is then skipped for unsatisfied `needs` and nothing ships, reported
    # as a cancellation rather than a red X.
    expression = str(cancel)
    # A substring test cannot tell `==` from `!=`: measured, `!= 'pull_request'`
    # passed while cancelling exactly the runs this protects, and so did
    # `== 'pull_request' || == 'push'`.
    assert "!=" not in expression and "||" not in expression, (
        f"ci.yml sets cancel-in-progress: {cancel!r}. Inverting or widening the "
        "condition cancels the tag and release-path runs this exists to protect."
    )
    assert "github.event_name == 'pull_request'" in expression, (
        f"ci.yml sets cancel-in-progress: {cancel!r}, which cancels tag and "
        "release-path runs too. Scope it to pull_request exactly."
    )
    group = str(concurrency.get("group", ""))
    assert "github.workflow" in group, (
        f"ci.yml cancels in-progress runs grouped by {group!r}, which does not "
        "distinguish a direct run from one called by another workflow. Include "
        "`github.workflow` (in a called workflow it is the CALLER's name) so a "
        "release's quality gate cannot be cancelled by a push to the same ref."
    )
