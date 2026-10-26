"""Regression tests for the 7 live-Fabric verification findings and PyPI certifi floor."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
import yaml
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from typer.testing import CliRunner

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.auth.token_provider import TokenProvider, reset_token_provider
from sigantry_core.capacity.core import Capacity
from sigantry_core.cli import app
from sigantry_core.client import FabricRestClient
from sigantry_core.deploy.core import deploy_workspace
from sigantry_core.pr_bot.breaking import analyze_tmdl_breaking_changes
from sigantry_core.preflight.engine import PreflightEngine
from sigantry_core.preflight.models import ProbeStatus
from sigantry_core.preflight.probes import CapacityStateProbe, SchemaSyntaxProbe
from sigantry_core.sync.apply import apply_sync

REPO_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"
WS_ID = "11111111-2222-4333-8444-555555555555"
CAP_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _fake_jwt(**claims: Any) -> str:
    def _b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}.sig"


# ---------------------------------------------------------------------------
# Finding 1: CD template self-approval check runs in the approval job BEFORE deploy
# ---------------------------------------------------------------------------


def test_finding_1_gha_cd_approval_job_rejects_self_approval_before_deploy() -> None:
    cd_path = REPO_ROOT / ".github" / "workflows" / "sigantry-cd.yml"
    doc = yaml.safe_load(cd_path.read_text(encoding="utf-8"))
    approval_job = doc["jobs"]["approval"]
    assert approval_job.get("permissions", {}).get("actions") == "read"

    steps = approval_job.get("steps", [])
    approver_steps = [s for s in steps if s.get("id") == "approver"]
    assert len(approver_steps) == 1, "approval job must resolve and validate approver before deploy"
    script = approver_steps[0]["run"]
    assert '[ "$SIGANTRY_APPROVER_INPUT" = "$SIGANTRY_ACTOR" ]' in script
    assert "cannot self-approve" in script

    if sys.platform != "win32":
        # Execute the approval job's approver script with actor == approver_input: must exit non-zero
        proc = subprocess.run(
            ["bash", "-e", "-c", script],
            env={
                "PATH": "/usr/bin:/bin",
                "SIGANTRY_ACTOR": "alice",
                "SIGANTRY_APPROVER_INPUT": "alice",
                "SIGANTRY_GATE_ENVIRONMENT": "",
                "SIGANTRY_REPOSITORY": "org/repo",
                "SIGANTRY_RUN_ID": "123",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "cannot self-approve" in (proc.stdout + proc.stderr)


def test_finding_1_ado_cd_approval_stage_rejects_self_approval_before_deploy() -> None:
    ado_path = REPO_ROOT / "templates" / "stages" / "sigantry-cd.yml"
    text = ado_path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    stages = {s["stage"]: s for s in doc["stages"]}
    approval_stage = stages["approval"]
    gate_job = approval_stage["jobs"][0]
    steps = gate_job["strategy"]["runOnce"]["deploy"]["steps"]
    script = steps[0]["script"]
    env_map = steps[0].get("env", {})
    assert "SIGANTRY_APPROVER" in env_map
    assert "SIGANTRY_REQUESTED_FOR_EMAIL" in env_map
    assert 'if [ "$SIGANTRY_APPROVER" = "$SIGANTRY_REQUESTED_FOR_EMAIL" ]' in script
    assert "cannot self-approve" in script

    if sys.platform != "win32":
        proc = subprocess.run(
            ["bash", "-e", "-c", script],
            env={
                "PATH": "/usr/bin:/bin",
                "SIGANTRY_WORK_ITEMS": "AB#123",
                "SIGANTRY_APPROVER": "alice@example.com",
                "SIGANTRY_REQUESTED_FOR_EMAIL": "alice@example.com",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "cannot self-approve" in (proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# Finding 2: Shipped demo SemanticModel has a partition & preflight checks for it
# ---------------------------------------------------------------------------


def test_finding_2_demo_orders_tmdl_declares_a_partition() -> None:
    orders_tmdl = (
        REPO_ROOT
        / "templates"
        / "demo"
        / "fabric_items"
        / "OrdersAnalytics.SemanticModel"
        / "definition"
        / "tables"
        / "Orders.tmdl"
    )
    text = orders_tmdl.read_text(encoding="utf-8")
    assert "partition " in text
    assert "mode:" in text
    assert "source =" in text


def test_finding_2_schema_syntax_probe_rejects_table_tmdl_without_partition(
    tmp_path: Path,
) -> None:
    sm_dir = tmp_path / "fabric_items" / "Bad.SemanticModel"
    tables_dir = sm_dir / "definition" / "tables"
    tables_dir.mkdir(parents=True)
    (sm_dir / ".platform").write_text(
        json.dumps({"metadata": {"type": "SemanticModel", "displayName": "Bad"}}),
        encoding="utf-8",
    )
    (sm_dir / "definition.pbism").write_text(json.dumps({"version": "4.0"}), encoding="utf-8")
    (tables_dir / "Orders.tmdl").write_text(
        "table Orders\n\tcolumn OrderId\n\t\tdataType: int64\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "sync.yml"
    manifest.write_text(
        'schema_version: "1.0"\n'
        "items:\n"
        "  - local_path: fabric_items/Bad.SemanticModel\n"
        "    type: SemanticModel\n"
        "    display_name: Bad\n",
        encoding="utf-8",
    )
    res = SchemaSyntaxProbe().run(manifest_path=manifest, environment="DEV")
    assert res.status == ProbeStatus.FAIL
    assert "partition" in res.message.lower()


# ---------------------------------------------------------------------------
# Finding 3: Destructive sync delete records honour --audit-dir, log item/workspace names, and record principal
# ---------------------------------------------------------------------------


def test_finding_3_destructive_sync_deletes_respect_audit_dir_include_names_and_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_token_provider()
    home_dir = tmp_path / "fake_home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    custom_audit = tmp_path / "custom_audit"
    custom_audit.mkdir()

    nb_file = tmp_path / "nb.ipynb"
    nb_file.write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    sync_yml = tmp_path / "sync.yml"
    sync_yml.write_text(
        'schema_version: "1.0"\n'
        "items:\n"
        f"  - local_path: '{nb_file}'\n"
        "    type: Notebook\n"
        "    target_folder: '/keep'\n"
        "    display_name: 'KeptNb'\n",
        encoding="utf-8",
    )

    jwt_tok = _fake_jwt(tid=TENANT_A, upn="operator@contoso.com", appid="app-123")
    cred = MagicMock()
    cred.__class__.__name__ = "AzureCliCredential"
    cred.get_token.return_value = AccessToken(jwt_tok, int(time.time()) + 3600)
    tp = TokenProvider(credential=cred, tenant_id=TENANT_A)
    assert tp.get_token(FABRIC_SCOPE) == jwt_tok
    assert (
        tp.last_credential_class("probe") == "_TenantPinnedCredential"
        or tp.last_credential_class("probe") == "AzureCliCredential"
    )

    orphan_item_id = "99999999-8888-4777-8666-555555555555"
    orphan_folder_id = "77777777-6666-4555-8444-333333333333"

    with respx.mock(base_url=FABRIC_AUDIENCE, assert_all_called=False) as router:
        router.get(f"/v1/workspaces/{WS_ID}").mock(
            return_value=httpx.Response(
                200, json={"id": WS_ID, "displayName": "ws-live", "gitConnection": None}
            )
        )
        router.get(f"/v1/workspaces/{WS_ID}/folders").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": orphan_folder_id,
                            "displayName": "old-folder",
                            "parentFolderId": None,
                            "workspaceId": WS_ID,
                        }
                    ]
                },
            )
        )
        router.get(f"/v1/workspaces/{WS_ID}/items").mock(
            return_value=httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": orphan_item_id,
                            "displayName": "OrphanNb",
                            "type": "Notebook",
                            "folderId": orphan_folder_id,
                            "workspaceId": WS_ID,
                        }
                    ]
                },
            )
        )
        router.post(f"/v1/workspaces/{WS_ID}/folders").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "keep-folder-id",
                    "displayName": "keep",
                    "parentFolderId": None,
                    "workspaceId": WS_ID,
                },
            )
        )
        router.delete(f"/v1/workspaces/{WS_ID}/items/{orphan_item_id}").mock(
            return_value=httpx.Response(200, json={})
        )
        router.delete(f"/v1/workspaces/{WS_ID}/folders/{orphan_folder_id}").mock(
            return_value=httpx.Response(200, json={})
        )

        with FabricRestClient(token_provider=tp) as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=WS_ID,
                client=client,
                unpublish_orphans=True,
                force=True,
                audit_dir=custom_audit,
            )

    # Must NOT write to ~/.sigantry/audit
    assert not (home_dir / ".sigantry" / "audit" / "destructive_ops.jsonl").exists()
    ops_file = custom_audit / "destructive_ops.jsonl"
    assert ops_file.is_file()
    records = [json.loads(line) for line in ops_file.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2

    item_rec = next(r for r in records if r["resource_kind"] == "item" and r["action"] == "delete")
    folder_rec = next(
        r for r in records if r["resource_kind"] == "folder" and r["action"] == "delete"
    )

    assert WS_ID in item_rec["resource_id"]
    assert "OrphanNb" in item_rec["resource_id"]
    assert orphan_item_id in item_rec["resource_id"]
    assert item_rec["principal"] == "operator@contoso.com"

    assert WS_ID in folder_rec["resource_id"]
    assert "old-folder" in folder_rec["resource_id"]
    assert orphan_folder_id in folder_rec["resource_id"]
    assert folder_rec["principal"] == "operator@contoso.com"


# ---------------------------------------------------------------------------
# Finding 4: sync apply writes only actually changed items to deploys.jsonl
# ---------------------------------------------------------------------------


def test_finding_4_sync_apply_empty_workspace_records_zero_changed_items(
    tmp_path: Path,
) -> None:
    nb_file = tmp_path / "nb.ipynb"
    nb_file.write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    sync_yml = tmp_path / "sync.yml"
    sync_yml.write_text(
        'schema_version: "1.0"\n'
        "items:\n"
        f"  - local_path: '{nb_file}'\n"
        "    type: Notebook\n"
        "    target_folder: '/raw'\n"
        "    display_name: 'Nb1'\n",
        encoding="utf-8",
    )
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    cred = MagicMock()
    cred.get_token.return_value = AccessToken(_fake_jwt(tid=TENANT_A), int(time.time()) + 3600)
    tp = TokenProvider(credential=cred)

    with respx.mock(base_url=FABRIC_AUDIENCE, assert_all_called=False) as router:
        router.get(f"/v1/workspaces/{WS_ID}").mock(
            return_value=httpx.Response(200, json={"id": WS_ID, "gitConnection": None})
        )
        router.get(f"/v1/workspaces/{WS_ID}/folders").mock(
            return_value=httpx.Response(200, json={"value": []})
        )
        router.get(f"/v1/workspaces/{WS_ID}/items").mock(
            return_value=httpx.Response(200, json={"value": []})
        )
        router.post(f"/v1/workspaces/{WS_ID}/folders").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": "f-1",
                    "displayName": "raw",
                    "parentFolderId": None,
                    "workspaceId": WS_ID,
                },
            )
        )
        with FabricRestClient(token_provider=tp) as client:
            apply_sync(
                manifest_path=sync_yml,
                workspace_id=WS_ID,
                client=client,
                audit_dir=audit_dir,
            )

    rec = json.loads((audit_dir / "deploys.jsonl").read_text(encoding="utf-8").strip())
    assert rec["fabric_items_changed"] == []


# ---------------------------------------------------------------------------
# Finding 5: Documented OIDC setup covers new GitHub repo subject format (AADSTS700213)
# ---------------------------------------------------------------------------


def test_finding_5_runbooks_document_new_github_oidc_subject_format() -> None:
    for rel in (
        "docs/runbooks/pipeline-orchestration/deploy-with-tests.md",
        "docs/runbooks/drift-detection/scheduled-drift.md",
        "docs/USER-GUIDE.md",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "AADSTS700213" in text, f"{rel} missing AADSTS700213 note"
        assert "@" in text and "repo:" in text, f"{rel} missing Owner@<id>/repo@<id> subject format"


# ---------------------------------------------------------------------------
# Finding 6a: itemsPublished count respects --item-types (item_type_in_scope)
# ---------------------------------------------------------------------------


def test_finding_6a_items_published_counts_only_items_in_scope(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "parameters.yml").write_text("find_replace: []\n", encoding="utf-8")

    cred = MagicMock()
    cred.get_token.return_value = AccessToken(_fake_jwt(tid=TENANT_A), int(time.time()) + 3600)
    tp = TokenProvider(credential=cred)

    class _FakeWS:
        def __init__(self, **kwargs: Any) -> None:
            self.item_type_in_scope = kwargs.get("item_type_in_scope", [])
            self.repository_items = {
                "Notebook": {"LoadOrders": object()},
                "SemanticModel": {"OrdersAnalytics": object()},
                "DataPipeline": {"DailyRefresh": object()},
            }

    with (
        patch("sigantry_core.deploy.core.FabricWorkspace", _FakeWS),
        patch("sigantry_core.deploy.core.publish_all_items", return_value=None),
    ):
        res = deploy_workspace(
            workspace_id=WS_ID,
            environment="DEV",
            repository_directory=str(src),
            item_type_in_scope=["SemanticModel"],
            token_provider=tp,
        )
    assert res.items_published == 1


# ---------------------------------------------------------------------------
# Finding 6b: Tenant mismatch excludes BrokerCredential and exits cleanly without azure-identity-broker traceback
# ---------------------------------------------------------------------------


def test_finding_6b_tenant_mismatch_from_dac_exits_cleanly_without_broker_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_token_provider()
    monkeypatch.setenv("AZURE_TENANT_ID", TENANT_B)

    class _DacFailingOnWrongTenant:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs.get("exclude_broker_credential") is True

        def get_token(self, *scopes: str, tenant_id: str | None = None, **_: Any) -> AccessToken:
            if tenant_id and tenant_id.lower() != TENANT_B.lower():
                raise ClientAuthenticationError(
                    "DefaultAzureCredential failed to retrieve a token from the included credentials.\n"
                    "BrokerCredential: InteractiveBrowserBrokerCredential unavailable. "
                    "The 'azure-identity-broker' package is required to use brokered authentication."
                )
            return AccessToken(_fake_jwt(tid=TENANT_B), int(time.time()) + 3600)

    monkeypatch.setattr(
        "sigantry_core.auth.token_provider.DefaultAzureCredential",
        _DacFailingOnWrongTenant,
    )
    res = CliRunner().invoke(app, ["workspace", "list", "--tenant-id", TENANT_A])
    assert res.exit_code == 1
    assert "Traceback" not in res.output
    assert "azure-identity-broker" not in res.output
    assert TENANT_A in res.output and TENANT_B in res.output


# ---------------------------------------------------------------------------
# Finding 6c: Preflight defaults to DEV
# ---------------------------------------------------------------------------


def test_finding_6c_preflight_defaults_to_uppercase_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SIGANTRY_DEMO_WORKSPACE_ID", WS_ID)
    demo_sync = REPO_ROOT / "templates" / "demo" / "sync.yml"
    demo_params = REPO_ROOT / "templates" / "demo" / "parameters.yml"
    report = PreflightEngine(probes=[SchemaSyntaxProbe()]).run(
        manifest_path=demo_sync,
        params_path=demo_params,
    )
    assert report.environment == "DEV"
    assert report.passed is True, report.results[0].message


# ---------------------------------------------------------------------------
# Finding 6d: CapacityStateProbe parses string sku and rejects item types the SKU cannot host
# ---------------------------------------------------------------------------


def test_finding_6d_capacity_probe_fails_when_ppu_sku_cannot_host_manifest_item_types() -> None:
    cap = Capacity.from_api(
        {
            "id": CAP_ID,
            "displayName": "PPU-trial",
            "sku": "PP3",
            "region": "UK South",
            "state": "Active",
        }
    )
    assert cap.sku_name == "PP3"

    class _Client:
        def send(self, method: str, path: str, **kwargs: Any) -> Any:
            return MagicMock(
                json_body={
                    "id": WS_ID,
                    "displayName": "demo-ws",
                    "type": "Workspace",
                    "capacityId": CAP_ID,
                }
            )

        def list_paginated(self, path: str, **kwargs: Any) -> Any:
            yield {
                "id": CAP_ID,
                "displayName": "PPU-trial",
                "sku": "PP3",
                "region": "UK South",
                "state": "Active",
            }

    demo_sync = REPO_ROOT / "templates" / "demo" / "sync.yml"
    res = CapacityStateProbe().run(
        manifest_path=demo_sync,
        environment="DEV",
        client=_Client(),
        workspace_id=WS_ID,
    )
    assert res.status == ProbeStatus.FAIL
    assert "PP3" in res.message
    assert "Notebook" in res.message or "DataPipeline" in res.message


# ---------------------------------------------------------------------------
# Finding 6e: PR bot flags column type changes (e.g. decimal -> string) as breaking
# ---------------------------------------------------------------------------


def test_finding_6e_pr_bot_flags_column_type_change_as_breaking(tmp_path: Path) -> None:
    from sigantry_core.pr_bot.tmdl_diff import diff_tmdl

    base = tmp_path / "base"
    head = tmp_path / "head"
    base.mkdir()
    head.mkdir()
    (base / "Orders.tmdl").write_text(
        "table Orders\n\tcolumn Amount\n\t\tdataType: decimal\n",
        encoding="utf-8",
    )
    (head / "Orders.tmdl").write_text(
        "table Orders\n\tcolumn Amount\n\t\tdataType: string\n",
        encoding="utf-8",
    )
    diff = diff_tmdl(base, head)
    findings = analyze_tmdl_breaking_changes(diff)
    assert len(findings) == 1
    assert findings[0].kind == "column_type_changed"
    assert findings[0].severity == "HIGH"
    assert "Orders" in findings[0].target and "Amount" in findings[0].target


# ---------------------------------------------------------------------------
# Finding 6f: CI workflows preserve or chain multiple ledger records before release verify
# ---------------------------------------------------------------------------


def test_finding_6f_ci_workflows_chain_multiple_records_before_verify() -> None:
    for rel in (
        ".github/workflows/sigantry-cd.yml",
        "templates/stages/sigantry-cd.yml",
        "templates/demo/.github/workflows/sigantry-demo-ci.yml",
        "templates/demo/.azuredevops/sigantry-demo-ci.yml",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "deploys.jsonl" in text, (
            f"{rel} does not seed/restore prior deploys.jsonl before release record"
        )


# ---------------------------------------------------------------------------
# PyPI Security Notice: certifi >= 2026.10.19 in pyproject.toml
# ---------------------------------------------------------------------------


def test_pypi_security_notice_certifi_floor_in_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]
    assert any(d.startswith("certifi>=2026.10.19") for d in deps), deps
