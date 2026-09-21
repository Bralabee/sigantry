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
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

# Source roots the project owns and therefore expects its linters to cover.
_LINTED_ROOTS = ("sigantry_core/", "tests/", "scripts/")


@pytest.fixture(scope="module")
def ci_workflow(repo_root: pathlib.Path) -> dict:
    return yaml.safe_load((repo_root / ".github" / "workflows" / "ci.yml").read_text())


def _run_commands(workflow: dict) -> list[str]:
    """Every ``run:`` string in the workflow, across all jobs and steps."""
    commands: list[str] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []) or []:
            run = step.get("run")
            if isinstance(run, str):
                commands.append(run)
    return commands


def test_mypy_is_configured(repo_root: pathlib.Path) -> None:
    """Precondition: the project really does configure mypy.

    Without this, the test below could pass vacuously on a repo that had
    simply dropped mypy altogether.
    """
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.mypy]" in pyproject


def test_mypy_is_actually_invoked_by_ci(ci_workflow: dict) -> None:
    """mypy runs in a workflow, not only in pyproject.toml."""
    commands = _run_commands(ci_workflow)
    assert any(c.strip().startswith("mypy ") or " mypy " in c for c in commands), (
        f"no CI step invokes mypy; run: commands were {commands}"
    )


def test_ruff_covers_every_source_root(ci_workflow: dict) -> None:
    """Both ruff invocations cover all the roots the project owns.

    ``scripts/`` was missing, so the CI guard scripts -- the files whose whole
    job is to police the repo -- were themselves unchecked.
    """
    ruff_cmds = [c for c in _run_commands(ci_workflow) if "ruff" in c]
    assert len(ruff_cmds) >= 2, f"expected a ruff check and a format check, got {ruff_cmds}"
    for cmd in ruff_cmds:
        missing = [root for root in _LINTED_ROOTS if root not in cmd]
        assert not missing, f"ruff invocation {cmd!r} does not cover {missing}"


def _as_list(needs: object) -> list[str]:
    """``needs:`` is a list OR a bare scalar; ``set("lint")`` is a set of letters."""
    if needs is None:
        return []
    if isinstance(needs, str):
        return [needs]
    return list(needs)


# Quality jobs deliberately NOT gating the artifact build, each with its reason.
#
# A job skipped because a dependency failed still reports a check run, and
# GitHub counts a skipped run as SATISFYING its required context. So making
# `build` depend on a job that is not itself a required context converts
# `build` from a gate into a way to hand branch protection a green
# "Build & Verify Artifacts" on a tree that failed that job -- strictly worse
# than not gating on it at all. Move a job out of this map the moment its own
# context is required on `main`.
_BUILD_DEPS_EXEMPT = {
    "types": "Type Check (mypy) is not yet a required status check on main",
}


def test_artifact_build_depends_on_every_quality_job(ci_workflow: dict) -> None:
    """Artifacts are not built from a tree that skipped a BLOCKING quality gate."""
    jobs = ci_workflow["jobs"]
    needs = set(_as_list(jobs["build"].get("needs")))
    # A job that depends on build cannot also gate build.
    downstream = {n for n, j in jobs.items() if "build" in _as_list(j.get("needs"))}
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
