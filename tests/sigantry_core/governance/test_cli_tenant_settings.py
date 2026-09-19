"""Unit tests for the ``fabric-dataops tenant-settings`` Typer subapp (GOV-05)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.client import FabricRestClient

_SAMPLE_SETTINGS = [
    {"settingName": "A", "enabled": True},
    {"settingName": "B", "enabled": False},
]


def _mock_fabric_from_defaults() -> MagicMock:
    c = MagicMock(spec=FabricRestClient)
    c.list_paginated.return_value = iter(_SAMPLE_SETTINGS)
    c.__enter__.return_value = c
    c.__exit__.return_value = None
    return c


def test_tenant_settings_help() -> None:
    r = CliRunner().invoke(app, ["tenant-settings", "--help"])
    assert r.exit_code == 0
    assert "export" in r.stdout


def test_tenant_settings_export_stdout() -> None:
    mock = _mock_fabric_from_defaults()
    with patch("sigantry_core.client.FabricRestClient.from_defaults", return_value=mock):
        r = CliRunner().invoke(app, ["tenant-settings", "export"])
    assert r.exit_code == 0, r.stdout
    # stdout contains the digest marker produced by the exporter.
    assert "sha256:" in r.stdout
    # capturedAt (camelCase) is the wire-shape key emitted via print_json.
    assert "capturedAt" in r.stdout


def test_tenant_settings_export_writes_file(tmp_path: Path) -> None:
    mock = _mock_fabric_from_defaults()
    out = tmp_path / "baseline.json"
    with patch("sigantry_core.client.FabricRestClient.from_defaults", return_value=mock):
        r = CliRunner().invoke(app, ["tenant-settings", "export", "--output", str(out)])
    assert r.exit_code == 0, r.stdout
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["digest"].startswith("sha256:")
    assert len(data["settings"]) == 2


def test_tenant_settings_export_threads_tenant_id(tmp_path: Path) -> None:
    mock = _mock_fabric_from_defaults()
    out = tmp_path / "baseline.json"
    with patch(
        "sigantry_core.client.FabricRestClient.from_defaults", return_value=mock
    ) as from_defaults:
        r = CliRunner().invoke(
            app,
            [
                "tenant-settings",
                "export",
                "--output",
                str(out),
                "--tenant-id",
                "tenant-xyz",
            ],
        )
    assert r.exit_code == 0, r.stdout
    # --tenant-id must thread through to FabricRestClient.from_defaults
    from_defaults.assert_called_once_with(tenant_id="tenant-xyz")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tenantId"] == "tenant-xyz"
