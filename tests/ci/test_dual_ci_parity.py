"""Behaviour tests for ``scripts/ci/check-dual-ci-parity.py`` (BRIEF-06).

Six tests lock the dual-CI parity lint against regression:

- ``test_paired_fixture_passes`` -- green path: matching ADO + GHA pairs
  with identical stage graphs and parameter surfaces exit 0.
- ``test_unpaired_fixture_fails`` -- red path: ADO template with no GHA
  counterpart and no exception annotation exits non-zero, with the
  offending filename in the error stream.
- ``test_ci_mechanics_exception_skipped`` -- exception path: a single-sided
  file annotated ``sigantry-dual-ci-exception: ci-mechanics`` does not
  trigger a parity error.
- ``test_stage_graph_mismatch_fails`` -- synthetic ``tmp_path`` pair where
  ADO declares stage ``deploy`` but GHA declares ``ship``: lint exits 1
  with a stage-graph node-mismatch message.
- ``test_parameter_type_mismatch_fails`` -- synthetic ``tmp_path`` pair
  where ADO declares ``env_name: string`` but GHA declares ``env_name:
  boolean``: lint exits 1 with a type-mismatch message.
- ``test_real_repo_passes`` -- invoking the lint against the real repo
  root (no ``--root`` flag) exits 0. Locks in the "green from day 1"
  commitment from BRIEF-06 success criterion #2.

The synthetic fixtures use ``tmp_path`` so the real
``tests/ci/fixtures/`` trees stay single-purpose (paired or unpaired
only, never mismatched).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

LINT_SCRIPT = "scripts/ci/check-dual-ci-parity.py"


def _run_lint(
    repo_root: pathlib.Path,
    *,
    root: pathlib.Path | None = None,
    verbose: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Invoke the lint as a subprocess from ``repo_root``.

    Always uses ``sys.executable`` (the active interpreter) to honour the
    project's conda-first convention without hard-coding a path.
    """
    cmd: list[str] = [sys.executable, LINT_SCRIPT]
    if root is not None:
        cmd.extend(["--root", str(root)])
    if verbose:
        cmd.append("--verbose")
    return subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _write(path: pathlib.Path, contents: str) -> None:
    """Create parent dirs and write ``contents`` (UTF-8, dedented)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).lstrip("\n"), encoding="utf-8")


# ---------------------------------------------------------------------------
# Real-fixture tests (tests/ci/fixtures/paired + tests/ci/fixtures/unpaired)
# ---------------------------------------------------------------------------


def test_paired_fixture_passes(repo_root: pathlib.Path) -> None:
    """tests/ci/fixtures/paired -- matching pairs + one ci-mechanics
    exception. Lint exits 0.
    """
    fixture = repo_root / "tests" / "ci" / "fixtures" / "paired"
    result = _run_lint(repo_root, root=fixture, verbose=True)
    assert result.returncode == 0, (
        f"Expected exit 0 against paired fixture; got {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
    assert "pairs=2" in result.stdout
    # The exception fixture file is counted (under templates/schedules/).
    assert "exceptions=1" in result.stdout


def test_unpaired_fixture_fails(repo_root: pathlib.Path) -> None:
    """tests/ci/fixtures/unpaired -- single-sided ADO template with no
    exception. Lint exits non-zero and names the offending file.
    """
    fixture = repo_root / "tests" / "ci" / "fixtures" / "unpaired"
    result = _run_lint(repo_root, root=fixture)
    assert result.returncode != 0, (
        f"Expected non-zero against unpaired fixture; got 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "ado-only.yml" in combined, f"Expected 'ado-only.yml' in lint output; got:\n{combined}"
    assert "FAIL" in combined or "##[error]" in combined


def test_ci_mechanics_exception_skipped(repo_root: pathlib.Path) -> None:
    """The ``exception-ci-mechanics.yml`` fixture under paired/ is
    single-sided but annotated; the lint MUST skip it (no error).
    """
    fixture = repo_root / "tests" / "ci" / "fixtures" / "paired"
    result = _run_lint(repo_root, root=fixture)
    assert result.returncode == 0
    # The exception file must not appear as a violation.
    assert "exception-ci-mechanics.yml" not in result.stdout, (
        f"Exception-annotated file leaked into errors:\n{result.stdout}"
    )
    assert "exceptions=1" in result.stdout


# ---------------------------------------------------------------------------
# Synthetic-fixture tests (tmp_path, fully self-contained)
# ---------------------------------------------------------------------------


def test_stage_graph_mismatch_fails(tmp_path: pathlib.Path, repo_root: pathlib.Path) -> None:
    """ADO + GHA share a basename but declare different stage names.

    The lint MUST flag a stage-graph node mismatch and exit 1.
    """
    _write(
        tmp_path / "templates" / "stages" / "graph-mismatch.yml",
        """
        # Synthetic fixture: ADO declares stage 'deploy'.
        parameters: []
        stages:
          - stage: deploy
            displayName: Deploy
            dependsOn: []
            jobs:
              - job: run
                steps:
                  - script: echo deploy
        """,
    )
    _write(
        tmp_path / ".github" / "workflows" / "graph-mismatch.yml",
        """
        # Synthetic fixture: GHA declares job 'ship' (different name).
        name: graph-mismatch
        on:
          workflow_call: {}
        jobs:
          ship:
            name: Ship
            runs-on: ubuntu-latest
            steps:
              - run: echo ship
        """,
    )

    result = _run_lint(repo_root, root=tmp_path)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "stage-graph node mismatch" in combined, combined
    assert "deploy" in combined
    assert "ship" in combined


def test_parameter_type_mismatch_fails(tmp_path: pathlib.Path, repo_root: pathlib.Path) -> None:
    """ADO declares ``env_name: string`` but GHA declares
    ``env_name: boolean``. The lint MUST flag a type mismatch and
    exit 1.
    """
    _write(
        tmp_path / "templates" / "stages" / "param-mismatch.yml",
        """
        # Synthetic fixture: ADO env_name typed as string.
        parameters:
          - name: env_name
            type: string
            default: dev
        stages:
          - stage: smoke
            displayName: Smoke
            dependsOn: []
            jobs:
              - job: run
                steps:
                  - script: echo ${{ parameters.env_name }}
        """,
    )
    _write(
        tmp_path / ".github" / "workflows" / "param-mismatch.yml",
        """
        # Synthetic fixture: GHA env_name typed as boolean (mismatch).
        name: param-mismatch
        on:
          workflow_call:
            inputs:
              env_name:
                type: boolean
                default: false
                required: false
        jobs:
          smoke:
            name: Smoke
            runs-on: ubuntu-latest
            steps:
              - run: echo ${{ inputs.env_name }}
        """,
    )

    result = _run_lint(repo_root, root=tmp_path)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "type mismatch" in combined, combined
    assert "env_name" in combined
    assert "string" in combined
    assert "boolean" in combined


# ---------------------------------------------------------------------------
# Real-repo green-from-day-one gate
# ---------------------------------------------------------------------------


def test_real_repo_passes(repo_root: pathlib.Path) -> None:
    """The lint MUST exit 0 against the real repo root (no --root).

    Locks in BRIEF-06 success criterion #2: "the lint passes against
    the current templates/ + .github/workflows/ tree" -- the legacy
    pre-Sigantry CI/CD templates are annotated as ci-mechanics
    exceptions; future Sigantry-namespaced template pairs land in
    Phases 12-14.
    """
    result = _run_lint(repo_root)
    assert result.returncode == 0, (
        f"Real-repo lint regressed; expected exit 0 but got {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout


# Re-export for fixture resolution -- ``repo_root`` is provided by
# tests/ci/conftest.py.
__all__ = [
    "test_ci_mechanics_exception_skipped",
    "test_paired_fixture_passes",
    "test_parameter_type_mismatch_fails",
    "test_real_repo_passes",
    "test_stage_graph_mismatch_fails",
    "test_unpaired_fixture_fails",
]


# Quiet pytest's import-time noise about unused imports if any get added
# during future maintenance.
_ = pytest
