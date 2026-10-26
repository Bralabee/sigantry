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
    assert step["parameters"]["sigantryVersion"] == "${{ parameters.sigantryVersion }}"
    assert "fabricDataopsVersion" not in step["parameters"], "pass the new name, not the alias"


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
    perms = _permissions(doc, "promote")
    assert perms.get("actions") == "read", "the approval record needs it"
    assert perms.get("issues") == "write", "sigantry release record posts to GitHub issues"
    for job in ("approval", "deploy", "smoke", "integration"):
        assert "actions" not in _permissions(doc, job), f"{job} needs no actions: read"
    steps = _steps(doc, "promote")
    resolve = next(s for s in steps if s.get("id") == "approver")
    assert "/actions/runs/$SIGANTRY_RUN_ID/approvals" in resolve["run"]
    assert 'select(.state == "approved")' in resolve["run"]
    assert "::error::" in resolve["run"], "missing approver or self-approval must fail closed"
    assert 'echo "approver=$SIGANTRY_ACTOR"' not in resolve["run"], (
        "never fall back to the triggering actor as approver (SIG-006)"
    )
    assert "${{" not in resolve["run"], "inputs reach bash via env (WR-05)"
    record = next(s for s in steps if s.get("name") == "sigantry release record")
    assert record["env"]["SIGANTRY_APPROVER"] == "${{ steps.approver.outputs.approver }}"
    assert record["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
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
    assert run.count("::warning::") >= 2
    assert run.count("::error::") >= 2


def test_ado_integration_stage_installs_pytest_and_runs_under_the_service_connection() -> None:
    doc = _load(ADO_CD)
    steps = _ado_job_steps(_ado_stage(doc, "integration"))
    extras = next(s for s in steps if "pip install pytest" in s.get("script", ""))
    assert "'.[dev,test]'" in extras["script"]
    run = next(s for s in steps if s.get("task") == "AzureCLI@2")
    assert run["inputs"]["azureSubscription"] == "${{ parameters.serviceConnection }}"
    assert "${{" not in run["inputs"]["inlineScript"], "command reaches bash via env (WR-05)"
    assert run["env"]["SIGANTRY_INTEGRATION_COMMAND"] == "${{ parameters.integrationCommand }}"
    assert steps.index(extras) < steps.index(run)


def test_ado_deploy_step_template_pins_through_env() -> None:
    doc = _load(ADO_DEPLOY_STEP)
    script = next(s for s in doc["steps"] if "script" in s)
    assert "${{" not in script["script"], "the version must not be interpolated (WR-05)"
    assert script["env"]["SIGANTRY_VERSION"] == _VERSION_WITH_ALIAS
    assert _INSTALL in script["script"]


# ---------------------------------------------------------------------------
# #17: `sigantryVersion` on every step template that installs sigantry, with
# `fabricDataopsVersion` kept as a deprecated alias that warns when it is set.
# ---------------------------------------------------------------------------

_VERSION_WITH_ALIAS = "${{ coalesce(parameters.sigantryVersion, parameters.fabricDataopsVersion) }}"
_ALIAS_SET = "${{ if ne(parameters.fabricDataopsVersion, '') }}"
_VERSIONED_STEP_TEMPLATES = (
    "fabric-deploy.yml",
    "fabric-validate.yml",
    "fabric-vl-apply.yml",
    "fabric-git-commit.yml",
)


@pytest.mark.parametrize("name", _VERSIONED_STEP_TEMPLATES)
def test_ado_step_template_takes_sigantry_version_and_the_deprecated_alias(name: str) -> None:
    path = ADO_INSTALL.with_name(name)
    doc = _load(path)
    params = {p["name"]: p for p in doc["parameters"]}
    for param in ("sigantryVersion", "fabricDataopsVersion"):
        assert params[param]["type"] == "string" and params[param]["default"] == "", (name, param)

    installs = [s for s in doc["steps"] if "pip install" in s.get("script", "")]
    assert len(installs) == 1, f"{name}: one install step"
    install = installs[0]
    assert "${{" not in install["script"], f"{name}: the version must not be interpolated (WR-05)"
    assert _INSTALL in install["script"], name
    assert install["env"]["SIGANTRY_VERSION"] == _VERSION_WITH_ALIAS, (
        f"{name}: sigantryVersion first, the alias only when it is empty"
    )

    warns = [s for s in doc["steps"] if _ALIAS_SET in s]
    assert len(warns) == 1 and len(warns[0]) == 1, f"{name}: one warning, inserted only if set"
    (warn,) = warns[0][_ALIAS_SET]
    assert warn["bash"].startswith('echo "##vso[task.logissue type=warning]'), name
    assert "fabricDataopsVersion" in warn["bash"] and "sigantryVersion" in warn["bash"], name
    assert "${{" not in warn["bash"], f"{name}: no parameter is interpolated into the warning"
    assert doc["steps"].index(warns[0]) < doc["steps"].index(install), f"{name}: warn first"

    # Nothing else reads the old name: it is the alias inside the coalesce and
    # the condition of the warning, and nowhere else.
    text = path.read_text("utf-8")
    assert text.count("parameters.fabricDataopsVersion") == 2, name


# ---------------------------------------------------------------------------
# Wave 1 CD & drift pipeline hardening:
# - SIG-006: no requester self-approval default on ADO; fail-closed on GHA
# - cd-promote-empty-workitems-fails: workItems required / validated in approval
# - gha-integration-requires-pyproject: GHA integration installs pytest + guards extras
# - template-param-shell-interpolation (WR-05): no ${{ }} in run/script/inlineScript
# - ado-feed-install-inconsistent: artifactsFeed consistent across install & CD
# - gha-cd-concurrency-only-on-promote: top-level concurrency per environment
# - SIG-002 / ci-fabric-items-from-static-scan: .platform metadata + itemTypes filter
# ---------------------------------------------------------------------------


def test_ado_promote_rejects_self_approval_and_has_no_requester_default() -> None:
    """SIG-006: ADO sigantry-cd.yml must not default approver to $(Build.RequestedForEmail)
    and must reject empty or self-approval in promote."""
    doc = _load(ADO_CD)
    params = {p["name"]: p for p in doc["parameters"]}
    assert params["approver"].get("default", "") == "", (
        "ADO approver must not default to $(Build.RequestedForEmail) (SIG-006)"
    )
    steps = _ado_job_steps(_ado_stage(doc, "promote"))
    record = next(s for s in steps if s.get("displayName") == "sigantry release record")
    script = record["inputs"]["inlineScript"]
    assert "SIGANTRY_REQUESTED_FOR_EMAIL" in record["env"]
    assert "$SIGANTRY_APPROVER" in script and "$SIGANTRY_REQUESTED_FOR_EMAIL" in script
    assert "Self-approval is not permitted" in script


def test_cd_pipeline_pair_validates_work_items_before_deploy() -> None:
    """cd-promote-empty-workitems-fails: workItems is required/validated in approval before deploy."""
    gha = _load(GHA_CD)
    for trigger in ("workflow_dispatch", "workflow_call"):
        wi = _on(gha)[trigger]["inputs"]["workItems"]
        assert wi["required"] is True, f"GHA {trigger}.workItems must be required"
    gha_approval_steps = _steps(gha, "approval")
    assert any(
        "SIGANTRY_WORK_ITEMS" in (s.get("env") or {}) and "SIGANTRY_WORK_ITEMS" in _run(s)
        for s in gha_approval_steps
    ), "GHA approval job must validate SIGANTRY_WORK_ITEMS before deploy runs"

    ado = _load(ADO_CD)
    ado_approval_steps = _ado_job_steps(_ado_stage(ado, "approval"))
    assert any(
        "SIGANTRY_WORK_ITEMS" in (s.get("env") or {})
        and "SIGANTRY_WORK_ITEMS" in str(s.get("script", ""))
        for s in ado_approval_steps
    ), "ADO approval stage must validate SIGANTRY_WORK_ITEMS before deploy runs"


def test_gha_integration_stage_installs_pytest_and_guards_editable_install() -> None:
    """gha-integration-requires-pyproject: GHA integration job matches ADO."""
    doc = _load(GHA_CD)
    steps = _steps(doc, "integration")
    extras = next(
        (s for s in steps if "pip install pytest" in _run(s)),
        None,
    )
    assert extras is not None, (
        "GHA integration job must install pytest before running integration suite"
    )
    run = _run(extras)
    assert "if [ -f pyproject.toml ] || [ -f setup.py ]; then" in run
    assert "'.[dev,test]'" in run


def test_cd_templates_never_interpolate_inputs_or_parameters_into_shell_scripts() -> None:
    """WR-05 / template-param-shell-interpolation: all inputs/parameters reach bash via env."""
    gha = _load(GHA_CD)
    for job_name, job in gha["jobs"].items():
        for step in job.get("steps", []):
            if "run" in step:
                assert "${{" not in step["run"], (
                    f"GHA sigantry-cd.yml:{job_name} interpolates ${{}} in run: {step['run']!r}"
                )

    ado = _load(ADO_CD)
    for stage in ado["stages"]:
        stage_name = stage["stage"]
        for step in _ado_job_steps(stage):
            for key in ("script", "bash"):
                if key in step:
                    assert "${{" not in str(step[key]), (
                        f"ADO sigantry-cd.yml:{stage_name} interpolates ${{}} in {key}"
                    )
            inline = (step.get("inputs") or {}).get("inlineScript", "")
            assert "${{" not in str(inline), (
                f"ADO sigantry-cd.yml:{stage_name} interpolates ${{}} in inlineScript"
            )

    deploy_step = _load(ADO_DEPLOY_STEP)
    for step in deploy_step["steps"]:
        inline = (step.get("inputs") or {}).get("inlineScript", "")
        assert "${{" not in str(inline), (
            f"fabric-deploy.yml interpolates ${{}} in inlineScript: {inline!r}"
        )


def test_ado_artifacts_feed_is_consistent_across_install_and_cd_templates() -> None:
    """ado-feed-install-inconsistent: artifactsFeed defaults to '' and threads through all stages."""
    install_doc = _load(ADO_INSTALL)
    install_params = {p["name"]: p for p in install_doc["parameters"]}
    assert "artifactsFeed" in install_params
    assert install_params["artifactsFeed"]["default"] == ""
    assert any("${{ if ne(parameters.artifactsFeed, '') }}" in s for s in install_doc["steps"]), (
        "sigantry-install.yml must conditionally run PipAuthenticate@1 when artifactsFeed is set"
    )

    deploy_doc = _load(ADO_DEPLOY_STEP)
    deploy_params = {p["name"]: p for p in deploy_doc["parameters"]}
    assert deploy_params["artifactsFeed"]["default"] == ""

    for path, stages in (
        (ADO_CD, ("smoke", "integration", "promote")),
        (ADO_DRIFT, ("drift_check", "notify")),
    ):
        doc = _load(path)
        params = {p["name"]: p for p in doc["parameters"]}
        assert "artifactsFeed" in params and params["artifactsFeed"]["default"] == ""
        for stage_name in stages:
            steps = _ado_job_steps(_ado_stage(doc, stage_name))
            install = next(
                s
                for s in steps
                if str(s.get("template", "")).endswith("steps/sigantry-install.yml")
            )
            assert (
                install["parameters"].get("artifactsFeed") == "${{ parameters.artifactsFeed }}"
            ), f"{path.name}:{stage_name} must forward artifactsFeed to sigantry-install.yml"

    cd_doc = _load(ADO_CD)
    deploy_template_step = _ado_job_steps(_ado_stage(cd_doc, "deploy"))[0]
    assert (
        deploy_template_step["parameters"].get("artifactsFeed") == "${{ parameters.artifactsFeed }}"
    )


def test_gha_cd_declares_workflow_level_concurrency_per_environment() -> None:
    """gha-cd-concurrency-only-on-promote: workflow-level concurrency serialises deploy+integration+promote."""
    doc = _load(GHA_CD)
    concurrency = doc.get("concurrency")
    assert isinstance(concurrency, dict), "GHA sigantry-cd.yml must declare top-level concurrency"
    assert concurrency.get("group") == "deploy-${{ inputs.environment }}"
    assert concurrency.get("cancel-in-progress") is False


def test_cd_promote_fabric_items_discovery_reads_platform_metadata_and_filters_item_types(
    tmp_path: pathlib.Path,
) -> None:
    """SIG-002 / ci-fabric-items-from-static-scan: promote auto-discovery supports sync pull
    directories (.platform metadata) and filters to SIGANTRY_ITEM_TYPES."""
    import json
    import os
    import subprocess

    # 1. Classic <Name>.<Type> directory in scope
    nb_dir = tmp_path / "etl_daily.Notebook"
    nb_dir.mkdir()
    (nb_dir / ".platform").write_text(
        json.dumps({"metadata": {"type": "Notebook", "displayName": "etl_daily"}}),
        encoding="utf-8",
    )
    # 2. sync pull directory without .<Type> suffix in directory name, in scope
    lh_dir = tmp_path / " bronze" / "sales_curated"
    lh_dir.mkdir(parents=True)
    (lh_dir / ".platform").write_text(
        json.dumps({"metadata": {"type": "Lakehouse", "displayName": "sales_curated"}}),
        encoding="utf-8",
    )
    # 3. Out-of-scope item type (Report not in Notebook,Lakehouse)
    rpt_dir = tmp_path / "exec_summary.Report"
    rpt_dir.mkdir()
    (rpt_dir / ".platform").write_text(
        json.dumps({"metadata": {"type": "Report", "displayName": "exec_summary"}}),
        encoding="utf-8",
    )

    gha = _load(GHA_CD)
    gha_record = next(
        s for s in _steps(gha, "promote") if s.get("name") == "sigantry release record"
    )
    assert gha_record["env"].get("SIGANTRY_ITEM_TYPES") == "${{ inputs.itemTypes }}"

    ado = _load(ADO_CD)
    ado_record = next(
        s
        for s in _ado_job_steps(_ado_stage(ado, "promote"))
        if s.get("displayName") == "sigantry release record"
    )
    assert ado_record["env"].get("SIGANTRY_ITEM_TYPES") == "${{ parameters.itemTypes }}"

    for label, script in (
        ("GHA", gha_record["run"]),
        ("ADO", ado_record["inputs"]["inlineScript"]),
    ):
        # Extract the shell block up to `sigantry release record` and print SIGANTRY_FABRIC_ITEMS
        before_cli = (
            script.split("sigantry release record")[0] + '\nprintf "%s" "$SIGANTRY_FABRIC_ITEMS"\n'
        )
        env = {
            **os.environ,
            "SIGANTRY_FABRIC_ITEMS": "",
            "SIGANTRY_SOURCE_DIR": str(tmp_path),
            "SIGANTRY_ITEM_TYPES": "Notebook,Lakehouse",
            "SIGANTRY_APPROVER": "reviewer@example.com",
            "SIGANTRY_REQUESTED_FOR_EMAIL": "requester@example.com",
        }
        res = subprocess.run(
            ["bash", "-c", before_cli],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"{label} discovery failed: {res.stderr}"
        assert res.stdout == "etl_daily:Notebook,sales_curated:Lakehouse", (
            f"{label} discovery output mismatch: {res.stdout!r}"
        )
