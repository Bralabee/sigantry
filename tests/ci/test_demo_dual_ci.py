"""Demo CI workflow-pair parity test (Plan 15-03 / DEMO-02 CI half).

Mirror of tests/ci/test_starter_dual_ci.py. CONTEXT D-05: the demo
pair lives under templates/demo/.{github,azuredevops}/ and is
annotated with sigantry-dual-ci-exception: ci-mechanics so the
monorepo dual-CI parity registry skips it; this phase-specific
test enforces parity instead.

Six tests after the Plan 15-03 flip:

1. test_both_files_exist                                      -- pair on disk.
2. test_gha_paths_filter_includes_fabric_items_and_parameters_yml
                                                              -- GHA paths.
3. test_ado_paths_include_fabric_items_and_parameters_yml     -- ADO paths.
4. test_both_invoke_three_sigantry_commands                   -- 3-command parity.
5. test_export_demo_dry_run_clean_with_demo_ci_yamls_committed
                                                              -- export-demo
                                                                 parity gate
                                                                 still passes
                                                                 with the new
                                                                 YAMLs in tree.
6. test_ado_file_carries_ci_mechanics_exception_annotation    -- annotation
                                                                 in first 5
                                                                 lines per
                                                                 EXCEPTION_PATTERN.

Wave 0 (Plan 15-00) stamped four xfail stubs; Plan 15-03 fills the
bodies after the workflow YAML pair lands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GHA = REPO / "templates" / "demo" / ".github" / "workflows" / "sigantry-demo-ci.yml"
ADO = REPO / "templates" / "demo" / ".azuredevops" / "sigantry-demo-ci.yml"

REQUIRED_PATHS_FILTER = ("fabric_items/**", "parameters.yml")
REQUIRED_CMDS = ("sigantry deploy run", "sigantry release record", "sigantry diff")


def _gha_on(doc: object) -> dict:
    """PyYAML 1.1 parses unquoted ``on:`` as Python True; defend.

    Mirrors the lookup discipline from
    ``scripts/ci/check-dual-ci-parity.py:_gha_on`` and
    ``tests/ci/test_starter_dual_ci.py:_gha_on``.
    """
    if not isinstance(doc, dict):
        return {}
    if "on" in doc:
        val = doc["on"]
    elif True in doc:
        val = doc[True]
    else:
        return {}
    return val if isinstance(val, dict) else {}


def test_both_files_exist() -> None:
    """Plan 15-03 ships templates/demo/.github/workflows/sigantry-demo-ci.yml + .azuredevops/sigantry-demo-ci.yml."""
    assert GHA.is_file(), GHA
    assert ADO.is_file(), ADO


def test_gha_paths_filter_includes_fabric_items_and_parameters_yml() -> None:
    """GHA workflow's on.push.paths includes fabric_items/** and parameters.yml."""
    doc = yaml.safe_load(GHA.read_text())
    on = _gha_on(doc)
    paths = on["push"]["paths"]
    for p in REQUIRED_PATHS_FILTER:
        assert p in paths, f"GHA missing path filter {p!r}; got {paths!r}"


def test_ado_paths_include_fabric_items_and_parameters_yml() -> None:
    """ADO pipeline's trigger.paths.include references fabric_items and parameters.yml."""
    txt = ADO.read_text()
    for p in REQUIRED_PATHS_FILTER:
        # ADO YAML lists items as "include: [fabric_items, parameters.yml]" --
        # match the bare token (strip trailing /** wildcard).
        bare = p.rstrip("/*").rstrip("/")
        assert bare in txt, f"ADO missing path include {bare!r}"


def test_both_invoke_three_sigantry_commands() -> None:
    """Both workflow halves invoke the same 3 commands: sigantry deploy run, release record, diff (D-04)."""
    gha_txt = GHA.read_text()
    ado_txt = ADO.read_text()
    for cmd in REQUIRED_CMDS:
        assert cmd in gha_txt, f"GHA missing {cmd!r}"
        assert cmd in ado_txt, f"ADO missing {cmd!r}"


def test_export_demo_dry_run_clean_with_demo_ci_yamls_committed() -> None:
    """Confirms the demo content + the new YAML pair together still pass export-demo's parity gate.

    Plan 15-01 introduced ``scripts/export-demo.py --dry-run`` as the
    parity gate that asserts ``templates/demo/`` is internally
    consistent. Plan 15-03 adds two new YAML files to the demo tree;
    this test ratifies that the parity gate still exits 0 with the
    new files in place.
    """
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "export-demo.py"), "--dry-run"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"export-demo --dry-run failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_ado_file_carries_ci_mechanics_exception_annotation() -> None:
    """Annotation must appear in first 5 lines per scripts/ci/check-dual-ci-parity.py EXCEPTION_PATTERN.

    The ADO file lives under ``templates/demo/.azuredevops/`` which is
    OUTSIDE the dual-CI parity walker's current scan tree
    (``ADO_SUBDIRS = stages/schedules/pr-review``), but the annotation
    is required as defence-in-depth so any future extension of the
    walker correctly skips this adopter-consumed file (RESEARCH
    Anti-Pattern 4 + Phase 14 pr-bot.yml precedent).
    """
    head = ADO.read_text().splitlines()[:5]
    head_blob = "\n".join(head)
    assert "sigantry-dual-ci-exception: ci-mechanics" in head_blob, (
        f"annotation missing in first 5 lines of {ADO}; got:\n{head_blob}"
    )


def _gha_step_by_name(doc: dict, name: str) -> dict:
    steps = doc["jobs"]["deploy_record_diff"]["steps"]
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"GHA missing step {name!r}")


def _ado_step_by_display_name(doc: dict, display_name: str) -> dict:
    for step in doc["steps"]:
        if isinstance(step, dict) and step.get("displayName") == display_name:
            return step
    raise AssertionError(f"ADO missing step {display_name!r}")


def test_neither_workflow_references_dead_sigantry_fabric_token_env_var() -> None:
    """Issue #70 (3): neither workflow maps a dead SIGANTRY_FABRIC_TOKEN env var."""
    assert "SIGANTRY_FABRIC_TOKEN:" not in GHA.read_text(), (
        "GHA still sets dead SIGANTRY_FABRIC_TOKEN env var"
    )
    assert "SIGANTRY_FABRIC_TOKEN:" not in ADO.read_text(), (
        "ADO still sets dead SIGANTRY_FABRIC_TOKEN env var"
    )


def test_deploy_and_diff_steps_set_azure_credential_env_and_tenant_id() -> None:
    """Issue #70 (3): Deploy and Diff steps set AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET and pass --tenant-id."""
    gha_doc = yaml.safe_load(GHA.read_text())
    ado_doc = yaml.safe_load(ADO.read_text())

    for label, step in (
        ("GHA Deploy", _gha_step_by_name(gha_doc, "Deploy")),
        ("ADO Deploy", _ado_step_by_display_name(ado_doc, "Deploy")),
    ):
        env = step.get("env", {})
        for key in (
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "SIGANTRY_DEMO_WORKSPACE_ID",
            "SIGANTRY_DEMO_CAPACITY_ID",
        ):
            assert key in env, f"{label} missing env key {key!r}"
        script = step.get("run") or step.get("script") or ""
        assert "--tenant-id" in script, f"{label} missing --tenant-id"
        assert "--item-types" in script, f"{label} missing --item-types"

    for label, step in (
        ("GHA Diff", _gha_step_by_name(gha_doc, "Diff (fail on drift)")),
        ("ADO Diff", _ado_step_by_display_name(ado_doc, "Diff (fail on drift)")),
    ):
        env = step.get("env", {})
        for key in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
            assert key in env, f"{label} missing env key {key!r}"
        script = step.get("run") or step.get("script") or ""
        assert "--tenant-id" in script, f"{label} missing --tenant-id"


def test_record_release_step_passes_required_flags_and_runs_verify() -> None:
    """Issue #70 (4): Record release uses --workspace (not --workspace-id), required flags, and release verify."""
    gha_doc = yaml.safe_load(GHA.read_text())
    ado_doc = yaml.safe_load(ADO.read_text())

    for label, step, expected_provider in (
        ("GHA Record release", _gha_step_by_name(gha_doc, "Record release"), "github"),
        ("ADO Record release", _ado_step_by_display_name(ado_doc, "Record release"), "ado"),
    ):
        script = step.get("run") or step.get("script") or ""
        assert "--workspace-id" not in script, f"{label} still uses dead --workspace-id flag"
        for required_flag in (
            "--release-id",
            "--workspace",
            f"--provider {expected_provider}",
            "--work-items",
            "--approver",
            "--fabric-items",
            "--audit-dir",
            "sigantry release verify",
        ):
            assert required_flag in script, f"{label} missing {required_flag!r}"


def _extract_cli_commands(script: str, env_map: dict[str, str]) -> list[list[str]]:
    """Expand shell/pipeline variables in a multi-line step script and split into sigantry argv lists."""
    import os
    import re
    import shlex

    joined = script.replace("\\\n", " ")
    commands: list[list[str]] = []
    for raw_line in joined.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Expand ADO $(VAR) syntax first.
        line = re.sub(
            r"\$\(([^)]+)\)",
            lambda m: env_map.get(m.group(1), ""),
            line,
        )
        # Expand bash ${VAR:-default} and ${VAR#*/} and ${VAR}.
        line = re.sub(
            r"\$\{([A-Za-z0-9_]+):-([^}]*)\}",
            lambda m: env_map.get(m.group(1)) or m.group(2),
            line,
        )
        line = re.sub(
            r"\$\{([A-Za-z0-9_]+)#\*/\}",
            lambda m: env_map.get(m.group(1), "").split("/", 1)[-1],
            line,
        )
        line = re.sub(
            r"\$\{([A-Za-z0-9_]+)\}",
            lambda m: env_map.get(m.group(1), os.environ.get(m.group(1), "")),
            line,
        )
        argv = shlex.split(line)
        assert argv and argv[0] == "sigantry", f"Expected sigantry command, got {argv!r}"
        commands.append(argv[1:])
    return commands


def test_demo_ci_workflows_execute_cleanly_in_hermetic_runner(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Issue #70 (3 & 4): Execute parsed GHA and ADO Deploy, Record release, and Diff steps via CliRunner."""
    import os
    import re
    import shutil
    from unittest.mock import MagicMock

    import httpx
    import pytest
    import respx
    from typer.testing import CliRunner

    import sigantry_core.deploy.core as deploy_core_mod
    from sigantry_core.cli import app

    mp = pytest.MonkeyPatch() if not hasattr(monkeypatch, "setenv") else monkeypatch  # type: ignore[assignment]

    demo_src = REPO / "templates" / "demo"
    runner = CliRunner()

    tenant_id = "11111111-1111-1111-1111-111111111111"
    client_id = "22222222-2222-2222-2222-222222222222"
    workspace_id = "33333333-3333-3333-3333-333333333333"
    capacity_id = "44444444-4444-4444-4444-444444444444"
    preprod_ws_id = "55555555-5555-5555-5555-555555555555"
    prod_ws_id = "66666666-6666-6666-6666-666666666666"

    def _assert_azure_env() -> None:
        for cred_key in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
            assert os.environ.get(cred_key), f"TokenProvider invoked without {cred_key} in step env"

    def _fake_get_token(self: object, scope: str | None = None) -> str:
        _assert_azure_env()
        return "fake-bearer-token"

    def _fake_get_credential(self: object) -> object:
        _assert_azure_env()
        return MagicMock()

    mp.setattr("sigantry_core.auth.token_provider.TokenProvider.get_token", _fake_get_token)
    mp.setattr(
        "sigantry_core.auth.token_provider.TokenProvider.get_credential",
        _fake_get_credential,
    )
    mp.setattr(deploy_core_mod, "FabricWorkspace", MagicMock(return_value=MagicMock()))
    mp.setattr(deploy_core_mod, "publish_all_items", MagicMock())

    gha_doc = yaml.safe_load(GHA.read_text())
    ado_doc = yaml.safe_load(ADO.read_text())

    for wf_kind, steps in (
        (
            "gha",
            [
                _gha_step_by_name(gha_doc, "Deploy"),
                _gha_step_by_name(gha_doc, "Record release"),
                _gha_step_by_name(gha_doc, "Diff (fail on drift)"),
            ],
        ),
        (
            "ado",
            [
                _ado_step_by_display_name(ado_doc, "Deploy"),
                _ado_step_by_display_name(ado_doc, "Record release"),
                _ado_step_by_display_name(ado_doc, "Diff (fail on drift)"),
            ],
        ),
    ):
        wf_dir = tmp_path / wf_kind
        work_dir = wf_dir / "demo"
        shutil.copytree(demo_src, work_dir)
        mp.chdir(work_dir)

        env_map = {
            "AZURE_TENANT_ID": tenant_id,
            "AZURE_CLIENT_ID": client_id,
            "AZURE_CLIENT_SECRET": "fake-secret",
            "SIGANTRY_DEMO_TENANT_ID": tenant_id,
            "SIGANTRY_DEMO_CLIENT_ID": client_id,
            "SIGANTRY_DEMO_FABRIC_TOKEN": "fake-secret",
            "SIGANTRY_DEMO_WORKSPACE_ID": workspace_id,
            "SIGANTRY_DEMO_CAPACITY_ID": capacity_id,
            "SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD": preprod_ws_id,
            "SIGANTRY_FABRIC_WORKSPACE_ID_PROD": prod_ws_id,
            "SIGANTRY_DEMO_WORK_ITEM_ID": "1",
            "SIGANTRY_DEMO_RELEASE_ID": f"deadbeef-{wf_kind}",
            "GITHUB_TOKEN": "ghp_fake_token_for_test",
            "GITHUB_ACTOR": "demo-operator",
            "GITHUB_REPOSITORY_OWNER": "demo-org",
            "GITHUB_REPOSITORY": "demo-org/demo-repo",
            "RUNNER_TEMP": str(wf_dir / "runner-temp"),
            "Build.SourceVersion": f"deadbeef-{wf_kind}",
            "Build.RequestedForEmail": "demo-operator@example.com",
            "System.CollectionUri": "https://dev.azure.com/demo-org/",
            "System.TeamProject": "demo-proj",
            "Agent.TempDirectory": str(wf_dir / "agent-temp"),
        }
        for k, v in env_map.items():
            if "." not in k:
                mp.setenv(k, v)

        with respx.mock(assert_all_called=False) as router:
            # GitHub issue comment endpoint for --provider github
            router.post("https://api.github.com/repos/demo-org/demo-repo/issues/1/comments").mock(
                return_value=httpx.Response(
                    201,
                    json={"id": 101, "html_url": "https://github.com/demo-org/demo-repo/issues/1"},
                )
            )
            # ADO work-item comment endpoint for --provider ado
            router.post(
                "https://dev.azure.com/demo-org/demo-proj/_apis/wit/workItems/1/comments"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"id": 202, "text": "linked"},
                )
            )
            # Fabric workspace + folders + items endpoints for sigantry diff --fail-on-drift
            router.get(f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}").mock(
                return_value=httpx.Response(
                    200,
                    json={"id": workspace_id, "displayName": "demo-sigantry"},
                )
            )
            router.get(
                f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/folders"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"value": []},
                )
            )
            router.get(f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/items").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "value": [
                            {
                                "id": "00000000-0000-0000-0000-00000000d002",
                                "displayName": "LoadOrders",
                                "type": "Notebook",
                                "folderId": None,
                                "workspaceId": workspace_id,
                            },
                            {
                                "id": "00000000-0000-0000-0000-00000000d003",
                                "displayName": "RefreshOrdersDaily",
                                "type": "DataPipeline",
                                "folderId": None,
                                "workspaceId": workspace_id,
                            },
                            {
                                "id": "00000000-0000-0000-0000-00000000d004",
                                "displayName": "OrdersAnalytics",
                                "type": "SemanticModel",
                                "folderId": None,
                                "workspaceId": workspace_id,
                            },
                        ]
                    },
                )
            )

            executed_subcommands: list[str] = []
            for step in steps:
                # Clear per-step credential env vars first, then resolve only what this step's `env:` declares.
                for cred_var in (
                    "AZURE_TENANT_ID",
                    "AZURE_CLIENT_ID",
                    "AZURE_CLIENT_SECRET",
                    "GITHUB_TOKEN",
                ):
                    mp.delenv(cred_var, raising=False)

                step_env = step.get("env", {})
                step_env_map = dict(env_map)
                for env_k, env_expr in step_env.items():
                    expr_str = str(env_expr)
                    m_gha_sec = re.search(r"secrets\.([A-Za-z0-9_]+)", expr_str)
                    m_gha_sha = re.search(r"github\.sha", expr_str)
                    m_ado_var = re.search(r"\$\(([^)]+)\)", expr_str)
                    if m_gha_sec:
                        resolved_val = env_map[m_gha_sec.group(1)]
                    elif m_gha_sha:
                        resolved_val = f"deadbeef-{wf_kind}"
                    elif m_ado_var:
                        resolved_val = env_map[m_ado_var.group(1)]
                    else:
                        resolved_val = expr_str
                    mp.setenv(env_k, resolved_val)
                    step_env_map[env_k] = resolved_val

                script = step.get("run") or step.get("script") or ""
                for argv in _extract_cli_commands(script, step_env_map):
                    res = runner.invoke(app, argv)
                    assert res.exit_code == 0, (
                        f"{wf_kind} command {argv!r} failed (exit {res.exit_code}):\n{res.output}"
                    )
                    executed_subcommands.append(" ".join(argv[:2]))
                    if argv[:2] == ["release", "verify"]:
                        assert "CHAIN VALID" in res.output
                        assert "1 record(s) verified" in res.output

            assert executed_subcommands == [
                "deploy run",
                "release record",
                "release verify",
                "diff --workspace-id",
            ]
