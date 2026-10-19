"""``scripts/ci/check-seeded-defects.py``: its wiring into ci.yml, and its verdicts (#47).

The script's end-to-end proof is its own CI job, which runs the real gates on
a seeded copy of the tree, and the break arms recorded on the pull request
that added it. These tests pin what that job cannot see about itself: that
it runs on every pull request as a gate of the artifact build, that it reads
the same steps the quality jobs run, that its seeds cover every directory
those jobs cover, and that its verdict fails a gate that passes, or that fails
without reporting each seed as a failure.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
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


def test_the_seeds_cover_every_directory_the_gates_cover() -> None:
    """Every directory a gate covers gets a seed, so a sub-package switched off is seen.

    The roots must match the ones the quality-gate tests pin, and on the real
    tree every tracked source directory under them (every test directory, for
    pytest) holds a seed.
    """
    by_label = {g.label: g for g in seeded.GATES}
    assert set(by_label) == {"ruff check", "ruff format", "mypy", "pytest"}
    linted = {_norm_root(r) for r in _LINTED_ROOTS}
    assert set(by_label["ruff check"].roots) == linted
    assert set(by_label["ruff format"].roots) == linted
    assert set(by_label["mypy"].roots) == {_norm_root(r) for r in _TYPED_ROOTS}
    tracked = seeded.tracked_files(REPO_ROOT)
    for gate in seeded.GATES:
        seeds = seeded.seeds_for(gate, tracked)
        seeded_dirs = {str(pathlib.PurePosixPath(n).parent) for n in seeds}
        covered = {
            str(pathlib.PurePosixPath(n).parent)
            for n in tracked
            if n.split("/", 1)[0] in gate.roots and gate.covers(n)
        }
        assert covered, f"{gate.label}: no covered directory found; the scan is vacuous"
        assert covered <= seeded_dirs, f"{gate.label}: unseeded {sorted(covered - seeded_dirs)}"
        assert len({pathlib.PurePosixPath(n).name for n in seeds}) == len(seeds), (
            f"{gate.label}: two seeds share a basename"
        )
    # A seed that collides with a real file is refused by `plant` (exit 2), not
    # checked here: inside the seeded copy the seed exists by design.


def test_seeds_for_names_each_seed_by_its_directory() -> None:
    gate = next(g for g in seeded.GATES if g.label == "mypy")
    seeds = seeded.seeds_for(
        gate,
        ["sigantry_core/a.py", "sigantry_core/sub-dir/b.py", "scripts/c.txt", "tests/test_x.py"],
    )
    assert sorted(seeds) == [
        "scripts/_seeded_defect_types_scripts.py",
        "sigantry_core/_seeded_defect_types_sigantry_core.py",
        "sigantry_core/sub-dir/_seeded_defect_types_sigantry_core_sub_dir.py",
    ]


_A = "a/_seeded_defect_types_a.py"
_B = "b/_seeded_defect_types_b.py"
_MYPY_BOTH = (
    f'{_A}:2: error: Incompatible return value type (got "str", expected "int")\n'
    f'{_B}:2: error: Incompatible return value type (got "str", expected "int")\n'
)


@pytest.mark.parametrize(
    ("returncode", "output", "ok"),
    [
        (1, _MYPY_BOTH, True),
        (0, _MYPY_BOTH, False),
        (1, _MYPY_BOTH.splitlines()[0], False),
        (2, f"{_A}: error: Duplicate module named\n{_B}: error: Duplicate module named", False),
        (2, "Traceback: the tool crashed", False),
    ],
    ids=["failed-on-both", "exited-0", "one-seed-unreported", "named-but-not-reported", "crashed"],
)
def test_judge(returncode: int, output: str, ok: bool) -> None:
    gate = next(g for g in seeded.GATES if g.label == "mypy")
    seeds = {_A: "", _B: ""}
    assert (seeded.judge(gate, seeds, returncode, output) == []) is ok


@pytest.mark.parametrize(
    ("label", "line", "ok"),
    [
        (
            "pytest",
            "FAILED tests/x/test_seeded_defect_tests_x.py::test_seeded_defect - assert",
            True,
        ),
        ("pytest", "SKIPPED tests/x/test_seeded_defect_tests_x.py::test_seeded_defect", False),
        ("pytest", "XFAIL tests/x/test_seeded_defect_tests_x.py::test_seeded_defect", False),
        ("ruff format", "Would reformat: tests/x/_seeded_defect_format_tests_x.py", True),
        ("ruff format", "error: Failed to parse tests/x/_seeded_defect_format_tests_x.py", False),
        (
            "ruff check",
            "F401 [*] `os` imported but unused\n --> tests/x/_seeded_defect_lint_tests_x.py:1:8",
            True,
        ),
        ("ruff check", "tests/x/_seeded_defect_lint_tests_x.py:1:8: F401 [*] `os` imported", True),
        ("ruff check", "E902 No such file\n --> tests/x/_seeded_defect_lint_tests_x.py:1:1", False),
    ],
)
def test_each_gate_accepts_only_its_own_failure_report(label: str, line: str, ok: bool) -> None:
    """The seed's path in the output is not enough; the tool must report it as a failure."""
    gate = next(g for g in seeded.GATES if g.label == label)
    seeds = seeded.seeds_for(gate, ["tests/x/test_a.py"])
    seed = next(n for n in seeds if n.startswith("tests/x/"))
    assert (seeded.judge(gate, {seed: ""}, 1, line) == []) is ok


def test_command_for_refuses_what_it_cannot_run() -> None:
    gate = next(g for g in seeded.GATES if g.label == "mypy")

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
        workflow(run="mypy x/", shell="bash"),
        {"defaults": {"run": {"working-directory": "x"}}, **workflow(run="mypy x/")},
        {
            "jobs": {
                "types": {
                    "defaults": {"run": {"shell": "sh"}},
                    "steps": [{"name": "Mypy", "run": "m"}],
                }
            }
        },
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
    for argv in (["init", "-q"], ["add", "pkg/tracked.py"]):
        seeded._git(argv, src)
    (src / "pkg" / "tracked.py").write_text("x = 2\n", encoding="utf-8")
    (src / "pkg" / "untracked.py").write_text("y = 1\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    seeded.copy_tree(src, dest, seeded.tracked_files(src))
    assert (dest / "pkg" / "tracked.py").read_text(encoding="utf-8") == "x = 2\n"
    assert not (dest / "pkg" / "untracked.py").exists()
    with pytest.raises(seeded.SetupError, match="already exists"):
        seeded.plant(dest, {"pkg/tracked.py": "z = 3\n"})
    seeded.plant(dest, {"pkg/new.py": "z = 3\n"})
    assert (dest / "pkg" / "new.py").read_text(encoding="utf-8") == "z = 3\n"


@pytest.fixture(scope="module")
def ado_template() -> dict:
    return yaml.safe_load((REPO_ROOT / seeded.ADO_TEMPLATE).read_text(encoding="utf-8"))


@pytest.mark.parametrize("gate", seeded.ADO_GATES, ids=lambda g: g.label)
def test_each_ado_gate_reads_a_step_of_the_lint_job_template(
    ado_template: dict, gate: object
) -> None:
    run, env, cwd = seeded.ado_command_for(ado_template, gate)
    tool = gate.label.split()[1]  # type: ignore[attr-defined]
    assert run.split()[0] == tool, f"{gate.label}: step runs {run!r}"  # type: ignore[attr-defined]
    assert (env, cwd) == ({}, ".")


def test_the_ado_gates_plant_what_the_github_gates_plant() -> None:
    """Same seeds and reports over the same roots: a narrowed ADO default root fails the check."""
    by_label = {g.label: g for g in seeded.GATES}
    assert {g.label for g in seeded.ADO_GATES} == {"ADO ruff check", "ADO ruff format", "ADO mypy"}
    tracked = seeded.tracked_files(REPO_ROOT)
    for gate in seeded.ADO_GATES:
        twin = by_label[gate.label.removeprefix("ADO ")]
        assert gate.lane == "ado" and twin.lane == "github"
        assert (gate.roots, gate.report, gate.content) == (twin.roots, twin.report, twin.content)
        assert seeded.seeds_for(gate, tracked) == seeded.seeds_for(twin, tracked)


def _ado(steps: list, **job: object) -> dict:
    return {
        "parameters": [
            {"name": "sourcePaths", "type": "string", "default": "a/ b/"},
            {"name": "enableTypeCheck", "type": "boolean", "default": True},
        ],
        "jobs": [{"job": "python_lint", "steps": steps, **job}],
    }


def test_ado_command_for_substitutes_defaults_and_evaluates_the_type_check_insertion() -> None:
    gate = next(g for g in seeded.ADO_GATES if g.label == "ADO mypy")
    mypy = {"script": "mypy ${{ parameters.sourcePaths }}", "displayName": "mypy"}
    doc = _ado([{"${{ if eq(parameters.enableTypeCheck, true) }}": [mypy]}])
    assert seeded.ado_command_for(doc, gate)[0] == "mypy a/ b/"
    doc["parameters"][1]["default"] = False
    with pytest.raises(seeded.SetupError, match="0 steps named"):
        seeded.ado_command_for(doc, gate)


def test_ado_command_for_refuses_what_it_cannot_run() -> None:
    gate = next(g for g in seeded.ADO_GATES if g.label == "ADO ruff check")

    def step(**extra: object) -> dict:
        return {
            "script": "ruff check ${{ parameters.sourcePaths }}",
            "displayName": "Ruff check",
            **extra,
        }

    assert seeded.ado_command_for(_ado([step()]), gate)[0] == "ruff check a/ b/"
    for bad in (
        _ado([]),
        _ado([step(), step()]),
        _ado([step(condition="false")]),
        _ado([step(continueOnError=True)]),
        _ado([step(enabled=False)]),
        _ado([step(env={"A": "1"})]),
        _ado([step(workingDirectory="x")]),
        _ado([step(script="ruff check ${{ parameters.missing }}")]),
        _ado([step(script="ruff check $(lintRoots)")]),
        _ado([step(script="ruff check ${{ replace(parameters.sourcePaths, 'a/', '') }}")]),
        _ado([{"${{ if eq(parameters.other, true) }}": [step()]}]),
        _ado([step()], condition="false"),
        _ado([step()], continueOnError=True),
        {**_ado([step()]), "variables": {"A": "1"}},
        {"jobs": [{"job": "python_lint", "steps": [step()]}]},
        {"parameters": [], "jobs": [{"job": "other", "steps": [step()]}]},
    ):
        with pytest.raises(seeded.SetupError):
            seeded.ado_command_for(bad, gate)
    flag = _ado([{"${{ if eq(parameters.enableTypeCheck, true) }}": [step()]}])
    flag["parameters"][1]["default"] = "true"
    with pytest.raises(seeded.SetupError, match="not a boolean"):
        seeded.ado_command_for(flag, gate)


@pytest.mark.skipif(
    sys.platform == "win32", reason="models a Linux agent's bash; Windows' bash.exe may be WSL"
)
def test_each_lane_runs_a_step_with_its_own_shell_semantics(tmp_path: pathlib.Path) -> None:
    """ADO's CmdLine has no errexit, so only the last command counts; GitHub's `bash -e` stops."""
    ado = seeded.ADO_GATES[0]
    github = seeded.GATES[0]

    def rc(gate: object, body: str) -> int:
        done = seeded.run_command(gate, body, tmp_path, {}, tmp_path)
        return done.returncode

    assert rc(ado, "false\ntrue\n") == 0
    assert rc(ado, "true\nfalse\n") != 0
    assert rc(github, "false\ntrue\n") != 0
