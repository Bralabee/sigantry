"""PR template parity (research D-13).

The GitHub and ADO pull-request templates MUST be byte-identical so the
contributor checklist does not drift between the two platforms. Phase 7
Plan 07-03 DOCS-04 pins this invariant.

Files checked:
  - .github/pull_request_template.md
  - .azuredevops/pull_request_template.md
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GITHUB_TEMPLATE = REPO_ROOT / ".github" / "pull_request_template.md"
ADO_TEMPLATE = REPO_ROOT / ".azuredevops" / "pull_request_template.md"


def test_github_pr_template_exists() -> None:
    assert GITHUB_TEMPLATE.is_file(), f"{GITHUB_TEMPLATE} is missing"


def test_ado_pr_template_exists() -> None:
    assert ADO_TEMPLATE.is_file(), f"{ADO_TEMPLATE} is missing"


def test_pr_templates_are_byte_identical() -> None:
    """D-13: GitHub and ADO PR templates must match byte-for-byte."""
    github_bytes = GITHUB_TEMPLATE.read_bytes()
    ado_bytes = ADO_TEMPLATE.read_bytes()
    assert github_bytes == ado_bytes, (
        "PR templates have drifted. Re-sync .github/pull_request_template.md "
        "and .azuredevops/pull_request_template.md per D-13."
    )


def test_pr_template_enforces_phase7_invariants() -> None:
    text = GITHUB_TEMPLATE.read_text(encoding="utf-8")
    # Dependency direction
    assert "aims_data_platform" in text
    assert "dq_framework" in text
    # httpx invariant
    assert "httpx" in text
    # No tag push rule
    assert "tag" in text.lower()
    # Conventional commits
    assert "conventional-commit" in text.lower() or "conventional commit" in text.lower()
