"""Unit tests for ``fabric-dataops deploy validate`` (Plan 05-02 / ADOPIPE-05 Python surface).

The ``validate`` subcommand composes three Phase 4 primitives:
    1. ``sigantry_core.deploy.parameters.load_and_validate`` - parameters.yml schema
       + no-hardcoded-GUID + ``$ENV:`` reachability.
    2. ``sigantry_core.deploy.dependency.validate_order`` - Kahn-cycle detection +
       DOT artefact emission.
    3. ``subprocess.run(['pre-commit', 'run', 'fabric-check-logical-id',
       'fabric-check-crlf', '--all-files'])`` - CRLF + logicalId uniqueness
       backstop.

Exit codes: 0 on all green, 1 on any failure, 2 on missing input files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.deploy.dependency import DependencyCycleError
from sigantry_core.deploy.parameters import HardcodedGuidError

runner = CliRunner()


@pytest.fixture
def golden_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Minimal acyclic Fabric item tree with one Lakehouse + a parameters.yml."""
    items = tmp_path / "items"
    (items / "Bronze.Lakehouse").mkdir(parents=True)
    (items / "Bronze.Lakehouse" / ".platform").write_text(
        '{"version":"2.0","config":{"logicalId":"11111111-1111-1111-1111-111111111111"},'
        '"metadata":{"type":"Lakehouse","displayName":"Bronze"}}\n',
        encoding="utf-8",
    )
    params = tmp_path / "parameters.yml"
    params.write_text("find_replace: []\n", encoding="utf-8")
    return items, params


def test_help_flags() -> None:
    """--help lists every documented flag (smoke for flag surface)."""
    r = runner.invoke(app, ["deploy", "validate", "--help"])
    assert r.exit_code == 0, r.output
    for flag in (
        "--source",
        "--params",
        "--item-types",
        "--junit-xml",
        "--dot-output",
        "--skip-pre-commit",
    ):
        assert flag in r.output, f"missing flag {flag} in help output"


def test_exit_0_on_golden_tree(
    golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    items, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(return_value=str(items / "dep-graph.dot")),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.subprocess.run",
        MagicMock(return_value=MagicMock(returncode=0)),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "All validation checks passed" in r.output


def test_rejects_hardcoded_guid(
    golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    items, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(
            side_effect=HardcodedGuidError(
                "raw GUID 11111111-2222-3333-4444-555555555555 at "
                "find_replace.[0].replace_value.DEV"
            )
        ),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(return_value=str(items / "dep-graph.dot")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 1
    assert "FAIL" in r.output and "parameters" in r.output


def test_detects_cycle(golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    items, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(side_effect=DependencyCycleError("cycle: A -> B -> A")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 1
    assert "dependency cycle" in r.output


def test_exit_2_on_missing_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "does-not-exist.yml"
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(side_effect=FileNotFoundError(str(missing))),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(tmp_path),
            "--params",
            str(missing),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 2


def test_artefacts_emitted(
    golden_tree: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items, params = golden_tree
    junit = tmp_path / "out" / "j.xml"
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(return_value=str(items / "dep-graph.dot")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
            "--junit-xml",
            str(junit),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 0, r.output
    assert junit.exists(), f"JUnit XML not written: {junit}"
    body = junit.read_text(encoding="utf-8")
    assert "<testsuite" in body and "<testcase" in body
    assert "parameters" in body
    assert "dependency" in body
    assert "pre_commit" in body


def test_warns_on_missing_pre_commit(
    golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    items, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(return_value="/tmp/dep.dot"),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.subprocess.run",
        MagicMock(side_effect=FileNotFoundError("pre-commit")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "WARN" in r.output and "pre-commit" in r.output


def test_pre_commit_failure_counts_as_fail(
    golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    items, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(return_value="/tmp/dep.dot"),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.subprocess.run",
        MagicMock(
            side_effect=subprocess.CalledProcessError(
                returncode=1, cmd=["pre-commit"], stderr="CRLF detected", output=""
            )
        ),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
        ],
    )
    assert r.exit_code == 1
    assert "FAIL" in r.output and "pre-commit" in r.output


def test_honours_skip_pre_commit_flag(
    golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    items, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(return_value="/tmp/dep.dot"),
    )
    subproc_mock = MagicMock()
    monkeypatch.setattr("sigantry_core.deploy.cli.subprocess.run", subproc_mock)
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            str(items),
            "--params",
            str(params),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 0, r.output
    subproc_mock.assert_not_called()


def test_missing_source_exits_1(
    golden_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing source dir surfaces as FAIL (exit 1) via validate_order.

    T-5-10 regression: we never accept a missing source as "all green".
    The parameters check still loaded OK, so no exit-2 policy applies here.
    """
    _, params = golden_tree
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.load_and_validate",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "sigantry_core.deploy.cli.validate_order",
        MagicMock(side_effect=FileNotFoundError("source missing")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "validate",
            "--source",
            "/does/not/exist",
            "--params",
            str(params),
            "--skip-pre-commit",
        ],
    )
    assert r.exit_code == 1
    assert "FAIL" in r.output
