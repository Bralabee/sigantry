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
import pathlib
import re
import shlex
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

# A publish that goes through a repo script is still a publish. release-alpha
# calls `bash scripts/release/publish-v3-alpha.sh`, which runs twine inside.
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
            for line in _command_lines(run):
                compound = bool(_OPERATOR_RE.search(line))
                for segment in _OPERATOR_RE.split(line):
                    argv = _normalise_tool_argv(_argv(segment), tool)
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
                            shell=str(
                                step.get(
                                    "shell", job.get("defaults", {}).get("run", {}).get("shell", "")
                                )
                            ).strip(),
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
# NOTE: a lapsed date here reds the `test` job, which gates `build`, which the
# release path now depends on -- so an expired carve-out blocks a release on a
# calendar date with no code change. That is intended (an expired excuse should
# stop the thing it was excusing) and is a one-line fix, but it is a real
# consequence and is recorded rather than discovered during a release.
# Keyed "<workflow>::<job>", not by filename: a filename key would go on
# excusing any publish job added to that file later, including a production
# one, on a reason recorded about a different job.
_PUBLISH_GATE_EXEMPT: dict[str, tuple[str, str]] = {
    "release-alpha.yml::publish-testpypi": (
        "Tracked in #13: this workflow cannot currently succeed at all "
        "(PACKAGE_DIRS names directories absent from the repo under "
        "`set -euo pipefail`, and the twine operands match nothing), and "
        "whether it still has a purpose is an open decision. Gating a "
        "workflow that cannot run would assert nothing.",
        "2026-12-31",
    ),
}


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
    # A dependency that cannot fail is not a gate either.
    for job_id in needs:
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
    """Jobs that push a distribution to an index, by any mechanism.

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
        # A publish routed through a repo script is still a publish:
        # release-alpha calls `bash scripts/release/publish-v3-alpha.sh`, which
        # runs twine inside. Without this, deleting that job's inline upload
        # step would make it vanish from the scan -- the one direction this
        # file must never fail in.
        if any(
            _PUBLISH_SCRIPT_RE.search(line)
            for s in steps
            if isinstance(s.get("run"), str)
            for line in _command_lines(s["run"])
        ):
            out.append(name)
    return out


def test_every_publish_path_is_gated_by_the_quality_jobs(
    all_workflows: dict[str, dict], ci_workflow: dict
) -> None:
    """The distribution users install is gated, not just the CI artifact.

    ``publish-pypi.yml`` ran checkout -> build -> twine -> publish with no
    dependency on lint, test or types. CI's own ``build`` job gates only the
    ``dist`` artifact uploaded for that run, which nobody installs.

    Every workflow is scanned, not just ``publish-pypi.yml``: ``release-alpha``
    publishes via ``twine upload`` in an ungated job, and a check that looked
    only at one filename and one action could not see it.
    """
    found_any = False
    for filename, workflow in all_workflows.items():
        jobs = workflow.get("jobs") or {}
        for job_id in _publish_jobs(workflow):
            found_any = True
            if f"{filename}::{job_id}" in _PUBLISH_GATE_EXEMPT:
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
    assert found_any, (
        "no publish path found in any workflow; this scan would be vacuous. "
        "Either the detection is broken or publishing moved somewhere unseen."
    )

    # And the gate must be callable, or the `uses:` reference is broken.
    assert "workflow_call" in _triggers(ci_workflow), (
        "ci.yml is referenced as a reusable workflow but has no "
        "`workflow_call` trigger, so the publish gate cannot run."
    )


def test_publish_gate_exemptions_name_real_publish_jobs(
    all_workflows: dict[str, dict],
) -> None:
    """A carve-out must still name a job that really publishes."""
    for key in _PUBLISH_GATE_EXEMPT:
        filename, sep, job_id = key.partition("::")
        assert sep and job_id, (
            f"_PUBLISH_GATE_EXEMPT key {key!r} must be '<workflow>.yml::<job-id>'; a "
            "filename alone excuses every publish job in that file, now and later."
        )
        assert filename in all_workflows, (
            f"_PUBLISH_GATE_EXEMPT names a workflow that does not exist: {filename}"
        )
        assert job_id in _publish_jobs(all_workflows[filename]), (
            f"_PUBLISH_GATE_EXEMPT excuses {key!r}, which no longer publishes "
            "anything -- the carve-out is dead weight."
        )


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
    if not isinstance(concurrency, dict) or not _is_truthy(concurrency.get("cancel-in-progress")):
        return
    group = str(concurrency.get("group", ""))
    assert "github.workflow" in group, (
        f"ci.yml cancels in-progress runs grouped by {group!r}, which does not "
        "distinguish a direct run from one called by another workflow. Include "
        "`github.workflow` (in a called workflow it is the CALLER's name) so a "
        "release's quality gate cannot be cancelled by a push to the same ref."
    )
