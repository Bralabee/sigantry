"""Audit-2026-05-07 W3.5 -- meta-gate: ``.pre-commit-config.yaml`` carries
the standard hygiene + ruff + detect-secrets + mypy hook sets alongside
the 2 custom Fabric hooks.

Pre-W3.5 the config carried only the 2 ``fabric-check-*`` hooks
(logical-id + CRLF guards from Plan 04-02). The audit synthesis flagged
the missing standard set as the largest gap in pre-commit coverage --
no whitespace / EOF / yaml / json hygiene, no ruff at the pre-commit
boundary (so untyped lint regressions only surface in CI), no
secret-detection at the commit edge.

W3.5 adds:

- ``pre-commit/pre-commit-hooks`` v4.6.0: trailing-whitespace,
  end-of-file-fixer, check-yaml/json/toml, check-added-large-files,
  check-merge-conflict, check-case-conflict, mixed-line-ending.
- ``astral-sh/ruff-pre-commit`` v0.7.4: ``ruff`` (lint) +
  ``ruff-format``.
- ``Yelp/detect-secrets`` v1.5.0: secret detection at commit-time
  with a ``.secrets.baseline`` audit file.
- a mypy hook. It began as ``pre-commit/mirrors-mypy``, whose isolated
  env reported errors CI's mypy did not (issue #21); it is now a
  ``repo: local`` ``language: system`` hook whose entry is CI's own mypy
  command, run by the project env.
- The 2 custom Fabric hooks preserved unchanged.

Falsifiability: this test FAILS if any of the four documented hook
sets is dropped or renamed, or if the mypy hook's command drifts from
CI's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".pre-commit-config.yaml"
SECRETS_BASELINE_PATH = REPO_ROOT / ".secrets.baseline"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_pre_commit_config_exists() -> None:
    assert CONFIG_PATH.is_file()


@pytest.mark.parametrize(
    "expected_repo, expected_hook_ids",
    [
        (
            "https://github.com/pre-commit/pre-commit-hooks",
            [
                "trailing-whitespace",
                "end-of-file-fixer",
                "check-yaml",
                "check-json",
                "check-toml",
                "check-added-large-files",
                "check-merge-conflict",
                "check-case-conflict",
                "mixed-line-ending",
            ],
        ),
        (
            "https://github.com/astral-sh/ruff-pre-commit",
            ["ruff", "ruff-format"],
        ),
        (
            "https://github.com/Yelp/detect-secrets",
            ["detect-secrets"],
        ),
        (
            "local",
            ["mypy"],
        ),
    ],
)
def test_pre_commit_config_includes_documented_hook_set(
    expected_repo: str, expected_hook_ids: list[str]
) -> None:
    """Every documented W3.5 hook set is registered in the config."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    assert expected_repo in text, (
        f"Pre-commit config missing repo {expected_repo!r}. "
        "W3.5 hook sets removed without replacement."
    )
    for hook_id in expected_hook_ids:
        # Hook ids appear after a ``- id: `` prefix in the YAML.
        pattern = rf"-\s*id:\s*{re.escape(hook_id)}\b"
        assert re.search(pattern, text), (
            f"Pre-commit config missing hook id {hook_id!r} from {expected_repo!r}"
        )


def test_custom_fabric_hooks_preserved() -> None:
    """The 2 ``fabric-check-*`` hooks remain registered (Plan 04-02 lockdown)."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    for hook_id in ("fabric-check-logical-id", "fabric-check-crlf"):
        assert re.search(rf"-\s*id:\s*{re.escape(hook_id)}\b", text), (
            f"Custom Fabric hook {hook_id!r} dropped. "
            "Plan 04-02 (DEPLOY-02 + Pitfall 3) requires both hooks."
        )


def _ci_mypy_command() -> str:
    """The ``run`` of the mypy step in CI's ``types`` job."""
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    runs = [
        step["run"].strip()
        for step in workflow["jobs"]["types"]["steps"]
        if str(step.get("run", "")).strip().startswith("mypy ")
    ]
    assert len(runs) == 1, f"expected one mypy step in CI's types job, found {runs}"
    return str(runs[0])


def _mypy_hooks() -> list[tuple[str, dict]]:
    """``(repo, hook)`` for every hook whose id is ``mypy``."""
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return [
        (repo["repo"], hook)
        for repo in config["repos"]
        for hook in repo.get("hooks", [])
        if hook.get("id") == "mypy"
    ]


def test_mypy_hook_runs_the_ci_command_in_the_project_env() -> None:
    """Issue #21: the hook runs CI's mypy command with the project env's mypy.

    An isolated ``mirrors-mypy`` env lacked runtime types the project env has
    and reported errors CI did not. A ``language: system`` hook whose entry is
    CI's command, over whole roots, removes that difference.
    """
    hooks = _mypy_hooks()
    assert [repo for repo, _ in hooks] == ["local"], hooks
    hook = hooks[0][1]
    assert hook["language"] == "system"
    assert hook["entry"] == _ci_mypy_command() == "mypy sigantry_core/ scripts/"
    assert hook["pass_filenames"] is False
    assert "additional_dependencies" not in hook
    files = re.compile(hook["files"])
    for path in ("sigantry_core/auth/diagnose.py", "scripts/docs_freshness.py"):
        assert files.search(path), f"a change to {path} does not trigger the mypy hook"


def test_secrets_baseline_exists_and_is_valid_json() -> None:
    """``.secrets.baseline`` exists and is parseable JSON.

    detect-secrets refuses to start without a baseline; the baseline is
    a JSON file enumerating the active plugins + filters + the
    currently-allowlisted hits.
    """
    import json

    assert SECRETS_BASELINE_PATH.is_file(), (
        ".secrets.baseline is missing. detect-secrets pre-commit hook "
        "fails at install time without it. Regenerate via "
        "``detect-secrets scan > .secrets.baseline``."
    )
    data = json.loads(SECRETS_BASELINE_PATH.read_text(encoding="utf-8"))
    assert "version" in data
    assert "plugins_used" in data
    assert "results" in data


def test_ruff_pre_commit_args_match_pyproject_intent() -> None:
    """ruff pre-commit hook runs with --fix --exit-non-zero-on-fix.

    Without ``--fix`` the developer experience regresses to "lint, then
    re-run after manual fixes"; with it the hook auto-fixes safe issues.
    ``--exit-non-zero-on-fix`` makes the commit fail the FIRST time so
    the developer sees the change before re-staging.
    """
    text = CONFIG_PATH.read_text(encoding="utf-8")
    # Find the ruff hook block.
    ruff_block_match = re.search(
        r"-\s*id:\s*ruff\s*\n((?:\s+\S.*\n)+)",
        text,
    )
    assert ruff_block_match, "Could not locate the ruff hook block."
    block = ruff_block_match.group(1)
    assert "--fix" in block, "ruff hook missing --fix argument."
    assert "--exit-non-zero-on-fix" in block, "ruff hook missing --exit-non-zero-on-fix argument."
