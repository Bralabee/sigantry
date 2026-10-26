"""Regression tests for Issue #70 points 1 and 2: demo walkthrough commands run on templates/demo/.

Verifies that from a copy of ``templates/demo/``:
1. With only the four ``SIGANTRY_DEMO_*`` variables from ``docs/demo/QUICKSTART.md``
   set (and ``SIGANTRY_FABRIC_*_PREPROD`` / ``_PROD`` unset), the Step 3 command
   ``sigantry config validate parameters.yml --environment DEV`` exits 0 and prints
   the ``OK -- 3 environment(s) parsed`` summary.
2. Against a workspace with no pending Git updates, ``sigantry sync apply``
   (both ``--dry-run`` and live reconcile) packs all manifest items cleanly
   without ``No packager registered for type 'Lakehouse'`` or
   ``NotebookPackager.pack: source must be an .ipynb file``.
3. ``sigantry diff --workspace-id ... --manifest sync.yml --fail-on-drift``
   reports ``no drift`` and exits 0 when the workspace holds the manifest items.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from typer.testing import CliRunner

from sigantry_core.cli import app

_REPO = Path(__file__).resolve().parents[2]
_DEMO_DIR = _REPO / "templates" / "demo"
_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
_CAPACITY_ID = "33333333-3333-3333-3333-333333333333"

_RUNNER = CliRunner()


def _copy_demo(tmp_path: Path) -> Path:
    dest = tmp_path / "sigantry-demo"
    shutil.copytree(_DEMO_DIR, dest)
    return dest


def _set_only_four_demo_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SIGANTRY_FABRIC_WORKSPACE_ID_DEV",
        "SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD",
        "SIGANTRY_FABRIC_WORKSPACE_ID_PROD",
        "SIGANTRY_FABRIC_CAPACITY_ID_DEV",
        "SIGANTRY_FABRIC_CAPACITY_ID_PREPROD",
        "SIGANTRY_FABRIC_CAPACITY_ID_PROD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SIGANTRY_DEMO_TENANT_ID", _TENANT_ID)
    monkeypatch.setenv("SIGANTRY_DEMO_WORKSPACE_ID", _WORKSPACE_ID)
    monkeypatch.setenv("SIGANTRY_DEMO_CAPACITY_ID", _CAPACITY_ID)
    monkeypatch.setenv("SIGANTRY_DEMO_FABRIC_TOKEN", "demo-placeholder-secret")


def test_quickstart_step3_config_validate_dev_succeeds_with_only_four_demo_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #70 (1): Step 3 command in QUICKSTART.md exits 0 with only the 4 SIGANTRY_DEMO_* vars."""
    demo_copy = _copy_demo(tmp_path)
    monkeypatch.chdir(demo_copy)
    _set_only_four_demo_env_vars(monkeypatch)

    quickstart = (_REPO / "docs" / "demo" / "QUICKSTART.md").read_text(encoding="utf-8")
    assert "sigantry config validate parameters.yml --environment DEV" in quickstart

    result = _RUNNER.invoke(
        app,
        ["config", "validate", "parameters.yml", "--environment", "DEV"],
    )
    assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "OK -- 3 environment(s) parsed: DEV, PREPROD, PROD" in result.stdout


@respx.mock
def test_quickstart_step4_and_step5_sync_apply_and_diff_succeed_on_demo_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #70 (2): sync apply and diff run cleanly on a copy of templates/demo/."""
    demo_copy = _copy_demo(tmp_path)
    monkeypatch.chdir(demo_copy)
    _set_only_four_demo_env_vars(monkeypatch)

    fake_tp = MagicMock()
    fake_tp.get_token.return_value = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.e30.sig"
    fake_tp.get_fabric_token.return_value = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.e30.sig"
    monkeypatch.setattr("sigantry_core.sync.apply.TokenProvider", lambda **_kw: fake_tp)
    monkeypatch.setattr("sigantry_core.client.fabric.get_token_provider", lambda **_kw: fake_tp)

    base = f"https://api.fabric.microsoft.com/v1/workspaces/{_WORKSPACE_ID}"
    respx.get(base).mock(
        return_value=httpx.Response(
            200,
            json={"id": _WORKSPACE_ID, "displayName": "sigantry-demo"},
        )
    )
    respx.get(f"{base}/folders").mock(return_value=httpx.Response(200, json={"value": []}))
    respx.get(f"{base}/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
                        "displayName": "LoadOrders",
                        "type": "Notebook",
                        "workspaceId": _WORKSPACE_ID,
                    },
                    {
                        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
                        "displayName": "RefreshOrdersDaily",
                        "type": "DataPipeline",
                        "workspaceId": _WORKSPACE_ID,
                    },
                    {
                        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
                        "displayName": "OrdersAnalytics",
                        "type": "SemanticModel",
                        "workspaceId": _WORKSPACE_ID,
                    },
                ]
            },
        )
    )

    audit_dir = tmp_path / "audit"
    dry_res = _RUNNER.invoke(
        app,
        [
            "sync",
            "apply",
            "--manifest",
            "sync.yml",
            "--workspace-id",
            _WORKSPACE_ID,
            "--tenant-id",
            _TENANT_ID,
            "--audit-dir",
            str(audit_dir),
            "--dry-run",
        ],
    )
    assert dry_res.exit_code == 0, f"stdout={dry_res.stdout}\nstderr={dry_res.stderr}"
    assert "for 3 manifest item(s)" in dry_res.stdout

    apply_res = _RUNNER.invoke(
        app,
        [
            "sync",
            "apply",
            "--manifest",
            "sync.yml",
            "--workspace-id",
            _WORKSPACE_ID,
            "--tenant-id",
            _TENANT_ID,
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert apply_res.exit_code == 0, f"stdout={apply_res.stdout}\nstderr={apply_res.stderr}"
    assert "sync apply succeeded" in apply_res.stdout
    assert "items_packaged=3" in apply_res.stdout

    diff_res = _RUNNER.invoke(
        app,
        [
            "diff",
            "--workspace-id",
            _WORKSPACE_ID,
            "--manifest",
            "sync.yml",
            "--tenant-id",
            _TENANT_ID,
            "--fail-on-drift",
        ],
    )
    assert diff_res.exit_code == 0, f"stdout={diff_res.stdout}\nstderr={diff_res.stderr}"
    assert "no drift" in diff_res.stdout
