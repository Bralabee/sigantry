"""``scripts/ci/check-seeded-defects.py``: its wiring into ci.yml, and its verdicts (#47).

The script's end-to-end proof is its own CI job, which runs the real gates on
a seeded copy of the tree, and the break arms recorded on the pull request
that added it. These tests pin what that job cannot see about itself: that
it runs on every pull request as a gate of the artifact build, that it reads
the same steps the quality jobs run, that its seeds cover every root those
jobs cover, and that its verdict fails a gate that passes or fails without
naming its seed.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
from types import ModuleType

import pytest
import yaml

from .test_quality_gates_run import (
    _LINTED_ROOTS,
    _TYPED_ROOTS,
    _as_list,
    _assert_job_enforcing,
    _norm_root,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "check-seeded-defects.py"
_RUN = "python scripts/ci/check-seeded-defects.py --root ."


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_seeded_defects", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the dataclass decorator looks its module up.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


seeded = _load()


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    return yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )


def test_the_seeded_job_gates_the_build(ci_workflow: dict) -> None:
    """The job runs the script, cannot be neutered, and the artifact build waits on it."""
    jobs = ci_workflow["jobs"]
    job = jobs.get("seeded")
    assert job, "ci.yml has no `seeded` job"
    assert job["name"] == "Seeded defects", "the required context is matched by job name"
    runs = [s.get("run") for s in job["steps"] if "run" in s]
    assert runs[-1] == _RUN, f"the job's last step must be {_RUN!r}: {runs}"
    for step in job["steps"]:
        assert "continue-on-error" not in step and "if" not in step, step
    _assert_job_enforcing(jobs, "seeded", "the seeded-defect check could not fail the build")
    assert "seeded" in _as_list(jobs["build"].get("needs"))


@pytest.mark.parametrize("gate", seeded.GATES, ids=lambda g: g.label)
def test_each_gate_reads_a_step_the_quality_jobs_run(ci_workflow: dict, gate: object) -> None:
    """The script finds each gate's step in ci.yml, and it runs the gate's tool."""
    run, _env, cwd = seeded.command_for(ci_workflow, gate)
    tool = gate.label.split()[0]  # type: ignore[attr-defined]
    assert tool in run, f"{gate.label}: step runs {run!r}"  # type: ignore[attr-defined]
    assert cwd == "."


def test_the_seeds_cover_every_root_the_gates_cover() -> None:
    """A root a gate covers but the seeds miss could be switched off unseen."""
    by_label = {g.label: g for g in seeded.GATES}
    assert set(by_label) == {"ruff check", "ruff format", "mypy", "pytest"}

    def roots(label: str) -> set[str]:
        return {name.split("/", 1)[0] for name in by_label[label].seeds}

    linted = {_norm_root(r) for r in _LINTED_ROOTS}
    assert roots("ruff check") == linted
    assert roots("ruff format") == linted
    assert roots("mypy") == {_norm_root(r) for r in _TYPED_ROOTS}
    # A seed that collides with a real file is refused by `plant` (exit 2), not
    # checked here: inside the seeded copy the seed exists by design.


@pytest.mark.parametrize(
    ("returncode", "output", "ok"),
    [
        (1, "a/x.py:1 error\nb/x.py:2 error", True),
        (0, "a/x.py:1 error\nb/x.py:2 error", False),
        (1, "a/x.py:1 error", False),
        (2, "Traceback: the tool crashed", False),
    ],
    ids=["failed-on-both", "exited-0", "one-seed-unreported", "crashed"],
)
def test_judge(returncode: int, output: str, ok: bool) -> None:
    gate = seeded.Gate("t", "j", "s", {}, ("a/x.py", "b/x.py"))
    assert (seeded.judge(gate, returncode, output) == []) is ok


def test_command_for_refuses_what_it_cannot_run() -> None:
    gate = seeded.Gate("mypy", "types", "Mypy", {}, ())

    def workflow(**step: object) -> dict:
        return {"jobs": {"types": {"steps": [{"name": "Mypy", **step}]}}}

    assert seeded.command_for(workflow(run="mypy x/"), gate)[0] == "mypy x/"
    for bad in (
        {"jobs": {}},
        {
            "jobs": {
                "types": {"steps": [{"name": "Mypy", "run": "a"}, {"name": "Mypy", "run": "b"}]}
            }
        },
        workflow(),
        workflow(run="mypy ${{ inputs.roots }}"),
        workflow(run="mypy x/", env={"A": "${{ secrets.X }}"}),
    ):
        with pytest.raises(seeded.SetupError):
            seeded.command_for(bad, gate)
    _run, env, _ = seeded.command_for(
        {
            "env": {"A": "1"},
            "jobs": {
                "types": {
                    "env": {"B": True},
                    "steps": [{"name": "Mypy", "run": "m", "env": {"A": "2"}}],
                }
            },
        },
        gate,
    )
    assert env == {"A": "2", "B": "true"}


def test_the_copy_holds_the_tracked_files_and_refuses_to_overwrite(tmp_path: pathlib.Path) -> None:
    """Only what git tracks is copied, as the working tree has it; a seed never replaces a file."""
    git = shutil.which("git")
    assert git, "git is not on PATH"
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for argv in (["init", "-q"], ["add", "pkg/tracked.py"]):
        subprocess.run([git, *argv], cwd=src, env=env, check=True)
    (src / "pkg" / "tracked.py").write_text("x = 2\n", encoding="utf-8")
    (src / "pkg" / "untracked.py").write_text("y = 1\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    seeded.copy_tree(src, dest)
    assert (dest / "pkg" / "tracked.py").read_text(encoding="utf-8") == "x = 2\n"
    assert not (dest / "pkg" / "untracked.py").exists()
    with pytest.raises(seeded.SetupError, match="already exists"):
        seeded.plant(dest, {"pkg/tracked.py": "z = 3\n"})
    seeded.plant(dest, {"pkg/new.py": "z = 3\n"})
    assert (dest / "pkg" / "new.py").read_text(encoding="utf-8") == "z = 3\n"
