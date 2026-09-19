"""Plan 14-01 / 14-06 / 14-07 starter-content presence assertions.

Wave 0 (Plan 14-00) stamped the starter directory + a placeholder README
and ships this file as xfail stubs. Plan 14-01 fills in parameters.yml +
docs/ + _partials/ + .github/ + .azuredevops/ -- those tests flip to
real assertions here. Plan 14-06 lands the workflow pair; those two
tests stay xfail until then. Plan 14-07 adds the export-script smoke
elsewhere.
"""

from __future__ import annotations

from pathlib import Path

# Local-import idiom matches tests/release/test_comment_parity.py:31
# (the repo does not ship a top-level tests/ package; import-by-module
# path through `tests.starter.conftest` is therefore not available).
# tests/starter/conftest.py keeps the STARTER_DIR constant so downstream
# plan tests can read it via pytest fixtures or by mirroring this idiom.
_STARTER_DIR = Path(__file__).resolve().parents[2] / "templates" / "starter"


def test_starter_directory_exists() -> None:
    """Wave 0 smoke: templates/starter/ is stamped on disk by Plan 14-00 Task 1.

    This is the only test in this file that fired immediately as a Wave 0
    cement smoke. It still passes after Plan 14-01.
    """
    assert _STARTER_DIR.is_dir(), f"templates/starter/ must exist; got {_STARTER_DIR}"


def test_starter_has_parameters_yml() -> None:
    """Plan 14-01 ships templates/starter/parameters.yml (3-env fabric-cicd substitution file)."""
    assert (_STARTER_DIR / "parameters.yml").is_file()


def test_starter_has_partials_pr_checklist_md() -> None:
    """Plan 14-01 ships templates/starter/_partials/pr-checklist.md (single-source PR checklist)."""
    assert (_STARTER_DIR / "_partials" / "pr-checklist.md").is_file()


def test_starter_has_github_pull_request_template() -> None:
    """Plan 14-01 ships templates/starter/.github/pull_request_template.md (rendered from _partials/)."""
    assert (_STARTER_DIR / ".github" / "pull_request_template.md").is_file()


def test_starter_has_azuredevops_pull_request_template() -> None:
    """Plan 14-01 ships templates/starter/.azuredevops/pull_request_template.md (rendered from _partials/)."""
    assert (_STARTER_DIR / ".azuredevops" / "pull_request_template.md").is_file()


def test_starter_has_docs_branching_md() -> None:
    """Plan 14-01 ships templates/starter/docs/BRANCHING.md (trunk-based; references ADR-0010 + ADR-0011)."""
    assert (_STARTER_DIR / "docs" / "BRANCHING.md").is_file()


def test_starter_has_docs_quickstart_md() -> None:
    """Plan 14-01 ships templates/starter/docs/QUICKSTART.md (15-minute adopter walkthrough)."""
    assert (_STARTER_DIR / "docs" / "QUICKSTART.md").is_file()


def test_starter_has_github_workflows_pr_bot_yml() -> None:
    """Plan 14-06 ships templates/starter/.github/workflows/pr-bot.yml."""
    assert (_STARTER_DIR / ".github" / "workflows" / "pr-bot.yml").is_file()


def test_starter_has_azuredevops_jobs_pr_bot_yml() -> None:
    """Plan 14-06 ships templates/starter/.azuredevops/jobs/pr-bot.yml."""
    assert (_STARTER_DIR / ".azuredevops" / "jobs" / "pr-bot.yml").is_file()
