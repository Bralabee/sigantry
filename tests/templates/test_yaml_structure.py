"""Structural assertions over every shipped ADO YAML template (Plan 05-01;
Plan 08-03 productised the template filename + parameterisation).

Covers 14 templates across four subdirectories (extends/, stages/, jobs/,
steps/) plus 1 pre-existing parameters.example.yml. Tests lock:

- Directory layout (Microsoft canonical: extends / stages / jobs / steps).
- Every template parses via PyYAML safe_load.
- secure-pipeline.yml parameter surface (stages, agentPool, pythonVersion,
  artifactsFeed, pesterVersion, psscriptanalyzerVersion).
- security_scan / post_audit envelope stages + stageList iteration.
- cd-*.yml target their named environment (fabric-dev/test/prod).
- cd-prod condition + dependsOn.
- approval-gate.yml shape.
- Every `@self` path reference resolves on disk (Pitfall H).
- No CmdLine@2 task in any shipped stage/job/step template (T-5-02).
- Every template carries a source-citation docstring (Pitfall 14, T-5-14).
- fabric-deploy.yml uses AzureCLI@2.
- fabric-validate.yml invokes `fabric-dataops deploy validate`.
- security_scan stage wires the tag-pin lint.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_ROOT = Path("templates")


def test_templates_directory_layout() -> None:
    assert (TEMPLATE_ROOT / "extends").is_dir()
    assert (TEMPLATE_ROOT / "stages").is_dir()
    assert (TEMPLATE_ROOT / "jobs").is_dir()
    assert (TEMPLATE_ROOT / "steps").is_dir()


def test_all_templates_parse(parsed_templates: dict) -> None:
    # parsed_templates fixture raises on any parse failure; assert non-empty.
    assert parsed_templates, "no templates discovered under templates/"
    # Every parsed value is a dict (ADO YAML top-level is a mapping).
    for path, doc in parsed_templates.items():
        assert isinstance(doc, dict), f"{path}: top-level must be a mapping"


def test_all_templates_present() -> None:
    expected = {
        "templates/extends/secure-pipeline.yml",
        "templates/stages/ci.yml",
        "templates/stages/cd-dev.yml",
        "templates/stages/cd-test.yml",
        "templates/stages/cd-prod.yml",
        "templates/stages/approval-gate.yml",
        "templates/jobs/lint-python.yml",
        "templates/jobs/lint-powershell.yml",
        "templates/jobs/build-python.yml",
        "templates/jobs/build-powershell.yml",
        "templates/steps/fabric-deploy.yml",
        "templates/steps/fabric-git-commit.yml",
        "templates/steps/fabric-vl-apply.yml",
        "templates/steps/fabric-validate.yml",
    }
    found = {p.as_posix() for p in TEMPLATE_ROOT.rglob("*.yml")}
    missing = expected - found
    assert not missing, f"missing shipped templates: {missing}"


def _params_by_name(doc: dict) -> dict[str, dict]:
    return {p["name"]: p for p in doc.get("parameters", [])}


def test_extends_template_shape(extends_template: dict) -> None:
    params = _params_by_name(extends_template)
    for name in (
        "stages",
        "agentPool",
        "pythonVersion",
        "artifactsFeed",
        "pesterVersion",
        "psscriptanalyzerVersion",
    ):
        assert name in params, f"secure-pipeline.yml missing parameter {name!r}"
    assert params["stages"]["type"] == "stageList"


def test_agent_pool_parameter_default(extends_template: dict) -> None:
    params = _params_by_name(extends_template)
    assert params["agentPool"]["default"] == "Azure Pipelines"


def test_agent_pool_accepts_self_hosted_override(extends_source: str) -> None:
    """Pool selector compiles ubuntu-latest OR named self-hosted pool (Pitfall 5)."""
    assert "ne(parameters.agentPool" in extends_source
    assert "name: ${{ parameters.agentPool }}" in extends_source


def test_extends_has_security_scan_stage(extends_template: dict) -> None:
    ids = {s.get("stage") for s in extends_template["stages"] if isinstance(s, dict)}
    assert "security_scan" in ids


def test_extends_iterates_consumer_stages(extends_source: str) -> None:
    assert "each stage in parameters.stages" in extends_source


def test_extends_has_post_audit_stage(extends_template: dict) -> None:
    post = next(
        (
            s
            for s in extends_template["stages"]
            if isinstance(s, dict) and s.get("stage") == "post_audit"
        ),
        None,
    )
    assert post is not None
    assert post.get("condition") == "always()"


def test_security_scan_runs_template_refs_lint(extends_source: str) -> None:
    """Tag-pin lint (Pitfall A) is wired into the security envelope."""
    assert "scripts/ci/check-template-refs.py" in extends_source


def _cd_doc(name: str) -> dict:
    import yaml

    return yaml.safe_load(Path(f"templates/stages/{name}.yml").read_text(encoding="utf-8"))


def _deployment_env(doc: dict, stage_id: str) -> str | None:
    stage = next(
        (s for s in doc["stages"] if isinstance(s, dict) and s.get("stage") == stage_id),
        None,
    )
    assert stage is not None, f"stage {stage_id} not found"
    jobs = stage.get("jobs", [])
    dep = next((j for j in jobs if isinstance(j, dict) and "deployment" in j), None)
    assert dep is not None, f"deployment job not found in stage {stage_id}"
    return dep.get("environment")


def test_cd_dev_targets_environment() -> None:
    assert _deployment_env(_cd_doc("cd-dev"), "cd_dev") == "fabric-dev"


def test_cd_test_targets_environment() -> None:
    assert _deployment_env(_cd_doc("cd-test"), "cd_test") == "fabric-test"


def test_cd_prod_targets_environment() -> None:
    assert _deployment_env(_cd_doc("cd-prod"), "cd_prod") == "fabric-prod"


def test_cd_prod_depends_on_cd_test() -> None:
    doc = _cd_doc("cd-prod")
    stage = next(s for s in doc["stages"] if s.get("stage") == "cd_prod")
    # dependsOn is a template-expression by default; check source text for the
    # default value in the parameters block.
    params = _params_by_name(doc)
    assert params["dependsOn"]["default"] == ["cd_test"]
    # And the stage itself references the dependsOn parameter.
    assert "dependsOn" in stage


def test_cd_prod_requires_master_branch() -> None:
    doc = _cd_doc("cd-prod")
    params = _params_by_name(doc)
    cond = params["condition"]["default"]
    assert "Build.SourceBranchName" in cond
    assert "master" in cond


def test_approval_gate_shape() -> None:
    import yaml

    doc = yaml.safe_load(Path("templates/stages/approval-gate.yml").read_text(encoding="utf-8"))
    params = _params_by_name(doc)
    # `environment` parameter has no default (required).
    assert "environment" in params
    assert "default" not in params["environment"]
    # Stage has a deployment job named 'gate'.
    stage = doc["stages"][0]
    jobs = stage["jobs"]
    dep = next((j for j in jobs if isinstance(j, dict) and j.get("deployment") == "gate"), None)
    assert dep is not None, "approval-gate.yml must declare deployment: gate"


_SELF_REF = re.compile(r"template:\s*(\S+?)@self")


def test_every_self_reference_resolves() -> None:
    """Pitfall H: every `@self` path points at an existing file."""
    for p in TEMPLATE_ROOT.rglob("*.yml"):
        text = p.read_text(encoding="utf-8")
        for match in _SELF_REF.finditer(text):
            ref = match.group(1).strip("'\"")
            # refs are either absolute repo paths (templates/...) or relative
            # (../jobs/... / ../steps/...).
            if ref.startswith("templates/"):
                target = Path(ref)
            else:
                target = (p.parent / ref).resolve()
                repo_root = Path.cwd().resolve()
                target = target.relative_to(repo_root) if target.is_absolute() else target
            assert Path(target).exists() or (Path.cwd() / target).exists(), (
                f"{p}: @self reference {ref!r} does not resolve to a file on disk"
            )


def test_templates_have_docstrings() -> None:
    """Pitfall 14: every template starts with a `#` comment (source citation)."""
    for p in TEMPLATE_ROOT.rglob("*.yml"):
        first = p.read_text(encoding="utf-8").lstrip().splitlines()
        assert first, f"{p} is empty"
        assert first[0].startswith("#"), (
            f"{p}: first non-blank line must be a `#` comment (Pitfall 14)"
        )


def test_fabric_deploy_step_uses_azure_cli() -> None:
    text = Path("templates/steps/fabric-deploy.yml").read_text(encoding="utf-8")
    assert "AzureCLI@2" in text
    assert "azureSubscription: ${{ parameters.serviceConnection }}" in text
    # Post-Plan-10-02 (ADR-0011): CLI renamed from fabric-dataops-toolkits to sigantry.
    assert "sigantry deploy run" in text


def test_fabric_validate_step_invokes_validate_cli() -> None:
    text = Path("templates/steps/fabric-validate.yml").read_text(encoding="utf-8")
    # Post-Plan-10-02 (ADR-0011): CLI renamed from fabric-dataops-toolkits to sigantry.
    assert "sigantry deploy validate" in text


_TASK_DECL = re.compile(r"^\s*-\s+task:\s*CmdLine@2", re.MULTILINE)


def test_no_cmdline_task_in_stage_templates() -> None:
    """Security-templates pattern anchor (T-5-02): no CmdLine@2 in shipped YAML."""
    for directory in ("stages", "jobs", "steps"):
        for p in (TEMPLATE_ROOT / directory).rglob("*.yml"):
            text = p.read_text(encoding="utf-8")
            assert not _TASK_DECL.search(text), f"{p}: CmdLine@2 task declaration is prohibited"


def test_ci_yml_has_four_stages() -> None:
    import yaml

    doc = yaml.safe_load(Path("templates/stages/ci.yml").read_text(encoding="utf-8"))
    ids = {s.get("stage") for s in doc["stages"] if isinstance(s, dict)}
    assert {"lint", "test", "build", "publish"}.issubset(ids), ids
