"""The CD and drift template pairs authenticate, pin, and approve before they publish.

Persona-swarm item 4b. Measured at 1.0.1: the GitHub reusable workflows
could not authenticate to Azure as shipped (no login step, no
``id-token: write``), installed whatever ``pip install sigantry`` resolved
to, published BEFORE the approval gate, and recorded the run's actor as the
approver. On ADO, every stage after deploy ran ``sigantry`` on a fresh
agent that never installed it. Each test here fails on the templates before
this change.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GHA_CD = REPO_ROOT / ".github" / "workflows" / "sigantry-cd.yml"
GHA_DRIFT = REPO_ROOT / ".github" / "workflows" / "drift-check.yml"
ADO_CD = REPO_ROOT / "templates" / "stages" / "sigantry-cd.yml"
ADO_DRIFT = REPO_ROOT / "templates" / "schedules" / "drift-check.yml"
ADO_INSTALL = REPO_ROOT / "templates" / "steps" / "sigantry-install.yml"

_SHA_PIN = re.compile(r"^azure/login@[0-9a-f]{40}$")
_INSTALL = '"sigantry${SIGANTRY_VERSION:+==$SIGANTRY_VERSION}"'


def _load(path: pathlib.Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text("utf-8"))


def _on(doc: dict[Any, Any]) -> dict[str, Any]:
    return doc.get("on") if doc.get("on") is not None else doc.get(True, {})


def _steps(doc: dict[str, Any], job: str) -> list[dict[str, Any]]:
    return doc["jobs"][job]["steps"]


def _permissions(doc: dict[str, Any], job: str) -> dict[str, Any]:
    return doc["jobs"][job].get("permissions") or {}


def _login_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in steps if str(s.get("uses", "")).startswith("azure/login@")]


def _run(step: dict[str, Any]) -> str:
    return str(step.get("run", ""))


# ---------------------------------------------------------------------------
# GHA: OIDC login
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [GHA_CD, GHA_DRIFT], ids=["cd", "drift"])
def test_gha_declares_the_identity_inputs_and_no_workflow_wide_token(path: pathlib.Path) -> None:
    doc = _load(path)
    assert "id-token" not in doc["permissions"], (
        "the OIDC token is granted per job, not workflow-wide"
    )
    for trigger in ("workflow_dispatch", "workflow_call"):
        inputs = _on(doc)[trigger]["inputs"]
        for name in ("azureClientId", "azureTenantId"):
            assert inputs[name]["required"] is True, (
                f"{path.name}: {trigger}.{name} must be required"
            )
        assert inputs["sigantryVersion"]["required"] is False
        assert inputs["sigantryVersion"]["default"] == ""


@pytest.mark.parametrize(
    ("path", "jobs_with_login", "jobs_without"),
    [
        (GHA_CD, ("deploy", "integration", "promote"), ("approval", "smoke")),
        (GHA_DRIFT, ("drift_check",), ("notify",)),
    ],
    ids=["cd", "drift"],
)
def test_gha_jobs_that_reach_fabric_log_in_first_with_a_pinned_action(
    path: pathlib.Path, jobs_with_login: tuple[str, ...], jobs_without: tuple[str, ...]
) -> None:
    doc = _load(path)
    for job in jobs_with_login:
        steps = _steps(doc, job)
        logins = _login_steps(steps)
        assert len(logins) == 1, f"{path.name}:{job} needs exactly one azure/login step"
        login = logins[0]
        assert _permissions(doc, job).get("id-token") == "write", f"{path.name}:{job}"
        assert _SHA_PIN.match(login["uses"]), f"{path.name}:{job}: azure/login must be SHA-pinned"
        assert login["with"]["client-id"] == "${{ inputs.azureClientId }}"
        assert login["with"]["tenant-id"] == "${{ inputs.azureTenantId }}"
        assert login["with"]["allow-no-subscriptions"] is True, "Fabric needs no subscription"
        install = next(i for i, s in enumerate(steps) if "pip install" in _run(s))
        assert steps.index(login) < install, f"{path.name}:{job}: login must precede the install"
    for job in jobs_without:
        assert not _login_steps(_steps(doc, job)), f"{path.name}:{job} does not reach Fabric"
        assert "id-token" not in _permissions(doc, job), f"{path.name}:{job} needs no token"


# ---------------------------------------------------------------------------
# GHA + ADO: the version pin reaches every install, through env, never interpolated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [GHA_CD, GHA_DRIFT], ids=["cd", "drift"])
def test_gha_every_install_honours_sigantry_version_through_env(path: pathlib.Path) -> None:
    doc = _load(path)
    installs = [
        (job, s) for job in doc["jobs"] for s in _steps(doc, job) if "pip install" in _run(s)
    ]
    assert installs, f"{path.name}: no pip install step found"
    for job, step in installs:
        run = _run(step)
        assert "${{" not in run, f"{path.name}:{job}: inputs reach bash via env (WR-05): {run!r}"
        assert step["env"]["SIGANTRY_VERSION"] == "${{ inputs.sigantryVersion }}", (
            f"{path.name}:{job}"
        )
        assert _INSTALL in run, f"{path.name}:{job}: {run!r}"


def test_ado_install_step_template_pins_through_env() -> None:
    doc = _load(ADO_INSTALL)
    names = {p["name"] for p in doc["parameters"]}
    assert {"sigantryVersion", "pythonVersion"} <= names
    script = next(s for s in doc["steps"] if "script" in s)
    assert "${{" not in script["script"], "the version must not be interpolated into the script"
    assert script["env"]["SIGANTRY_VERSION"] == "${{ parameters.sigantryVersion }}"
    assert _INSTALL in script["script"]


def _ado_stage(doc: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in doc["stages"] if s["stage"] == name)


def _ado_job_steps(stage: dict[str, Any]) -> list[dict[str, Any]]:
    job = stage["jobs"][0]
    if "deployment" in job:
        return job["strategy"]["runOnce"]["deploy"]["steps"]
    return job["steps"]


@pytest.mark.parametrize(
    ("path", "stages"),
    [(ADO_CD, ("smoke", "integration", "promote")), (ADO_DRIFT, ("drift_check", "notify"))],
    ids=["cd", "drift"],
)
def test_ado_every_stage_that_runs_sigantry_installs_it_with_the_pin(
    path: pathlib.Path, stages: tuple[str, ...]
) -> None:
    doc = _load(path)
    assert any(p["name"] == "sigantryVersion" and p["default"] == "" for p in doc["parameters"])
    for name in stages:
        steps = _ado_job_steps(_ado_stage(doc, name))
        installs = [
            s for s in steps if str(s.get("template", "")).endswith("steps/sigantry-install.yml")
        ]
        assert len(installs) == 1, f"{path.name}:{name} must install sigantry once"
        assert installs[0]["parameters"]["sigantryVersion"] == "${{ parameters.sigantryVersion }}"
        before = steps[: steps.index(installs[0])]
        assert all("checkout" in s for s in before), (
            f"{path.name}:{name}: install before any script"
        )


def test_ado_deploy_stage_forwards_the_pin_to_the_deploy_step_template() -> None:
    doc = _load(ADO_CD)
    step = _ado_job_steps(_ado_stage(doc, "deploy"))[0]
    assert step["template"].endswith("steps/fabric-deploy.yml")
    assert step["parameters"]["fabricDataopsVersion"] == "${{ parameters.sigantryVersion }}"


# ---------------------------------------------------------------------------
# Approval before publish, approver from the platform's record
# ---------------------------------------------------------------------------


def test_approval_gates_the_deploy_on_both_platforms() -> None:
    gha = _load(GHA_CD)
    assert "needs" not in gha["jobs"]["approval"], "approval must be the first job"
    assert gha["jobs"]["deploy"]["needs"] == ["approval"]
    assert gha["jobs"]["promote"]["needs"] == ["integration"]
    ado = _load(ADO_CD)
    assert _ado_stage(ado, "approval")["dependsOn"] == []
    assert _ado_stage(ado, "deploy")["dependsOn"] == ["approval"]
    assert _ado_stage(ado, "promote")["dependsOn"] == ["integration"]


def test_gha_promote_records_the_reviewer_from_the_approval_record() -> None:
    doc = _load(GHA_CD)
    assert _permissions(doc, "promote").get("actions") == "read", "the approval record needs it"
    for job in ("approval", "deploy", "smoke", "integration"):
        assert "actions" not in _permissions(doc, job), f"{job} needs no actions: read"
    steps = _steps(doc, "promote")
    resolve = next(s for s in steps if s.get("id") == "approver")
    assert "/actions/runs/$SIGANTRY_RUN_ID/approvals" in resolve["run"]
    assert 'select(.state == "approved")' in resolve["run"]
    assert "::warning::" in resolve["run"], "falling back to the actor must be said out loud"
    assert "${{" not in resolve["run"], "inputs reach bash via env (WR-05)"
    record = next(s for s in steps if s.get("name") == "sigantry release record")
    assert record["env"]["SIGANTRY_APPROVER"] == "${{ steps.approver.outputs.approver }}"
    assert steps.index(resolve) < steps.index(record)


# ---------------------------------------------------------------------------
# Review round 1 on PR #87: one OIDC subject for every Fabric job, the approver
# record scoped to the gate and above the input, the ADO integration stage
# able to run its default command, the deploy step pinned through env.
# ---------------------------------------------------------------------------

ADO_DEPLOY_STEP = ADO_INSTALL.with_name("fabric-deploy.yml")


def test_gha_fabric_jobs_declare_the_environment_for_the_oidc_subject() -> None:
    """deploy and integration logged in with a ref-based token subject while the
    runbook's one federated credential names the environment (AADSTS700213)."""
    doc = _load(GHA_CD)
    for job in ("deploy", "integration", "promote"):
        assert doc["jobs"][job].get("environment") == "${{ inputs.environment }}", job
    assert doc["jobs"]["approval"]["environment"] == "${{ inputs.ghApprovalEnvironment }}"
    assert "environment" not in doc["jobs"]["smoke"], "smoke reaches no Fabric endpoint"


def test_gha_approver_record_is_scoped_to_the_gate_and_outranks_the_input() -> None:
    doc = _load(GHA_CD)
    resolve = next(s for s in _steps(doc, "promote") if s.get("id") == "approver")
    run = resolve["run"]
    assert resolve["env"]["SIGANTRY_GATE_ENVIRONMENT"] == "${{ inputs.ghApprovalEnvironment }}"
    assert "--arg env" in run and ".name == $env" in run, "filter on the gate environment"
    assert "2>/dev/null" not in run and "|| true" not in run, "an API error must be said"
    assert run.index("/approvals") < run.index('-n "$SIGANTRY_APPROVER_INPUT"'), (
        "the record is read first; the input is a fallback"
    )
    assert run.count("::warning::") >= 3


def test_ado_integration_stage_installs_pytest_and_runs_under_the_service_connection() -> None:
    doc = _load(ADO_CD)
    steps = _ado_job_steps(_ado_stage(doc, "integration"))
    extras = next(s for s in steps if "pip install pytest" in s.get("script", ""))
    assert "'.[dev,test]'" in extras["script"]
    run = next(s for s in steps if s.get("task") == "AzureCLI@2")
    assert run["inputs"]["azureSubscription"] == "${{ parameters.serviceConnection }}"
    assert run["inputs"]["inlineScript"] == "${{ parameters.integrationCommand }}"
    assert steps.index(extras) < steps.index(run)


def test_ado_deploy_step_template_pins_through_env() -> None:
    doc = _load(ADO_DEPLOY_STEP)
    script = next(s for s in doc["steps"] if "script" in s)
    assert "${{" not in script["script"], "the version must not be interpolated (WR-05)"
    assert script["env"]["SIGANTRY_VERSION"] == "${{ parameters.fabricDataopsVersion }}"
    assert _INSTALL in script["script"]
