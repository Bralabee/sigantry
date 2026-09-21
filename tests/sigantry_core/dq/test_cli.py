"""Unit tests for ``sigantry dq gate`` CLI (Plan 08-02 PROD-06)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.protocols import GateResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_dq_gate_cli_help(runner: CliRunner) -> None:
    """``dq gate --help`` exits 0 and lists the Plan 08-02 options."""
    r = runner.invoke(app, ["dq", "gate", "--help"])
    assert r.exit_code == 0, r.output
    for flag in ("--suite", "--dataset", "--gate"):
        assert flag in r.output, f"{flag} missing from dq gate --help"


def test_dq_gate_cli_happy_path(runner: CliRunner, monkeypatch) -> None:
    """A successful GateResult exits 0 and prints a JSON payload."""
    monkeypatch.setattr(
        "sigantry_core.dq.cli.run_gate",
        lambda *a, **kw: GateResult(
            suite="s", success=True, violations=0, evaluated=5, run_id="run-1"
        ),
    )
    r = runner.invoke(app, ["dq", "gate", "--suite", "s", "--dataset", "d"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["suite"] == "s"
    assert payload["success"] is True
    assert payload["evaluated"] == 5
    assert payload["dataset"] == "d"


def test_dq_gate_cli_violation_exits_one(runner: CliRunner, monkeypatch) -> None:
    """A failing GateResult exits with code 1."""
    monkeypatch.setattr(
        "sigantry_core.dq.cli.run_gate",
        lambda *a, **kw: GateResult(
            suite="s", success=False, violations=3, evaluated=5, run_id="run-2"
        ),
    )
    r = runner.invoke(app, ["dq", "gate", "--suite", "s", "--dataset", "d"])
    assert r.exit_code == 1, r.output


def test_dq_gate_cli_config_error_exits_two(runner: CliRunner, monkeypatch) -> None:
    """KeyError / ValueError from the dispatcher map to exit code 2."""

    def _raise(*a, **kw):
        raise KeyError("unknown-gate")

    monkeypatch.setattr("sigantry_core.dq.cli.run_gate", _raise)
    r = runner.invoke(
        app, ["dq", "gate", "--suite", "s", "--dataset", "d", "--gate", "unknown-gate"]
    )
    assert r.exit_code == 2
    assert "config error" in r.output.lower()


def test_dq_gate_cli_config_error_on_value_error(runner: CliRunner, monkeypatch) -> None:
    """ValueError from the dispatcher also maps to exit code 2."""

    def _raise(*a, **kw):
        raise ValueError("no gate_name and no default")

    monkeypatch.setattr("sigantry_core.dq.cli.run_gate", _raise)
    r = runner.invoke(app, ["dq", "gate", "--suite", "s", "--dataset", "d"])
    assert r.exit_code == 2
    assert "config error" in r.output.lower()


def test_dq_gate_cli_forwards_gate_flag(runner: CliRunner, monkeypatch) -> None:
    """``--gate`` reaches the dispatcher as ``gate_name=``."""
    captured: dict = {}

    def _capture(*a, **kw):
        captured.update(kw)
        return GateResult(suite="s", success=True, violations=0, evaluated=1, run_id="r")

    monkeypatch.setattr("sigantry_core.dq.cli.run_gate", _capture)
    r = runner.invoke(app, ["dq", "gate", "--suite", "s", "--dataset", "d", "--gate", "my-gate"])
    assert r.exit_code == 0, r.output
    assert captured["gate_name"] == "my-gate"


def test_dq_gate_cli_dataset_name_flag(runner: CliRunner, monkeypatch) -> None:
    """``--dataset-name`` overrides the DataRef.name; path stays the raw dataset."""
    captured: dict = {}

    def _capture(suite, data_ref, *a, **kw):
        captured["suite"] = suite
        captured["data_ref"] = data_ref
        return GateResult(suite=suite, success=True, violations=0, evaluated=1, run_id="r")

    monkeypatch.setattr("sigantry_core.dq.cli.run_gate", _capture)
    r = runner.invoke(
        app,
        [
            "dq",
            "gate",
            "--suite",
            "s",
            "--dataset",
            "/Workspace/Bronze/orders",
            "--dataset-name",
            "orders",
        ],
    )
    assert r.exit_code == 0, r.output
    assert captured["data_ref"].name == "orders"
    assert captured["data_ref"].path == "/Workspace/Bronze/orders"
