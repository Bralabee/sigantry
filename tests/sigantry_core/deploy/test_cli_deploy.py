"""Unit tests for the ``fabric-dataops deploy`` Typer subcommand."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.deploy.core import DeployResult
from sigantry_core.governance.audit import DestructiveOpError


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_deploy_help_lists_all_flags(runner: CliRunner) -> None:
    """`fabric-dataops deploy --help` must surface every documented flag."""
    r = runner.invoke(app, ["deploy", "--help"])
    assert r.exit_code == 0, r.output
    # The single command is `run`; help enumerates subcommands.
    r2 = runner.invoke(app, ["deploy", "run", "--help"])
    assert r2.exit_code == 0, r2.output
    for flag in (
        "--source",
        "--workspace-id",
        "--environment",
        "--params",
        "--item-types",
        "--unpublish-orphans",
        "--unpublish-force",
        "--unpublish-runbook-id",
        "--item-name-exclude-regex",
        "--folder-path-exclude-regex",
        "--folder-path-to-include",
        "--items-to-include",
        "--shortcut-exclude-regex",
    ):
        assert flag in r2.output, f"{flag} missing from deploy run --help"


def test_root_help_lists_six_subapps(runner: CliRunner) -> None:
    """After Plan 04-01 the root CLI must register six subapps."""
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.output
    for name in (
        "workspace",
        "capacity",
        "label-sync",
        "rbac-audit",
        "tenant-settings",
        "deploy",
    ):
        assert name in r.output, f"{name} subapp missing from root --help"


def test_deploy_run_happy_path_calls_deploy_workspace(
    runner: CliRunner, monkeypatch, tmp_path: Path
) -> None:
    """Happy-path invocation forwards CLI args to deploy_workspace."""
    fake = MagicMock(
        name="deploy_workspace",
        return_value=DeployResult(
            workspace_id="ws-1",
            environment="DEV",
            items_published=3,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path="/tmp/g.dot",
        ),
    )
    monkeypatch.setattr("sigantry_core.deploy.cli.deploy_workspace", fake)
    # Stub TokenProvider.from_defaults so the CLI doesn't hit Azure.
    monkeypatch.setattr(
        "sigantry_core.auth.TokenProvider.from_defaults",
        classmethod(lambda cls, **kw: MagicMock(name="tp")),
    )

    r = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            str(tmp_path),
            "--workspace-id",
            "ws-1",
            "--environment",
            "DEV",
            "--item-types",
            "Lakehouse,Notebook",
        ],
    )
    assert r.exit_code == 0, r.output
    fake.assert_called_once()
    kwargs = fake.call_args.kwargs
    assert kwargs["workspace_id"] == "ws-1"
    assert kwargs["environment"] == "DEV"
    assert kwargs["repository_directory"] == str(tmp_path)
    assert kwargs["item_type_in_scope"] == ["Lakehouse", "Notebook"]
    assert kwargs["unpublish_orphans"] is False
    assert kwargs["unpublish_force"] is False


def test_deploy_run_item_types_parsed_into_list(
    runner: CliRunner, monkeypatch, tmp_path: Path
) -> None:
    """--item-types comma-separated string must become a list[str]."""
    captured: dict[str, object] = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return DeployResult(
            workspace_id="w",
            environment="DEV",
            items_published=0,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )

    monkeypatch.setattr("sigantry_core.deploy.cli.deploy_workspace", _capture)
    monkeypatch.setattr(
        "sigantry_core.auth.TokenProvider.from_defaults",
        classmethod(lambda cls, **kw: MagicMock(name="tp")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            str(tmp_path),
            "--workspace-id",
            "w",
            "--environment",
            "DEV",
            "--item-types",
            "Lakehouse,Environment,Notebook,DataPipeline",
        ],
    )
    assert r.exit_code == 0, r.output
    assert captured["item_type_in_scope"] == [
        "Lakehouse",
        "Environment",
        "Notebook",
        "DataPipeline",
    ]


def test_deploy_run_forwards_filter_flags(runner: CliRunner, monkeypatch, tmp_path: Path) -> None:
    """Every new filter flag reaches deploy_workspace with its raw value;
    repeatable list flags (--folder-path-to-include, --items-to-include)
    accumulate into a list; absent flags pass as None (not empty list)."""
    captured: dict[str, object] = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return DeployResult(
            workspace_id="w",
            environment="DEV",
            items_published=0,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )

    monkeypatch.setattr("sigantry_core.deploy.cli.deploy_workspace", _capture)
    monkeypatch.setattr(
        "sigantry_core.auth.TokenProvider.from_defaults",
        classmethod(lambda cls, **kw: MagicMock(name="tp")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            str(tmp_path),
            "--workspace-id",
            "w",
            "--environment",
            "DEV",
            "--item-types",
            "Lakehouse",
            "--item-name-exclude-regex",
            r"^_wip_",
            "--folder-path-exclude-regex",
            r"^archive/",
            "--folder-path-to-include",
            "bronze",
            "--folder-path-to-include",
            "silver",
            "--items-to-include",
            "nb_one",
            "--shortcut-exclude-regex",
            r"^temp_",
        ],
    )
    assert r.exit_code == 0, r.output
    assert captured["item_name_exclude_regex"] == r"^_wip_"
    assert captured["folder_path_exclude_regex"] == r"^archive/"
    assert captured["folder_path_to_include"] == ["bronze", "silver"]
    assert captured["items_to_include"] == ["nb_one"]
    assert captured["shortcut_exclude_regex"] == r"^temp_"


def test_deploy_run_filter_flags_default_to_none(
    runner: CliRunner, monkeypatch, tmp_path: Path
) -> None:
    """Omitting all filter flags must pass None (not empty list) for the
    repeatable ones — fabric-cicd treats [] as "allow nothing" vs None as
    "no allow-list"."""
    captured: dict[str, object] = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return DeployResult(
            workspace_id="w",
            environment="DEV",
            items_published=0,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )

    monkeypatch.setattr("sigantry_core.deploy.cli.deploy_workspace", _capture)
    monkeypatch.setattr(
        "sigantry_core.auth.TokenProvider.from_defaults",
        classmethod(lambda cls, **kw: MagicMock(name="tp")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            str(tmp_path),
            "--workspace-id",
            "w",
            "--environment",
            "DEV",
            "--item-types",
            "Lakehouse",
        ],
    )
    assert r.exit_code == 0, r.output
    assert captured["item_name_exclude_regex"] is None
    assert captured["folder_path_exclude_regex"] is None
    assert captured["folder_path_to_include"] is None
    assert captured["items_to_include"] is None
    assert captured["shortcut_exclude_regex"] is None


def test_deploy_run_unpublish_orphans_without_force_exits_nonzero(
    runner: CliRunner, monkeypatch, tmp_path: Path
) -> None:
    """--unpublish-orphans without --unpublish-force surfaces DestructiveOpError
    from deploy_workspace and the CLI exits non-zero (Pitfall 4B)."""

    def _raise(**kwargs):
        raise DestructiveOpError("fabric_workspace.unpublish_orphans requires force=True")

    monkeypatch.setattr("sigantry_core.deploy.cli.deploy_workspace", _raise)
    monkeypatch.setattr(
        "sigantry_core.auth.TokenProvider.from_defaults",
        classmethod(lambda cls, **kw: MagicMock(name="tp")),
    )
    r = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            str(tmp_path),
            "--workspace-id",
            "ws",
            "--environment",
            "DEV",
            "--item-types",
            "Lakehouse",
            "--unpublish-orphans",
        ],
    )
    assert r.exit_code != 0
    assert "deploy failed" in r.output
