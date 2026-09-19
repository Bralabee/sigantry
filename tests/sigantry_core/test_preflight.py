"""Tests for preflight pre-deployment safety probes (ADR-0015)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.preflight.engine import PreflightEngine
from sigantry_core.preflight.models import ProbeStatus
from sigantry_core.preflight.probes import (
    CapacityStateProbe,
    DependencyGraphProbe,
    EntraScopeProbe,
    SchemaSyntaxProbe,
)


@pytest.fixture
def temp_manifest_dir(tmp_path: Path) -> Path:
    """Fixture creating a temporary directory with valid manifest and artifacts."""
    manifest = tmp_path / "sync.yml"
    manifest.write_text(
        """
schema_version: "2.0"
items:
  - name: nb_clean
    type: Notebook
    path: notebooks/nb_clean.ipynb
    depends_on: [lakehouse_bronze]
  - name: lakehouse_bronze
    type: Lakehouse
    path: lakehouses/bronze
    depends_on: []
""",
        encoding="utf-8",
    )

    # Valid .platform
    platform_dir = tmp_path / "notebooks"
    platform_dir.mkdir()
    (platform_dir / ".platform").write_text(
        '{"version": "2.0", "type": "Notebook"}', encoding="utf-8"
    )

    # Valid .ipynb
    (platform_dir / "nb_clean.ipynb").write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 2}),
        encoding="utf-8",
    )

    return tmp_path


def test_schema_syntax_probe_success(temp_manifest_dir: Path) -> None:
    probe = SchemaSyntaxProbe()
    result = probe.run(
        manifest_path=temp_manifest_dir / "sync.yml",
        environment="dev",
    )
    assert result.status == ProbeStatus.PASS
    assert "passed syntax verification" in result.message


def test_schema_syntax_probe_fails_on_corrupt_json(temp_manifest_dir: Path) -> None:
    # Corrupt the .platform file
    (temp_manifest_dir / "notebooks" / ".platform").write_text("{invalid json", encoding="utf-8")

    probe = SchemaSyntaxProbe()
    result = probe.run(
        manifest_path=temp_manifest_dir / "sync.yml",
        environment="dev",
    )
    assert result.status == ProbeStatus.FAIL
    assert "Syntax/schema errors detected" in result.message
    assert len(result.details["errors"]) >= 1


def test_dependency_graph_probe_valid_dag(temp_manifest_dir: Path) -> None:
    probe = DependencyGraphProbe()
    result = probe.run(
        manifest_path=temp_manifest_dir / "sync.yml",
        environment="dev",
    )
    assert result.status == ProbeStatus.PASS
    assert result.details["execution_order"] == ["lakehouse_bronze", "nb_clean"]


def test_dependency_graph_probe_detects_cycles(tmp_path: Path) -> None:
    manifest = tmp_path / "sync.yml"
    manifest.write_text(
        """
schema_version: "2.0"
items:
  - name: item_a
    depends_on: [item_b]
  - name: item_b
    depends_on: [item_a]
""",
        encoding="utf-8",
    )

    probe = DependencyGraphProbe()
    result = probe.run(
        manifest_path=manifest,
        environment="dev",
    )
    assert result.status == ProbeStatus.FAIL
    assert "Circular dependency cycle detected" in result.message


def test_entra_scope_probe_with_spn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "mock-client-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "mock-secret")

    probe = EntraScopeProbe()
    result = probe.run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
    )
    assert result.status == ProbeStatus.PASS
    assert "credentials detected" in result.message


def test_entra_scope_probe_offline_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_FEDERATED_TOKEN_FILE", raising=False)

    probe = EntraScopeProbe()
    result = probe.run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
    )
    assert result.status == ProbeStatus.WARN
    assert "offline simulation mode" in result.message


def test_capacity_state_probe_active_client(tmp_path: Path) -> None:
    class MockActiveClient:
        def get_capacity_state(self) -> str:
            return "Active"

    probe = CapacityStateProbe()
    result = probe.run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        client=MockActiveClient(),
    )
    assert result.status == ProbeStatus.PASS
    assert "Active" in result.message


def test_capacity_state_probe_paused_client(tmp_path: Path) -> None:
    class MockPausedClient:
        def get_capacity_state(self) -> str:
            return "Paused"

    probe = CapacityStateProbe()
    result = probe.run(
        manifest_path=tmp_path / "sync.yml",
        environment="prod",
        client=MockPausedClient(),
    )
    assert result.status == ProbeStatus.WARN
    assert "Paused" in result.message


def test_preflight_engine_strict_handling(
    temp_manifest_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Trigger a warning in Entra probe
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)

    engine = PreflightEngine()

    # Non-strict mode should pass despite warnings
    report = engine.run(
        manifest_path=temp_manifest_dir / "sync.yml",
        environment="dev",
        strict=False,
    )
    assert report.has_warnings is True
    assert report.passed is True

    # Strict mode should fail on warnings
    strict_report = engine.run(
        manifest_path=temp_manifest_dir / "sync.yml",
        environment="dev",
        strict=True,
    )
    assert strict_report.passed is False


def test_preflight_cli_execution(temp_manifest_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "mock-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "mock-secret")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["preflight", "--manifest", str(temp_manifest_dir / "sync.yml"), "--environment", "test"],
    )
    assert result.exit_code == 0
    assert "Sigantry Preflight Probe Report" in result.output
    assert "schema_syntax" in result.output
    assert "dependency_graph" in result.output


def test_preflight_cli_json_output(
    temp_manifest_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "mock-id")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "mock-secret")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["preflight", "--manifest", str(temp_manifest_dir / "sync.yml"), "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["environment"] == "dev"
    assert data["passed"] is True
    assert len(data["results"]) >= 4
