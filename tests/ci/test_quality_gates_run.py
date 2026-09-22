"""A quality tool the project configures must actually be RUN by CI.

STRUCT-02. ``mypy`` was configured under ``[tool.mypy]`` in pyproject.toml
and invoked by no workflow at all, so it reported nothing for as long as that
was true -- including a real ``attr-defined`` bug in
``scripts/ci/check-no-sys-path.py`` that crashed the guard on any malformed
``.py`` file. Ruff, meanwhile, ran over ``sigantry_core/`` and ``tests/`` but
not ``scripts/``, leaving the CI guards themselves unlinted.

A configured-but-unrun tool is worse than an absent one: the config file
advertises a gate that does not exist, and everyone downstream believes the
tree is covered. These tests assert the tools are wired to something that
executes, so the gap cannot silently reopen.

The first version of these assertions was itself vacuous, which is why they
are written the way they are now. Measured clean -> arms -> clean against a
parsed copy of the real ``ci.yml``, the old substring checks PASSED when:

* the ``types`` job body was replaced with ``pip install mypy ruff`` -- the
  substring ``" mypy "`` is present and nothing executes it;
* the mypy step became ``# mypy sigantry_core/ disabled`` plus an ``echo`` --
  a comment is not a command;
* ``continue-on-error: true`` was added to the mypy step -- the tool runs and
  the job cannot fail;
* both ruff steps were rewritten to ``--exclude scripts/`` -- the root is
  named in the command precisely because it is being excluded from it.

Only deleting the job outright failed them. So these tests now tokenise each
``run:`` line and ask what a shell would EXECUTE, not what the YAML contains.

Known limit, deliberately left failing loudly: only inline ``run:`` strings in
``ci.yml`` are inspected. Moving mypy or ruff into a composite action or a
reusable workflow would read here as "not invoked" and fail. That is the safe
direction -- a false alarm demanding this file be updated, never a silent pass.
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
# resolution before type checking begins (duplicate basenames, no
# `__init__.py`), so demanding coverage here would assert a thing that cannot
# currently be true. Widening it is its own piece of work.
_TYPED_ROOTS = ("sigantry_core/", "scripts/")

# The job `name:` that branch protection knows as a required status check.
# Tying the assertion to this string means renaming the job -- which would
# silently stop the required context ever reporting -- fails here too.
_TYPE_CHECK_JOB_NAME = "Type Check (mypy)"

# Shell operators that separate one executed command from the next.
_OPERATOR_RE = re.compile(r"\|\||&&|;|\|")

# Suffixes that let a command fail without failing the step.
_NEUTERING = ("|| true", "|| :", "|| exit 0")

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _norm_root(root: str) -> str:
    """Compare roots without tripping over a trailing slash."""
    return root.rstrip("/")


@pytest.fixture(scope="module")
def ci_workflow(repo_root: pathlib.Path) -> dict:
    # Explicit encoding: the test matrix includes windows-latest, where the
    # default encoding is not UTF-8, so one non-ASCII character in a comment
    # would otherwise red three legs for a reason unrelated to the code.
    path = repo_root / ".github" / "workflows" / "ci.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def publish_workflow(repo_root: pathlib.Path) -> dict:
    path = repo_root / ".github" / "workflows" / "publish-pypi.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """The ``on:`` block.

    YAML 1.1 resolves a bare ``on`` key to the boolean ``True``, so reading
    ``workflow["on"]`` finds nothing on a real GitHub workflow.
    """
    if True in workflow:
        return workflow[True] or {}
    return workflow.get("on") or {}


def _is_truthy(value: object) -> bool:
    """``continue-on-error`` may be a bool or an expression string."""
    if value is None or value is False:
        return False
    return str(value).strip().lower() not in ("", "false")


def _command_lines(run: str) -> list[str]:
    """The executable lines of a ``run:`` block.

    Blank and comment lines are dropped: a step whose mypy call has been
    commented out does not run mypy, and a substring check cannot tell.
    """
    lines = []
    for raw in run.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
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


def _normalise_tool_argv(argv: list[str], tool: str) -> list[str] | None:
    """Return argv with ``tool`` at position 0, or None if it is not executed.

    Tokenised rather than substring-matched: ``pip install mypy ruff`` MENTIONS
    both tools and executes neither, and that is exactly what the assertion
    this replaces accepted as proof the tool was wired up.
    """
    if not argv:
        return None
    if argv[0] == tool:
        return argv
    if argv[0] in ("python", "python3") and argv[1:3] == ["-m", tool]:
        return argv[2:]
    return None


@dataclass(frozen=True)
class _Invocation:
    job: str
    job_name: str
    step: str
    line: str
    argv: list[str]
    neutered: bool
    continue_on_error: bool

    def describe(self) -> str:
        return f"job {self.job!r} step {self.step!r}: {self.line!r}"


def _invocations(workflow: dict, tool: str) -> list[_Invocation]:
    """Every place the workflow actually EXECUTES ``tool``."""
    found: list[_Invocation] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        job_coe = _is_truthy(job.get("continue-on-error"))
        for step in job.get("steps") or []:
            run = step.get("run")
            if not isinstance(run, str):
                continue
            step_coe = _is_truthy(step.get("continue-on-error"))
            for line in _command_lines(run):
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
                            neutered=any(n in line for n in _NEUTERING),
                            continue_on_error=job_coe or step_coe,
                        )
                    )
    return found


def _assert_enforcing(call: _Invocation) -> None:
    """A tool that runs but cannot fail the build is not a gate."""
    assert not call.neutered, (
        f"{call.describe()} swallows its own failure; a command that cannot fail certifies nothing."
    )
    assert not call.continue_on_error, (
        f"{call.describe()} runs under continue-on-error, so the job reports "
        "success whatever the tool finds."
    )


def _excluded_roots(argv: list[str]) -> set[str]:
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
        "count -- that mention is what made the previous version of this "
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
    """mypy checks every root it is supposed to, and excludes none of them."""
    calls = _invocations(ci_workflow, "mypy")
    assert calls, "no mypy invocation to inspect"
    covered: set[str] = set()
    for call in calls:
        operands = {_norm_root(t) for t in call.argv[1:] if not t.startswith("-")}
        covered |= operands - _excluded_roots(call.argv)
    missing = sorted(_norm_root(r) for r in _TYPED_ROOTS if _norm_root(r) not in covered)
    assert not missing, (
        f"mypy does not type check {missing}. Commands were: {[c.line for c in calls]}"
    )


def test_ruff_covers_every_source_root(ci_workflow: dict) -> None:
    """Both ruff invocations cover all the roots the project owns.

    ``scripts/`` was missing, so the CI guard scripts -- the files whose whole
    job is to police the repo -- were themselves unchecked.
    """
    calls = _invocations(ci_workflow, "ruff")
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
    for call in _invocations(ci_workflow, "ruff"):
        if len(call.argv) > 1 and call.argv[1] == "format":
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


# Quality jobs deliberately NOT gating the artifact build: job -> (reason,
# review-by date). EMPTY, and it should stay that way.
#
# A job skipped because a dependency failed still reports a check run, and
# GitHub counts a skipped run as SATISFYING its required context. So making
# `build` depend on a job that is not itself a required context converts
# `build` from a gate into a way to hand branch protection a green
# "Build & Verify Artifacts" on a tree that failed that job -- strictly worse
# than not gating on it at all.
#
# `types` lived here until `Type Check (mypy)` became a required context on
# main (2026-09-21), and then went on living here, because nothing made the
# carve-out expire. Every entry now carries a review-by date and fails this
# suite once that date passes, so a stale excuse cannot outlive its reason in
# silence.
_BUILD_DEPS_EXEMPT: dict[str, tuple[str, str]] = {}


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
        "Either add them to build's `needs`, or record why not in _BUILD_DEPS_EXEMPT."
    )


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


def test_build_deps_exemptions_expire() -> None:
    """A carve-out outlives its reason unless something makes it expire.

    The `types` exemption stayed in place after the condition it named
    ("not yet a required status check") stopped being true, because nothing
    was watching. A review-by date fails loudly instead of relying on someone
    remembering.
    """
    today = dt.date.today()
    for job, entry in _BUILD_DEPS_EXEMPT.items():
        assert isinstance(entry, tuple) and len(entry) == 2, (
            f"_BUILD_DEPS_EXEMPT[{job!r}] must be (reason, 'YYYY-MM-DD'), got {entry!r}"
        )
        reason, review_by = entry
        assert reason.strip(), f"_BUILD_DEPS_EXEMPT[{job!r}] has no reason"
        deadline = dt.date.fromisoformat(review_by)
        assert deadline >= today, (
            f"_BUILD_DEPS_EXEMPT[{job!r}] was due for review on {review_by} "
            f"({(today - deadline).days} day(s) ago). Reason given: {reason!r}. "
            "Either add the job to build's `needs` now that its context is "
            "required, or restate the reason with a new date."
        )


def test_published_wheel_is_gated_by_the_same_quality_jobs(
    publish_workflow: dict, ci_workflow: dict
) -> None:
    """The wheel users install is gated, not just the throwaway CI artifact.

    ``publish-pypi.yml`` ran checkout -> build -> twine -> publish with no
    dependency on lint, test or types. CI's own ``build`` job gates only the
    ``dist`` artifact uploaded for that run, which nobody installs.
    """
    jobs = publish_workflow["jobs"]
    publishers = [
        name
        for name, job in jobs.items()
        if any(
            "pypa/gh-action-pypi-publish" in str(step.get("uses", ""))
            for step in job.get("steps") or []
        )
    ]
    assert publishers, "no job in publish-pypi.yml publishes to PyPI"

    for name in publishers:
        needs = set(_as_list(jobs[name].get("needs")))
        gates = [n for n in needs if str(jobs.get(n, {}).get("uses", "")).endswith("ci.yml")]
        assert gates, (
            f"publish job {name!r} does not depend on a job that runs ci.yml; "
            f"its needs are {sorted(needs)}. The published wheel would ship "
            "without lint, type or test having run."
        )

    # And the gate must be callable, or the `uses:` reference is broken.
    assert "workflow_call" in _triggers(ci_workflow), (
        "ci.yml is referenced as a reusable workflow but has no "
        "`workflow_call` trigger, so the publish gate cannot run."
    )
