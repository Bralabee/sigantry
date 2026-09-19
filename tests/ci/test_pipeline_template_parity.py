"""Pipeline-template parity test (Plan 12-02 / PIPELINE-02).

Asserts that the ADO + GHA halves of the Sigantry pipeline pair conform
to the dual-CI parity contract (Plan 10-06 + RESEARCH.md Pattern 1 / 2).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ADO_TEMPLATE = REPO_ROOT / "templates" / "stages" / "sigantry-cd.yml"
GHA_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sigantry-cd.yml"
LINT_SCRIPT = REPO_ROOT / "scripts" / "ci" / "check-dual-ci-parity.py"


def _gha_on(doc: dict[Any, Any]) -> dict[str, Any]:
    """Resolve the `on:` block defensively against YAML 1.1 truthy parsing.

    PyYAML 1.1 (project default) parses unquoted bare ``on:`` as Python
    ``True`` -- the dict key becomes ``True``, not the string ``"on"``.
    The existing parity lint at scripts/ci/check-dual-ci-parity.py:_gha_on
    does this same lookup; mirror it here so the test stays robust to
    YAML library version drift (Pitfall 4 / RESEARCH lines 721-729).
    """
    return doc.get("on") if doc.get("on") is not None else doc.get(True, {})


def test_pipeline_template_pair_exists() -> None:
    """Both paired YAML files must exist."""
    assert ADO_TEMPLATE.is_file(), f"missing: {ADO_TEMPLATE}"
    assert GHA_WORKFLOW.is_file(), f"missing: {GHA_WORKFLOW}"


def test_pipeline_pair_carries_no_dual_ci_exception() -> None:
    """First non-exception pair (PATTERNS.md line 13)."""
    ado_text = ADO_TEMPLATE.read_text("utf-8")
    gha_text = GHA_WORKFLOW.read_text("utf-8")
    assert "sigantry-dual-ci-exception" not in ado_text, (
        "ADO template must NOT carry the ci-mechanics exception annotation"
    )
    assert "sigantry-dual-ci-exception" not in gha_text, (
        "GHA workflow must NOT carry the ci-mechanics exception annotation"
    )


def test_pipeline_pair_declares_five_stages() -> None:
    """deploy -> smoke -> integration -> approval -> promote with parity DAG."""
    ado_doc = yaml.safe_load(ADO_TEMPLATE.read_text("utf-8"))
    gha_doc = yaml.safe_load(GHA_WORKFLOW.read_text("utf-8"))
    expected = {"deploy", "smoke", "integration", "approval", "promote"}
    ado_stages = {s["stage"] for s in ado_doc["stages"]}
    gha_jobs = set(gha_doc["jobs"].keys())
    assert ado_stages == expected, f"ADO stages mismatch: {ado_stages}"
    assert gha_jobs == expected, f"GHA jobs mismatch: {gha_jobs}"


def test_pipeline_approval_uses_deployment_job_in_ado() -> None:
    """Pitfall 2: approval stage must be deployment:gate, not job:.

    Only deployment jobs trigger ADO environment checks (Approvals,
    Pre-deployment gates, etc.). A regular ``job:`` with ``environment:``
    is inert for approvals.
    """
    ado_doc = yaml.safe_load(ADO_TEMPLATE.read_text("utf-8"))
    approval = next(s for s in ado_doc["stages"] if s["stage"] == "approval")
    job = approval["jobs"][0]
    assert "deployment" in job, "approval stage must use deployment: not job: (Pitfall 2)"
    assert job.get("environment"), (
        "approval stage's deployment job must declare environment: (D-04)"
    )


def test_pipeline_approval_uses_environment_in_gha() -> None:
    """Pitfall 3: approval job carries environment:; promote does NOT carry the approval environment.

    Single-prompt invariant -- if both ``approval`` and ``promote`` carried
    the same protected environment, the approver would be prompted twice.

    Scope of THIS assertion: template-string level only -- ``approval_env``
    and ``promote_env`` are different ``${{ inputs.* }}`` expressions
    (``ghApprovalEnvironment`` vs ``environment``), so the templates
    declare DIFFERENT inputs. The runtime invariant -- that the configured
    GitHub environments are themselves distinct so the approver sees ONE
    prompt -- is verified by the HUMAN-UAT runbook (Plan 12-05 Task 2
    section 3). That section confirms the operator-configured
    ``ghApprovalEnvironment`` and deploy ``environment`` resolve to two
    different GitHub environments with their own protection rules.
    """
    gha_doc = yaml.safe_load(GHA_WORKFLOW.read_text("utf-8"))
    jobs = gha_doc["jobs"]
    assert "environment" in jobs["approval"], (
        "approval job must declare environment: (D-04 platform-native gate)"
    )
    approval_env = jobs["approval"]["environment"]
    promote_env = jobs["promote"].get("environment", "")
    # promote may carry an environment (the deploy env) but it MUST NOT be
    # the same string as the approval env -- that would prompt twice.
    assert approval_env != promote_env, (
        f"approval and promote must reference DIFFERENT environments to "
        f"avoid double-prompting (Pitfall 3); got both = {approval_env!r}"
    )
    # The promote job must carry the concurrency block (Pitfall 8).
    assert "concurrency" in jobs["promote"], (
        "promote job must declare concurrency: to queue concurrent runs (Pitfall 8)"
    )
    # Defensive on: lookup (Pitfall 4 -- YAML 1.1 truthy).
    on_block = _gha_on(gha_doc)
    assert "workflow_dispatch" in on_block, "GHA workflow must declare workflow_dispatch: trigger"
    assert "workflow_call" in on_block, (
        "GHA workflow must declare workflow_call: trigger (reusable workflow)"
    )


def test_dual_ci_parity_lint_passes_on_real_repo() -> None:
    """check-dual-ci-parity.py exits 0 against the real repo (Plan 10-06 invariant + Plan 12-02 closure)."""
    result = subprocess.run(
        [sys.executable, str(LINT_SCRIPT), "--root", str(REPO_ROOT), "--verbose"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Dual-CI parity lint failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
