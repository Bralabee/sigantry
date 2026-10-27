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

# A job publishes only if it can obtain a publishing credential, and there are
# two routes to one: an OIDC token (`id-token: write`), which is what PyPI
# trusted publishing accepts, or a secret. Both are small, enumerable surfaces,
# where the commands that upload are not (issue #33: a detector that read
# those spellings missed some and misread others, arm after arm). So the gate is on
# the credential. The jobs below hold an OIDC token for `azure/login` and
# cannot publish (PyPI's trusted publisher is bound to publish-pypi.yml and
# its `pypi` environment); every other holder is a publishing job.
_AZURE_LOGIN_HOLDERS = frozenset(
    {
        "sigantry-cd.yml::deploy",
        "sigantry-cd.yml::integration",
        "sigantry-cd.yml::promote",
        "drift-check.yml::drift_check",
    }
)

# The only secrets a workflow may reference. None of them can publish to an
# index: the token list feeds the name gate, GITHUB_TOKEN is scoped to this
# repository, and the drift-check notify job's sink settings (#131) only
# deliver an alert: the Teams and Slack webhooks post to a channel, and the
# SMTP settings send mail through the operator's own relay. A new secret
# fails `test_workflows_reference_only_allowed_secrets` and has to be added
# here, in a reviewed change, with what it is for.
_ALLOWED_SECRETS = frozenset(
    {
        "NAME_GATE_TOKENS",
        "GITHUB_TOKEN",
        "SIGANTRY_TEAMS_WEBHOOK",
        "SIGANTRY_SLACK_WEBHOOK",
        "SIGANTRY_SMTP_HOST",
        "SIGANTRY_SMTP_PORT",
        "SIGANTRY_SMTP_USER",
        "SIGANTRY_SMTP_PASSWORD",
        "SIGANTRY_SMTP_FROM",
        "SIGANTRY_SMTP_TO",
    }
)

# The repository variables a workflow may read: none. A variable is shown in
# logs and to anyone with read access, so it must never hold a credential, but
# nothing in GitHub stops one from doing so. Pinned empty for the same reason
# as the secrets above.
_ALLOWED_VARS: frozenset[str] = frozenset()

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
# The map below is job -> (reason, review-by date). A carve-out with no
# expiry outlives its reason in silence: `types` stayed exempt from `build`'s
# `needs` after `Type Check (mypy)` became required, because nothing was
# watching. `_assert_not_expired` is unit-tested directly rather than only
# through the map, so the check is proven able to fail even while it is
# empty.
#
# The date is compared with the COMMIT under test, not with today (issue
# #23). This test runs in the `test` job, which gates `build`, which is what
# a release publishes; against the wall clock, a lapsed date would red every
# merge and block every release on a calendar day with no code change. Against
# the commit date, a commit that passed once passes on every re-run and every
# release, and a lapsed entry fails on the first commit made after its date,
# in the pull request that carries it.


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


def _commit_date(root: pathlib.Path) -> dt.date:
    """The UTC date of the commit checked out at ``root``.

    The committer date, so a rebase or a merge commit counts as new. UTC, not
    local: a local-time date flips a day earlier or later depending on which
    runner picks the job up. No git, or no commit, fails: skipping would turn
    the expiry check off without a word.
    """
    git = shutil.which("git")
    assert git, "git is not on PATH; the carve-out expiry check reads the commit date"
    done = subprocess.run(
        [git, "-C", str(root), "log", "-1", "--format=%ct", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0 and done.stdout.strip().isdigit(), (
        f"could not read the commit date at {root} (rc {done.returncode}): "
        f"{done.stderr.strip() or done.stdout.strip()!r}"
    )
    return dt.datetime.fromtimestamp(int(done.stdout.strip()), dt.UTC).date()


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


def test_carve_outs_have_not_expired(repo_root: pathlib.Path) -> None:
    """Every live carve-out was within its review-by date when this commit was made."""
    committed = _commit_date(repo_root)
    for key, entry in _BUILD_DEPS_EXEMPT.items():
        _assert_not_expired(key, entry, committed, "_BUILD_DEPS_EXEMPT")


def test_expiry_follows_the_commit_not_the_clock(tmp_path: pathlib.Path) -> None:
    """Issue #23: the same entry passes on an old commit and fails on a new one.

    Both commits are judged on the same day (today), so the difference is the
    commit date alone: the wall clock does not decide.
    """
    git = shutil.which("git")
    assert git, "git is not on PATH"
    entry = ("not required yet", "2030-06-30")

    def commit_at(when: str) -> pathlib.Path:
        repo = tmp_path / when[:10]
        repo.mkdir()
        # Nothing from the caller's git: a GIT_DIR or GIT_INDEX_FILE inherited
        # from a hook would point these commands at the real repository, and
        # a global config can add hooks or signing that --no-verify does not
        # turn off.
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        for argv in (
            ["init", "-q"],
            [
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "--allow-empty",
                "--no-verify",
                "-m",
                "c",
            ],
        ):
            subprocess.run([git, "-C", str(repo), *argv], env=env, check=True)
        return repo

    before = _commit_date(commit_at("2030-06-30T23:30:00+00:00"))
    assert before == dt.date(2030, 6, 30)
    _assert_not_expired("job", entry, before, "_TEST")
    after = _commit_date(commit_at("2030-07-01T00:30:00+00:00"))
    with pytest.raises(AssertionError, match="due for review"):
        _assert_not_expired("job", entry, after, "_TEST")
    with pytest.raises(AssertionError, match="could not read the commit date"):
        _commit_date(tmp_path / "no-repo-here")


def _grants_id_token(permissions: object) -> bool:
    if permissions == "write-all":
        return True
    return isinstance(permissions, dict) and str(permissions.get("id-token")) == "write"


def _id_token_holders(workflows: dict[str, dict]) -> set[str]:
    """``<workflow>::<job>`` for every job that can request an OIDC token.

    A workflow-level grant reaches every job that does not set its own
    ``permissions``, so it is reported as ``<workflow>::<workflow>`` and also
    expanded to those jobs: the pin below must see the marker, and the
    publishing-job scan must see the jobs.
    """
    holders: set[str] = set()
    for filename, workflow in workflows.items():
        jobs = workflow.get("jobs") or {}
        workflow_grant = _grants_id_token(workflow.get("permissions"))
        if workflow_grant:
            holders.add(f"{filename}::<workflow>")
        for job_id, job in jobs.items():
            own = job.get("permissions")
            if _grants_id_token(own) or (workflow_grant and own is None):
                holders.add(f"{filename}::{job_id}")
    return holders


def _publishing_jobs(workflows: dict[str, dict]) -> list[tuple[str, str]]:
    """``(workflow, job)`` for every job that holds a credential PyPI accepts.

    Secrets are not a route (``test_workflows_reference_only_allowed_secrets``
    allows none that can publish), so this is the OIDC holders other than the
    pinned ``azure/login`` jobs. It is decided by the credential, not by how
    the upload command is spelled (issue #33).
    """
    out = []
    for holder in sorted(_id_token_holders(workflows) - _AZURE_LOGIN_HOLDERS):
        filename, _, job_id = holder.partition("::")
        if job_id != "<workflow>":
            out.append((filename, job_id))
    return out


def test_publishing_jobs_are_found_by_credential() -> None:
    """The scan follows the credential, whatever the upload is spelled as.

    Each spelling below once passed an ungated job through the command
    scan this replaced (issue #33); here none of them matters. A job with the
    token publishes; the same steps without it do not.
    """
    spellings = [
        [{"run": "twine upload dist/*"}],
        [{"run": "uv publish"}],
        [{"run": "sudo -E python3.12 -m twine upload dist/*"}],
        [{"run": "bash -c 'twine upload dist/*'"}],
        [{"uses": "PyPA/gh-action-pypi-publish@v1"}],
        [{"run": "./release.sh"}],
        [{"run": "make publish"}],
    ]
    token = {"id-token": "write", "contents": "read"}
    for steps in spellings:
        held = {"pub.yml": {"jobs": {"up": {"permissions": token, "steps": steps}}}}
        assert _publishing_jobs(held) == [("pub.yml", "up")], steps
        assert _publishing_jobs({"pub.yml": {"jobs": {"up": {"steps": steps}}}}) == [], steps
    # A workflow-wide grant reaches the jobs that do not narrow it.
    wide = {
        "pub.yml": {
            "permissions": "write-all",
            "jobs": {"up": {"steps": []}, "lint": {"permissions": {}, "steps": []}},
        }
    }
    assert _publishing_jobs(wide) == [("pub.yml", "up")]
    # A check named like a release holds no token and is not a publish.
    named = {
        "ci.yml": {
            "jobs": {"lint": {"steps": [{"run": "python scripts/ci/check-release-notes.py"}]}}
        }
    }
    assert _publishing_jobs(named) == []
    # The azure/login holders are not publishing jobs, by name only.
    azure = {"drift-check.yml": {"jobs": {"drift_check": {"permissions": token, "steps": []}}}}
    assert _publishing_jobs(azure) == []


def _assert_publishing_jobs_gated(workflows: dict[str, dict]) -> bool:
    """Every publishing job depends on an enforcing ci.yml job.

    Returns whether any publishing job was seen, so the caller can refuse a
    vacuous scan.
    """
    found_any = False
    for filename, job_id in _publishing_jobs(workflows):
        found_any = True
        jobs = workflows[filename].get("jobs") or {}
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


_UNGATED = "does not depend on a job that runs ci.yml"


def test_an_ungated_publishing_job_fails_the_gate() -> None:
    """The gate check can fail, and passes only on a real ci.yml dependency."""
    token = {"id-token": "write"}
    ungated = {"pub.yml": {"jobs": {"up": {"permissions": token, "steps": []}}}}
    with pytest.raises(AssertionError, match=_UNGATED):
        _assert_publishing_jobs_gated(ungated)
    near = {
        "pub.yml": {
            "jobs": {
                "gate": {"uses": "./.github/workflows/noop-ci.yml"},
                "up": {"needs": ["gate"], "permissions": token, "steps": []},
            }
        }
    }
    with pytest.raises(AssertionError, match=_UNGATED):
        _assert_publishing_jobs_gated(near)
    gated = {
        "pub.yml": {
            "jobs": {
                "gate": {"uses": _CI_WORKFLOW_USES},
                "up": {"needs": "gate", "permissions": token, "steps": []},
            }
        }
    }
    assert _assert_publishing_jobs_gated(gated)
    gated["pub.yml"]["jobs"]["up"]["if"] = "always()"
    with pytest.raises(AssertionError, match="could publish after its gate failed"):
        _assert_publishing_jobs_gated(gated)


def test_every_publish_path_is_gated_by_the_quality_jobs(
    all_workflows: dict[str, dict], ci_workflow: dict
) -> None:
    """The distribution users install is gated by the quality jobs.

    ``publish-pypi.yml`` ran checkout -> build -> twine -> publish with no
    dependency on lint, test or types. It now depends on ci.yml, and
    publishes the ``dist`` artifact ci.yml's ``build`` job made (see
    ``test_the_release_builds_nothing``).

    Every workflow is scanned, not just ``publish-pypi.yml``, and a job is
    found by the credential it holds, not by its commands (issue #33).
    """
    found_any = _assert_publishing_jobs_gated(all_workflows)
    assert found_any, (
        "no job in any workflow holds a publishing credential; this scan would "
        "be vacuous. Either the detection is broken or publishing moved somewhere unseen."
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
    publishing = sorted({filename for filename, _ in _publishing_jobs(all_workflows)})
    assert publishing, "no publishing workflow found; this check would be vacuous"
    for filename in publishing:
        workflow = all_workflows[filename]
        for tool in _BUILD_OR_INSTALL:
            calls = _invocations(workflow, tool)
            assert not calls, (
                f"{filename}: {calls[0].describe()} builds or installs a distribution in a "
                "workflow that publishes. Publish the `dist` artifact ci.yml's `build` job "
                "built and checked, instead of building again after the gate."
            )


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
    publishing = _publishing_jobs(all_workflows)
    assert publishing, "no publish job found; this check would be vacuous"
    for filename, job_id in publishing:
        jobs = all_workflows[filename].get("jobs") or {}
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
    "needs": ["lint", "test", "types", "seeded"],
    "outputs": {"dist-sha256": "${{ steps.record.outputs.sha256 }}"},
    "steps": [
        {"uses": "actions/checkout", "with": {"persist-credentials": False}},
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


def test_only_the_publish_job_holds_the_oidc_token_or_the_token_list(
    all_workflows: dict[str, dict],
) -> None:
    """The publishing credential and the token list each reach only their pinned jobs.

    ``id-token: write`` is what PyPI trusted publishing accepts, so the
    publish job asks for it, and that job restores no cache: a restored
    cache is content written by an earlier run, held in the same job as the
    token. The build half (ci.yml, called with ``contents: read`` and no
    ``secrets:``) is handed no secret at all. The token list goes to the two jobs
    ``tests/ci/test_name_gate.py`` pins whole, and nowhere else.

    The consumer-facing reusable workflows (``sigantry-cd.yml``,
    ``drift-check.yml``) sign in to Azure with ``azure/login`` through OIDC,
    which needs the same permission. Each such job is pinned here by name,
    holds the permission at job level (never workflow-wide), runs
    ``azure/login`` and restores no cache, and neither workflow has a
    ``release`` trigger. PyPI's trusted publisher is bound to
    ``publish-pypi.yml`` and its ``pypi`` environment, so a token minted in
    those jobs cannot publish; the set stays explicit all the same.
    """
    holders = _id_token_holders(all_workflows)
    assert holders == {"publish-pypi.yml::publish", *_AZURE_LOGIN_HOLDERS}, (
        f"only the publish job and the azure/login jobs may request an OIDC token: "
        f"{sorted(holders)}"
    )
    for holder in sorted(_AZURE_LOGIN_HOLDERS):
        filename, job_id = holder.split("::")
        workflow = all_workflows[filename]
        assert "release" not in _triggers(workflow), f"{filename} must not run on a release"
        steps = workflow["jobs"][job_id]["steps"]
        assert any(_action(s) == "azure/login" for s in steps), (
            f"{holder}: the token is for azure/login"
        )
        for step in steps:
            assert _action(step) != "actions/cache", (
                f"{holder} restores a cache beside the OIDC token"
            )
            assert "cache" not in (step.get("with") or {}), (
                f"{holder}: step {step.get('name')!r} restores a cache beside the OIDC token"
            )
    for step in all_workflows["publish-pypi.yml"]["jobs"]["publish"]["steps"]:
        assert _action(step) != "actions/cache", "the job holding the OIDC token restores a cache"
        assert "cache" not in (step.get("with") or {}), (
            f"step {step.get('name')!r} restores a cache in the job holding the OIDC token"
        )
    quality = all_workflows["publish-pypi.yml"]["jobs"]["quality"]
    assert "secrets" not in quality, "the build half must not be handed secrets"
    assert _context_uses(all_workflows["ci.yml"], "secrets") == (set(), []), (
        "ci.yml is the build half of a release and must reference no secret"
    )
    # Per job, through the same case-insensitive scan as the allow-list: a
    # text count of `secrets.NAME_GATE_TOKENS` missed `Secrets.name_gate_tokens`.
    readers = []
    for filename, workflow in all_workflows.items():
        top = {k: v for k, v in workflow.items() if k != "jobs"}
        if "NAME_GATE_TOKENS" in _context_uses(top, "secrets")[0]:
            readers.append(f"{filename}::<workflow>")
        for job_id, job in (workflow.get("jobs") or {}).items():
            if "NAME_GATE_TOKENS" in _context_uses(job, "secrets")[0]:
                readers.append(f"{filename}::{job_id}")
    assert sorted(readers) == ["name-gate.yml::name-gate", "publish-pypi.yml::publish"], (
        f"the token list reaches a job that is not pinned: {readers}"
    )


def _expressions(text: str) -> list[str]:
    """Every ``${{ ... }}`` in ``text``, ending at the first ``}}`` outside a string.

    A regex that stops at the first ``}}`` ends early on ``format('{0}}}', ...)``
    and never sees what follows. Expression strings are single-quoted, with
    ``''`` for a quote. An expression left open runs to the end of the text, so
    nothing after an unbalanced quote goes unread.
    """
    out = []
    start = text.find("${{")
    while start != -1:
        i = start + 3
        quoted = False
        while i < len(text):
            if text[i] == "'":
                if quoted and text[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                quoted = not quoted
            elif not quoted and text.startswith("}}", i):
                break
            i += 1
        out.append(text[start + 3 : i])
        start = text.find("${{", i + 2)
    return out


def _context_uses(node: object, context: str) -> tuple[set[str], list[str]]:
    """The names read from ``context`` (``secrets`` or ``vars``), and every use naming none.

    GitHub expressions are case-insensitive, so names are compared upper-cased.
    Any reference other than ``<context>.<NAME>`` -- an index such as
    ``secrets['X']``, ``toJSON(secrets)``, a computed index -- is reported as a
    problem rather than parsed, as is ``secrets: inherit``, which hands a
    called workflow every secret the caller can read. A property of another
    context that happens to be called the same (``needs.secrets.result``) is
    not the context.
    """
    word = re.compile(rf"(?<![\w.]){context}\b", re.I)
    ref = re.compile(rf"{context}\.([A-Za-z_][A-Za-z0-9_]*)", re.I)
    names: set[str] = set()
    problems: list[str] = []

    def scan(text: str, where: str) -> None:
        for match in word.finditer(text):
            found = ref.match(text, match.start())
            if found:
                names.add(found.group(1).upper())
            else:
                problems.append(f"{where}: {text.strip()!r}")

    def walk(node: object, where: str, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if context == "secrets" and str(k).lower() == "secrets" and not isinstance(v, dict):
                    problems.append(f"{where}.secrets: {v!r}")
                walk(v, f"{where}.{k}", str(k))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{where}[{i}]", key)
        elif isinstance(node, str):
            for expression in _expressions(node):
                scan(expression, where)
            if key == "if" and "${{" not in node:
                scan(node, where)

    walk(node, "")
    return names, problems


def test_workflows_reference_only_allowed_secrets(all_workflows: dict[str, dict]) -> None:
    """No workflow can reach a secret outside ``_ALLOWED_SECRETS``, or any variable.

    With the OIDC holders pinned above, a secret is the only other way a job
    gets a credential an index accepts, so this is what keeps the publishing
    jobs the ones ``_publishing_jobs`` finds (issue #33). A repository
    variable is not meant to hold a credential, but nothing stops one from
    holding it, so variables are held to an allow-list too, and it is empty.
    PyPI accepts this project only through trusted publishing from
    publish-pypi.yml's ``pypi`` environment, whose reviewer approves each
    upload; no API token exists.
    """
    seen: set[str] = set()
    for filename, workflow in all_workflows.items():
        for context, allowed in (("secrets", _ALLOWED_SECRETS), ("vars", _ALLOWED_VARS)):
            names, problems = _context_uses(workflow, context)
            assert not problems, (
                f"{filename} reads {context} in a form this check cannot enumerate: "
                f"{problems}. Reference each one as `{context}.<NAME>`."
            )
            extra = sorted(names - allowed)
            assert not extra, (
                f"{filename} references {context} {extra} outside the allow-list "
                f"{sorted(allowed)}. Either can carry a publishing credential: add it "
                "to the allow-list with what it is for, in a reviewed change."
            )
            if context == "secrets":
                seen |= names
    # The scan must see the references that are known to be there.
    assert {"NAME_GATE_TOKENS", "GITHUB_TOKEN"} <= seen, (
        f"the secret scan found {sorted(seen)}; it is not reading the workflows"
    )


@pytest.mark.parametrize(
    ("workflow", "names", "problem"),
    [
        pytest.param({"env": {"T": "${{ secrets.PYPI_TOKEN }}"}}, {"PYPI_TOKEN"}, False, id="env"),
        pytest.param(
            {"jobs": {"j": {"steps": [{"run": "x ${{ Secrets.pypi_token }} y"}]}}},
            {"PYPI_TOKEN"},
            False,
            id="case",
        ),
        pytest.param({"jobs": {"j": {"secrets": "inherit"}}}, set(), True, id="inherit"),
        pytest.param({"env": {"A": "${{ toJSON(secrets) }}"}}, set(), True, id="tojson"),
        pytest.param({"env": {"A": "${{ secrets['PYPI_TOKEN'] }}"}}, set(), True, id="index"),
        pytest.param(
            {"jobs": {"j": {"steps": [{"if": "secrets.X != ''", "run": "true"}]}}},
            {"X"},
            False,
            id="bare-if",
        ),
        pytest.param(
            {"jobs": {"j": {"secrets": {"T": "${{ secrets.GITHUB_TOKEN }}"}}}},
            {"GITHUB_TOKEN"},
            False,
            id="passed-explicitly",
        ),
        pytest.param(
            {"jobs": {"j": {"steps": [{"run": "echo no secrets here"}]}}}, set(), False, id="prose"
        ),
        pytest.param(
            {"env": {"A": "${{ format('{0}}}', secrets.PYPI_TOKEN) }}"}},
            {"PYPI_TOKEN"},
            False,
            id="braces-in-a-string",
        ),
        pytest.param(
            {"env": {"A": "${{ format('it''s }}', secrets.PYPI_TOKEN) }}"}},
            {"PYPI_TOKEN"},
            False,
            id="escaped-quote",
        ),
        pytest.param(
            {"env": {"A": "${{ format('{0}', 'x) }} ${{ secrets.PYPI_TOKEN }}"}},
            {"PYPI_TOKEN"},
            False,
            id="unbalanced-quote",
        ),
        pytest.param(
            {"jobs": {"j": {"if": "needs.secrets.result == 'success'"}}},
            set(),
            False,
            id="property-named-secrets",
        ),
    ],
)
def test_secret_scan_sees_every_reference_form(
    workflow: dict, names: set[str], problem: bool
) -> None:
    """Each way to read the secrets context is either named or refused."""
    found, problems = _context_uses(workflow, "secrets")
    assert found == names
    assert bool(problems) is problem, problems


def test_variable_scan_sees_a_variable() -> None:
    """``vars`` gets the same scan: a token in a variable is seen and named."""
    workflow = {"jobs": {"j": {"steps": [{"run": "twine upload -p ${{ VARS.pypi_token }}"}]}}}
    assert _context_uses(workflow, "vars") == ({"PYPI_TOKEN"}, [])
    assert _context_uses({"env": {"A": "${{ toJSON(vars) }}"}}, "vars")[1]


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
