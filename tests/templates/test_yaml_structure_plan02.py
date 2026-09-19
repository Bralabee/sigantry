"""Structural assertions for Plan 05-02 YAML templates.

Covers:
  - ``templates/stages/validate-fabric-items.yml`` (PR-trigger dry-run stage).
  - ``templates/steps/post-pr-comment.yml`` (ADO git/threads REST-based PR comment).

Locks the Pitfall C mitigation (token via env var, NEVER inline in shell arg)
and the ADOPIPE-05 behaviour contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VALIDATE_PATH = _REPO_ROOT / "templates/stages/validate-fabric-items.yml"
_POST_PR_PATH = _REPO_ROOT / "templates/steps/post-pr-comment.yml"


@pytest.fixture(scope="module")
def validate_template() -> dict:
    assert _VALIDATE_PATH.exists(), f"missing {_VALIDATE_PATH}"
    doc = yaml.safe_load(_VALIDATE_PATH.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


@pytest.fixture(scope="module")
def validate_template_source() -> str:
    assert _VALIDATE_PATH.exists(), f"missing {_VALIDATE_PATH}"
    return _VALIDATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def post_pr_comment_source() -> str:
    assert _POST_PR_PATH.exists(), f"missing {_POST_PR_PATH}"
    return _POST_PR_PATH.read_text(encoding="utf-8")


def test_validate_template_parses(validate_template: dict) -> None:
    """YAML parses and exposes a ``stages`` top-level key."""
    assert "stages" in validate_template


def test_validate_template_runs_on_pr(validate_template: dict) -> None:
    """ADOPIPE-05: validate stage condition references PullRequest + Build.Reason."""
    stages = validate_template.get("stages") or []
    conds = [s.get("condition", "") for s in stages if isinstance(s, dict)]
    assert any("PullRequest" in c and "Build.Reason" in c for c in conds), (
        f"no PR-trigger condition in validate stage; conds={conds}"
    )


def test_validate_template_invokes_cli(validate_template_source: str) -> None:
    """YAML body must call the Plan 05-02 CLI subcommand.

    Post-Plan-10-02 (ADR-0011): CLI renamed from
    ``fabric-dataops-toolkits`` to ``sigantry``.
    """
    assert "sigantry deploy validate" in validate_template_source


def test_validate_template_publishes_artefacts(
    validate_template_source: str,
) -> None:
    """JUnit + DOT artefacts must be published via PublishPipelineArtifact@1."""
    assert "PublishPipelineArtifact@1" in validate_template_source
    assert "validate-artefacts" in validate_template_source


def test_validate_template_publishes_test_results(
    validate_template_source: str,
) -> None:
    """JUnit XML must be surfaced via PublishTestResults@2 for ADO test tab."""
    assert "PublishTestResults@2" in validate_template_source


def test_validate_template_uses_post_pr_comment(
    validate_template_source: str,
) -> None:
    """Validate stage wires in the post-pr-comment step template."""
    assert "templates/steps/post-pr-comment.yml@self" in validate_template_source


def test_validate_template_pip_install_editable(
    validate_template_source: str,
) -> None:
    """PR-branch code must be installed editable so the PR's CLI is validated."""
    # Matches `pip install -e .[dev]` with optional quoting variations.
    assert re.search(r"pip\s+install\s+-e\s+.?\.\[dev\]", validate_template_source), (
        "expected `pip install -e .[dev]` in the validate template"
    )


def test_post_pr_comment_uses_threads_endpoint(
    post_pr_comment_source: str,
) -> None:
    """Pitfall-C-adjacent: REST, not the non-existent ``az repos pr comment add``."""
    assert re.search(r"pullRequests/.*threads", post_pr_comment_source), (
        "post-pr-comment.yml must hit the /_apis/git/.../pullRequests/.../threads endpoint"
    )


def test_post_pr_comment_passes_token_via_env(
    post_pr_comment_source: str,
) -> None:
    """Pitfall C: token via env var SYSTEM_ACCESSTOKEN, NEVER inline in shell.

    Regression guard - process-table snooping (`ps`, `/proc/*/cmdline`) must
    not expose the Bearer token.
    """
    assert "SYSTEM_ACCESSTOKEN: $(System.AccessToken)" in post_pr_comment_source
    cmd_lines = [
        line for line in post_pr_comment_source.splitlines() if "curl" in line and "Bearer" in line
    ]
    for line in cmd_lines:
        assert "$(System.AccessToken)" not in line, (
            f"token must be passed via env var SYSTEM_ACCESSTOKEN; found inline in line: {line!r}"
        )


def test_post_pr_comment_noop_on_non_pr(
    post_pr_comment_source: str,
) -> None:
    """Graceful no-op when not a PR build (exit 0 with log line, not error)."""
    assert "SYSTEM_PULLREQUEST_PULLREQUESTID" in post_pr_comment_source


def test_post_pr_comment_uses_always_condition(
    post_pr_comment_source: str,
) -> None:
    """Comment must post regardless of previous step result (so failures annotate)."""
    assert "condition: always()" in post_pr_comment_source


def test_templates_have_docstring_citations(
    validate_template_source: str, post_pr_comment_source: str
) -> None:
    """Pitfall 14: every shipped template has a source citation at top."""
    for name, body in (
        ("validate-fabric-items.yml", validate_template_source),
        ("post-pr-comment.yml", post_pr_comment_source),
    ):
        assert body.lstrip().startswith("#"), f"{name} missing top-of-file comment"
        assert "learn.microsoft.com" in body, f"{name} missing MS Learn citation"
