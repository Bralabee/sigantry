"""The ADO lint lane is pinned whole where the seeded-defect check does not run it (#22).

``scripts/ci/check-seeded-defects.py`` runs the ``Ruff check``, ``Ruff format
check`` and ``mypy`` steps of ``templates/jobs/lint-python.yml`` on a seeded
tree, with the template's defaults, as the CmdLine task runs them. What it
does not run is pinned here, so that it changes only through a reviewed edit
of this file:

- the job template whole: every key the runner does not model (a step
  ``condition``, ``continueOnError``, ``enabled``, ``env``, a job ``pool``),
  and the install step that provides the tools;
- the ``lint`` stage of ``templates/stages/ci.yml``, which hands its own
  defaults to the job, so those defaults must equal the job's;
- that stage file's ``pytest`` job, whose step the check does not run;
- the ``dev`` extra the install step installs, which must carry the tools
  the steps call.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_LINT_JOB = REPO_ROOT / "templates" / "jobs" / "lint-python.yml"
_CI_STAGES = REPO_ROOT / "templates" / "stages" / "ci.yml"

_EXPECTED_LINT_JOB_TEMPLATE: dict[str, Any] = {
    "parameters": [
        {"name": "pythonVersion", "type": "string", "default": "3.11"},
        {"name": "sourcePaths", "type": "string", "default": "sigantry_core/ tests/ scripts/"},
        {"name": "enableTypeCheck", "type": "boolean", "default": True},
        {"name": "typeCheckPaths", "type": "string", "default": "sigantry_core/ scripts/"},
    ],
    "jobs": [
        {
            "job": "python_lint",
            "displayName": "Python lint (ruff)",
            "steps": [
                {
                    "task": "UsePythonVersion@0",
                    "displayName": "Use Python ${{ parameters.pythonVersion }}",
                    "inputs": {
                        "versionSpec": "${{ parameters.pythonVersion }}",
                        "addToPath": True,
                    },
                },
                {"script": "python -m pip install --upgrade pip", "displayName": "Upgrade pip"},
                {"script": 'pip install -e ".[dev]"', "displayName": "Install Python deps"},
                {
                    "script": "ruff check ${{ parameters.sourcePaths }}",
                    "displayName": "Ruff check",
                },
                {
                    "script": "ruff format --check ${{ parameters.sourcePaths }}",
                    "displayName": "Ruff format check",
                },
                {
                    "${{ if eq(parameters.enableTypeCheck, true) }}": [
                        {
                            "script": "mypy ${{ parameters.typeCheckPaths }}",
                            "displayName": "mypy",
                        }
                    ]
                },
            ],
        }
    ],
}

_EXPECTED_LINT_STAGE: dict[str, Any] = {
    "stage": "lint",
    "displayName": "Lint",
    "jobs": [
        {
            "template": "../jobs/lint-python.yml",
            "parameters": {
                "pythonVersion": "${{ parameters.pythonVersion }}",
                "sourcePaths": "${{ parameters.lintSourcePaths }}",
                "enableTypeCheck": "${{ parameters.enableTypeCheck }}",
                "typeCheckPaths": "${{ parameters.typeCheckPaths }}",
            },
        },
        {
            "template": "../jobs/lint-powershell.yml",
            "parameters": {"psscriptanalyzerVersion": "${{ parameters.psscriptanalyzerVersion }}"},
        },
    ],
}

_EXPECTED_PYTEST_JOB: dict[str, Any] = {
    "job": "pytest",
    "displayName": "pytest",
    "strategy": {"matrix": "${{ parameters.pythonVersionMatrix }}", "maxParallel": 2},
    "steps": [
        {
            "task": "UsePythonVersion@0",
            "inputs": {"versionSpec": "$(pythonVersion)", "addToPath": True},
        },
        {
            "script": 'python -m pip install --upgrade pip\npip install -e ".[dev]"\n',
            "displayName": "Install Python deps",
        },
        {
            # pytest is the body's last command, so its status is the step's.
            "script": "mkdir -p test-results\n"
            "pytest tests/ \\\n"
            "  --junitxml=test-results/pytest-results-py$(pythonVersion).xml \\\n"
            "  --cov=sigantry_core --cov-report=xml --cov-report=term \\\n"
            "  --cov-fail-under=85\n",
            "displayName": "pytest + coverage gate",
        },
        {
            "task": "PublishTestResults@2",
            "condition": "always()",
            "displayName": "Publish pytest results",
            "inputs": {
                "testResultsFiles": "test-results/pytest-results-py$(pythonVersion).xml",
                "testRunTitle": "sigantry_core pytest (py$(pythonVersion))",
            },
        },
        {
            "task": "PublishCodeCoverageResults@2",
            "condition": "always()",
            "displayName": "Publish coverage",
            "inputs": {"summaryFileLocation": "coverage.xml"},
        },
    ],
}

# The stage parameter that feeds each job parameter the seeded check runs.
_STAGE_TO_JOB = {
    "lintSourcePaths": "sourcePaths",
    "enableTypeCheck": "enableTypeCheck",
    "typeCheckPaths": "typeCheckPaths",
}


def _load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), path
    return doc


def _defaults(doc: dict[str, Any]) -> dict[str, Any]:
    return {p["name"]: p["default"] for p in doc["parameters"]}


def _stage(doc: dict[str, Any], name: str) -> dict[str, Any]:
    (stage,) = [s for s in doc["stages"] if s.get("stage") == name]
    return stage


def test_the_lint_job_template_is_pinned_whole() -> None:
    """A change to the job template updates the pin in the same commit, where review sees it."""
    assert _load(_LINT_JOB) == _EXPECTED_LINT_JOB_TEMPLATE


def test_the_ci_stages_lint_stage_and_pytest_job_are_pinned_whole() -> None:
    doc = _load(_CI_STAGES)
    assert _stage(doc, "lint") == _EXPECTED_LINT_STAGE
    (pytest_job,) = [j for j in _stage(doc, "test")["jobs"] if j.get("job") == "pytest"]
    assert pytest_job == _EXPECTED_PYTEST_JOB


def test_the_lint_stage_hands_the_job_the_defaults_the_seeded_check_ran() -> None:
    """Through the stage, the stage's defaults reach the job; they must be the job's own."""
    stage = _defaults(_load(_CI_STAGES))
    job = _defaults(_load(_LINT_JOB))
    for stage_name, job_name in _STAGE_TO_JOB.items():
        assert stage[stage_name] == job[job_name], (
            f"stages/ci.yml {stage_name}={stage[stage_name]!r} but "
            f"jobs/lint-python.yml {job_name}={job[job_name]!r}"
        )


def test_the_dev_extra_carries_the_tools_the_lint_steps_call() -> None:
    """The job installs ``.[dev]`` and then calls ruff and mypy from it."""
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = extras["project"]["optional-dependencies"]["dev"]
    names = {re.split(r"[\s<>=!~;\[]", req, maxsplit=1)[0].lower() for req in dev}
    assert {"ruff", "mypy"} <= names, f"the dev extra lacks a lint tool: {sorted(names)}"
