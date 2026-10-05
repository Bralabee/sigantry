"""Unit tests for sigantry_core.workspace.bootstrap (BOOTSTRAP-XX, Phase 13.5).

Coverage targets:

- JSONSchema validation: required keys, additionalProperties=false, oneOf
  (blueprint XOR list), if/then conditional on git.enabled.
- Cross-field constraints (FEATURE stage requires feature_branch).
- BootstrapConfig.stage_marked_name composition.
- Probe-before-act idempotency: re-runs report ``already-converged`` for
  every step that found existing state.
- End-to-end mocked: each of the 5 steps is exercised in the right order.
- Dry-run: no POSTs fire and no audit-log line is appended.
- Existing-layout warning: a blueprint that adds top-level folders beside
  a workspace's other top-level folders warns (library, stderr, JSON
  report) and still creates; every other case stays silent, and a warning
  sink that raises never stops the run.
- CLI: schema error -> exit 2; runtime error -> exit 1.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app
from sigantry_core.workspace.bootstrap import (
    BootstrapConfig,
    BootstrapResult,
    BootstrapValidationError,
    bootstrap_workspace,
    load_and_validate,
)
from sigantry_core.workspace.core import Workspace
from sigantry_core.workspace.folders import Folder

runner = CliRunner()

_VALID_CAPACITY = "00000000-0000-4000-8000-000000000001"
_VALID_WORKSPACE = "00000000-0000-4000-8000-000000000002"


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "workspace.yml"
    p.write_text(body, encoding="utf-8")
    return p


def _minimal_yaml(name: str = "test-ws") -> str:
    return f"""
schema_version: "1.0"
workspace:
  name: "{name}"
  capacity_id: "{_VALID_CAPACITY}"
folders:
  blueprint: minimal_starter
"""


def _make_workspace(name: str, **overrides: object) -> Workspace:
    defaults: dict[str, object] = {
        "id": _VALID_WORKSPACE,
        "display_name": name,
        "description": None,
        "type": "Workspace",
        "capacity_id": _VALID_CAPACITY,
        "domain_id": None,
    }
    defaults.update(overrides)
    return Workspace(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Loader + validator
# ---------------------------------------------------------------------------


def test_load_minimal_yaml(tmp_path: Path) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    assert isinstance(cfg, BootstrapConfig)
    assert cfg.workspace_name == "test-ws"
    assert cfg.capacity_id == _VALID_CAPACITY
    assert cfg.blueprint == "minimal_starter"
    assert cfg.stage == "NONE"
    assert cfg.git_enabled is False
    # minimal_starter has 8 folders.
    assert len(cfg.folder_list) == 8
    assert cfg.folder_list[0] == "00_control"


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_and_validate(tmp_path / "absent.yml")


def test_schema_rejects_missing_required(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, 'schema_version: "1.0"\n')
    with pytest.raises(BootstrapValidationError, match="workspace"):
        load_and_validate(p)


def test_schema_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    body = _minimal_yaml() + "\nunknown_top_level: oops\n"
    p = _write_yaml(tmp_path, body)
    with pytest.raises(BootstrapValidationError, match=r"unknown_top_level|additionalProperties"):
        load_and_validate(p)


def test_schema_rejects_invalid_capacity_guid(tmp_path: Path) -> None:
    body = """
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "not-a-guid"
"""
    with pytest.raises(BootstrapValidationError, match="capacity_id"):
        load_and_validate(_write_yaml(tmp_path, body))


def test_schema_rejects_unknown_stage(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
  stage: STAGING_ENV
"""
    with pytest.raises(BootstrapValidationError, match="stage"):
        load_and_validate(_write_yaml(tmp_path, body))


def test_schema_rejects_blueprint_and_list_together(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
folders:
  blueprint: minimal_starter
  list:
    - 10_intake
"""
    with pytest.raises(BootstrapValidationError, match=r"folders|oneOf"):
        load_and_validate(_write_yaml(tmp_path, body))


def test_explicit_folder_list_loads(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
folders:
  list:
    - "00_control"
    - "10_intake"
"""
    cfg = load_and_validate(_write_yaml(tmp_path, body))
    assert cfg.blueprint is None
    assert cfg.folder_list == ("00_control", "10_intake")


def test_feature_stage_requires_feature_branch(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
  stage: FEATURE
"""
    with pytest.raises(ValueError, match="feature_branch"):
        load_and_validate(_write_yaml(tmp_path, body))


def test_non_feature_stage_rejects_feature_branch(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
  stage: DEV
  feature_branch: my-feature
"""
    with pytest.raises(ValueError, match="feature_branch"):
        load_and_validate(_write_yaml(tmp_path, body))


def test_stage_marked_name_with_dev(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: my-ws
  capacity_id: "{_VALID_CAPACITY}"
  stage: DEV
  stage_marker_in_name: true
"""
    cfg = load_and_validate(_write_yaml(tmp_path, body))
    assert cfg.stage_marked_name == "[DEV] my-ws"


def test_stage_marked_name_with_feature_branch(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: my-ws
  capacity_id: "{_VALID_CAPACITY}"
  stage: FEATURE
  feature_branch: bugfix-42
  stage_marker_in_name: true
"""
    cfg = load_and_validate(_write_yaml(tmp_path, body))
    assert cfg.stage_marked_name == "[F] bugfix-42 my-ws"


def test_stage_marker_disabled_returns_bare_name(tmp_path: Path) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml("plain-ws")))
    assert cfg.stage_marked_name == "plain-ws"


def test_git_enabled_requires_all_git_fields(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
git:
  enabled: true
  organization_name: myorg
"""
    with pytest.raises(BootstrapValidationError):
        load_and_validate(_write_yaml(tmp_path, body))


def test_git_enabled_full_block_loads(tmp_path: Path) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
git:
  enabled: true
  provider: ado
  organization_name: myorg
  project_name: myproj
  repository_name: myrepo
  branch_name: main
  directory_name: fabric_items
  git_connection_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
"""
    cfg = load_and_validate(_write_yaml(tmp_path, body))
    assert cfg.git_enabled is True
    assert cfg.git_target is not None
    assert cfg.git_target["organization_name"] == "myorg"


# ---------------------------------------------------------------------------
# Probe-before-act + end-to-end mocked
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_client() -> MagicMock:
    return MagicMock(name="FabricRestClient")


def test_workspace_step_creates_when_absent(tmp_path: Path, stub_client: MagicMock) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml("fresh-ws")))
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.create_workspace",
            return_value=_make_workspace("fresh-ws"),
        ) as cw,
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("fresh-ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter([]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.create_folder",
            side_effect=lambda c, w, *, display_name, parent_folder_id: Folder(
                id=f"f-{display_name}",
                display_name=display_name,
                parent_folder_id=None,
                workspace_id=w,
            ),
        ),
    ):
        result = bootstrap_workspace(cfg, client=stub_client, operator="op", audit_dir=tmp_path)
    cw.assert_called_once()
    assert result.step_outcomes["workspace"] == "created"
    assert result.step_outcomes["capacity"] == "already-converged"
    assert result.step_outcomes["folders"] == "created"
    assert result.step_outcomes["git"] == "skipped"
    assert result.step_outcomes["initialize"] == "skipped"
    # Audit log written.
    ledger = tmp_path / "bootstraps.jsonl"
    assert ledger.is_file()
    record = json.loads(ledger.read_text("utf-8").splitlines()[0])
    assert record["workspace_id"] == _VALID_WORKSPACE
    assert len(record["folders_created"]) == 8


def test_workspace_step_no_op_when_existing(tmp_path: Path, stub_client: MagicMock) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml("existing-ws")))
    existing_folders = [
        Folder(id=f"f-{n}", display_name=n, parent_folder_id=None, workspace_id=_VALID_WORKSPACE)
        for n in cfg.folder_list
    ]
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([_make_workspace("existing-ws")]),
        ),
        patch("sigantry_core.workspace.bootstrap.create_workspace") as cw,
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("existing-ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter(existing_folders),
        ),
        patch("sigantry_core.workspace.bootstrap.create_folder") as cf,
    ):
        result = bootstrap_workspace(cfg, client=stub_client, operator="op", audit_dir=tmp_path)
    cw.assert_not_called()
    cf.assert_not_called()
    assert result.step_outcomes["workspace"] == "already-converged"
    assert result.step_outcomes["folders"] == "already-converged"


def test_capacity_step_rebinds_when_mismatched(tmp_path: Path, stub_client: MagicMock) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    other_capacity = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([_make_workspace("test-ws", capacity_id=other_capacity)]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("test-ws", capacity_id=other_capacity),
        ),
        patch("sigantry_core.workspace.bootstrap.assign_to_capacity") as assign,
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter([]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.create_folder",
            side_effect=lambda c, w, *, display_name, parent_folder_id: Folder(
                id=f"f-{display_name}",
                display_name=display_name,
                parent_folder_id=None,
                workspace_id=w,
            ),
        ),
    ):
        result = bootstrap_workspace(cfg, client=stub_client, operator="op", audit_dir=tmp_path)
    assign.assert_called_once_with(stub_client, _VALID_WORKSPACE, _VALID_CAPACITY)
    assert result.step_outcomes["capacity"] == "created"


def test_partial_folder_set_creates_only_missing(tmp_path: Path, stub_client: MagicMock) -> None:
    """Idempotency table: partial pre-existing folders -> only missing ones created."""
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    half = [
        Folder(id=f"f-{n}", display_name=n, parent_folder_id=None, workspace_id=_VALID_WORKSPACE)
        for n in cfg.folder_list[:4]
    ]
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([_make_workspace("test-ws")]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("test-ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter(half),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.create_folder",
            side_effect=lambda c, w, *, display_name, parent_folder_id: Folder(
                id=f"f-{display_name}",
                display_name=display_name,
                parent_folder_id=None,
                workspace_id=w,
            ),
        ) as cf,
    ):
        result = bootstrap_workspace(cfg, client=stub_client, operator="op", audit_dir=tmp_path)
    # 8 desired - 4 already present = 4 created.
    assert cf.call_count == 4
    assert len(result.record.folders_created) == 4
    # Order preserved: created folders are the LAST 4 of minimal_starter.
    assert result.record.folders_created == list(cfg.folder_list[4:])


def test_git_enabled_calls_connect_or_reconnect_and_initialize(
    tmp_path: Path, stub_client: MagicMock
) -> None:
    body = f"""
schema_version: "1.0"
workspace:
  name: ws
  capacity_id: "{_VALID_CAPACITY}"
git:
  enabled: true
  provider: ado
  organization_name: myorg
  project_name: myproj
  repository_name: myrepo
  branch_name: main
  directory_name: fabric_items
  git_connection_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
"""
    cfg = load_and_validate(_write_yaml(tmp_path, body))
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.create_workspace",
            return_value=_make_workspace("ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter([]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.connect_or_reconnect",
            return_value="connected",
        ) as connect,
        patch(
            "sigantry_core.workspace.bootstrap.initialize_connection",
            return_value=None,
        ) as init,
    ):
        result = bootstrap_workspace(cfg, client=stub_client, operator="op", audit_dir=tmp_path)
    connect.assert_called_once()
    init.assert_called_once()
    assert result.step_outcomes["git"] == "created"
    assert result.step_outcomes["initialize"] == "created"
    # Audit record's git_target excludes the connection-id secret.
    assert "git_connection_id" not in (result.record.git_target or {})
    assert (result.record.git_target or {})["organization_name"] == "myorg"


def test_dry_run_does_not_post_or_write_ledger(tmp_path: Path, stub_client: MagicMock) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([]),
        ),
        patch("sigantry_core.workspace.bootstrap.create_workspace") as cw,
        patch("sigantry_core.workspace.bootstrap.assign_to_capacity") as assign,
        patch("sigantry_core.workspace.bootstrap.create_folder") as cf,
    ):
        result = bootstrap_workspace(
            cfg,
            client=stub_client,
            operator="op",
            audit_dir=tmp_path,
            dry_run=True,
        )
    assert result.dry_run is True
    cw.assert_not_called()
    assign.assert_not_called()
    cf.assert_not_called()
    # No ledger written in dry-run.
    assert not (tmp_path / "bootstraps.jsonl").exists()


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


def test_cli_exit_2_on_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["workspace", "bootstrap", str(tmp_path / "missing.yml")],
    )
    assert result.exit_code == 2


def test_cli_exit_2_on_schema_error(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path, 'schema_version: "1.0"\n')
    result = runner.invoke(
        app,
        ["workspace", "bootstrap", str(p)],
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Existing-layout warning: sigantry 1.0.1 changed the blueprint folder names
# ---------------------------------------------------------------------------
#
# The folder names below are made up for these tests. The check compares a
# workspace only with the blueprint's own names, so no test needs a name
# from an earlier release.

_UNRELATED = ("pre-existing-1", "pre-existing-2", "pre-existing-3")
_NESTED = "pre-existing-1-child"
_REPORT_KEYS_1_0_0 = {
    "workspace_id",
    "workspace_name",
    "capacity_id",
    "stage",
    "blueprint",
    "folders_present",
    "step_outcomes",
    "audit_hash",
    "dry_run",
}
_BOOTSTRAP_LOGGER = "sigantry_core.workspace.bootstrap"


def _folders(names: tuple[str, ...] | list[str], *, parent: str | None = None) -> list[Folder]:
    return [
        Folder(id=f"f-{n}", display_name=n, parent_folder_id=parent, workspace_id=_VALID_WORKSPACE)
        for n in names
    ]


def _unrelated_layout() -> list[Folder]:
    """Three top-level folders the blueprint does not know, one with a child."""
    return _folders(_UNRELATED) + _folders([_NESTED], parent=f"f-{_UNRELATED[0]}")


def _new_folder(c: object, w: str, *, display_name: str, parent_folder_id: str | None) -> Folder:
    return Folder(
        id=f"f-{display_name}", display_name=display_name, parent_folder_id=None, workspace_id=w
    )


def _run_on_existing_workspace(
    tmp_path: Path,
    stub_client: MagicMock,
    cfg: BootstrapConfig,
    listed: list[Folder],
    **kwargs: object,
) -> tuple[BootstrapResult, MagicMock]:
    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([_make_workspace(cfg.workspace_name)]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace(cfg.workspace_name),
        ),
        patch("sigantry_core.workspace.bootstrap.list_folders", return_value=iter(listed)),
        patch("sigantry_core.workspace.bootstrap.create_folder", side_effect=_new_folder) as cf,
    ):
        result = bootstrap_workspace(
            cfg,
            client=stub_client,
            operator="op",
            audit_dir=tmp_path,
            **kwargs,  # type: ignore[arg-type]
        )
    return result, cf


def _bootstrap_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _BOOTSTRAP_LOGGER and r.levelno >= logging.WARNING
    ]


def test_blueprint_beside_unrelated_folders_warns_and_still_creates(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A blueprint laid out beside folders it does not know warns, never refuses."""
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)

    result, cf = _run_on_existing_workspace(tmp_path, stub_client, cfg, _unrelated_layout())

    # Warn, not refuse: every blueprint folder is still created.
    assert cf.call_count == len(cfg.folder_list) == 8
    assert result.step_outcomes["folders"] == "created"
    assert len(result.warnings) == 1
    message = result.warnings[0]
    assert "already has 3 top-level folders" in message  # the child is not counted
    assert "blueprint 'minimal_starter'" in message
    assert "bootstrap creates all 8 of the blueprint's folders at the top level" in message
    assert "sigantry 1.0.1 changed the folder names" in message
    assert f"folders.list in {cfg.path}" in message
    # Name-free: the text names no folder, existing or new.
    for name in (*_UNRELATED, _NESTED, *cfg.folder_list):
        assert name not in message
    # The default sink logs it at WARNING level.
    assert _bootstrap_warnings(caplog) == [message]
    # The audit record keeps its 1.0.0 shape.
    assert "warnings" not in result.record.model_dump()


def test_warning_reaches_on_warning_before_any_folder_is_created(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)
    events: list[str] = []

    def creating(c: object, w: str, *, display_name: str, parent_folder_id: str | None) -> Folder:
        events.append("create")
        return _new_folder(c, w, display_name=display_name, parent_folder_id=parent_folder_id)

    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([_make_workspace("test-ws")]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("test-ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter(_unrelated_layout()),
        ),
        patch("sigantry_core.workspace.bootstrap.create_folder", side_effect=creating),
    ):
        result = bootstrap_workspace(
            cfg,
            client=stub_client,
            operator="op",
            audit_dir=tmp_path,
            on_warning=lambda message: events.append(f"warn:{message}"),
        )

    assert events[0] == f"warn:{result.warnings[0]}"
    assert events[1:] == ["create"] * 8
    # A caller's sink replaces the default log line rather than adding to it.
    assert _bootstrap_warnings(caplog) == []


@pytest.mark.parametrize("dry_run", [False, True], ids=["real-run", "dry-run"])
@pytest.mark.parametrize("where", ["nested", "top-level"])
def test_one_blueprint_name_present_does_not_silence_the_warning(
    tmp_path: Path,
    stub_client: MagicMock,
    caplog: pytest.LogCaptureFixture,
    where: str,
    dry_run: bool,
) -> None:
    """A blueprint name already in the workspace, nested or at the top level,
    is not created again, but the other seven still land at the top level
    beside the workspace's other top-level folders, so bootstrap warns."""
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)
    matched = cfg.folder_list[-1]
    parent = f"f-{_UNRELATED[0]}" if where == "nested" else None
    listed = _unrelated_layout() + _folders([matched], parent=parent)

    result, cf = _run_on_existing_workspace(tmp_path, stub_client, cfg, listed, dry_run=dry_run)

    to_create = [name for name in cfg.folder_list if name != matched]
    if dry_run:
        cf.assert_not_called()
    else:
        assert [c.kwargs["display_name"] for c in cf.call_args_list] == to_create
        assert {c.kwargs["parent_folder_id"] for c in cf.call_args_list} == {None}
    assert result.record.folders_created == to_create
    assert len(result.warnings) == 1
    message = result.warnings[0]
    action = "would create" if dry_run else "creates"
    # Only the three other top-level folders count: not the blueprint's own
    # name, and not a nested folder.
    assert (
        "already has 3 top-level folders whose names are not in blueprint 'minimal_starter'"
        in message
    )
    # The count is the one the folder step creates (or would create).
    assert f"bootstrap {action} 7 of the blueprint's 8 folders at the top level" in message
    for name in (*_UNRELATED, _NESTED, *cfg.folder_list):
        assert name not in message
    assert _bootstrap_warnings(caplog) == [message]


@pytest.mark.parametrize("dry_run", [False, True], ids=["real-run", "dry-run"])
def test_no_warning_when_the_blueprint_creates_nothing(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture, dry_run: bool
) -> None:
    """A complete layout plus a hand-made top-level folder: nothing is created."""
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)
    listed = _folders(cfg.folder_list) + _folders(["hand-made"])

    result, cf = _run_on_existing_workspace(tmp_path, stub_client, cfg, listed, dry_run=dry_run)

    cf.assert_not_called()
    assert result.step_outcomes["folders"] == "already-converged"
    assert result.warnings == ()
    assert _bootstrap_warnings(caplog) == []


def test_no_warning_when_a_partial_layout_has_no_other_top_level_folders(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Finishing a half-built blueprint layout is ordinary convergence, even
    with a hand-made folder nested inside one of its folders."""
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)
    listed = _folders(cfg.folder_list[:4]) + _folders(
        ["hand-made-child"], parent=f"f-{cfg.folder_list[0]}"
    )

    result, cf = _run_on_existing_workspace(tmp_path, stub_client, cfg, listed)

    assert cf.call_count == 4
    assert result.warnings == ()
    assert _bootstrap_warnings(caplog) == []


def test_no_warning_on_a_workspace_without_folders(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)

    result, cf = _run_on_existing_workspace(tmp_path, stub_client, cfg, [])

    assert cf.call_count == 8
    assert result.warnings == ()
    assert _bootstrap_warnings(caplog) == []


def test_no_warning_for_an_explicit_folder_list(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """``folders.list`` names are the operator's own choice: nothing to warn about."""
    body = f"""
schema_version: "1.0"
workspace:
  name: test-ws
  capacity_id: "{_VALID_CAPACITY}"
folders:
  list:
    - "listed-a"
    - "listed-b"
"""
    cfg = load_and_validate(_write_yaml(tmp_path, body))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)

    result, cf = _run_on_existing_workspace(tmp_path, stub_client, cfg, _unrelated_layout())

    assert cf.call_count == 2
    assert result.warnings == ()
    assert _bootstrap_warnings(caplog) == []


def test_dry_run_reports_the_warning_and_posts_nothing(
    tmp_path: Path, stub_client: MagicMock
) -> None:
    cfg = load_and_validate(_write_yaml(tmp_path, _minimal_yaml()))
    seen: list[str] = []

    result, cf = _run_on_existing_workspace(
        tmp_path, stub_client, cfg, _unrelated_layout(), dry_run=True, on_warning=seen.append
    )

    cf.assert_not_called()
    assert result.dry_run is True
    assert len(result.warnings) == 1
    assert seen == list(result.warnings)
    assert "bootstrap would create all 8 of the blueprint's folders" in result.warnings[0]
    assert not (tmp_path / "bootstraps.jsonl").exists()


_BLUEPRINT_WITH_GIT_YAML = f"""
schema_version: "1.0"
workspace:
  name: test-ws
  capacity_id: "{_VALID_CAPACITY}"
folders:
  blueprint: minimal_starter
git:
  enabled: true
  provider: ado
  organization_name: myorg
  project_name: myproj
  repository_name: myrepo
  branch_name: main
  directory_name: fabric_items
  git_connection_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
"""


@pytest.mark.parametrize("dry_run", [False, True], ids=["real-run", "dry-run"])
def test_a_raising_warning_sink_never_stops_the_run(
    tmp_path: Path, stub_client: MagicMock, caplog: pytest.LogCaptureFixture, dry_run: bool
) -> None:
    """A sink that raises (a closed stderr, say) is logged, and bootstrap
    still runs every later step and keeps its record."""
    cfg = load_and_validate(_write_yaml(tmp_path, _BLUEPRINT_WITH_GIT_YAML))
    caplog.set_level(logging.WARNING, logger=_BOOTSTRAP_LOGGER)
    received: list[str] = []

    def broken_pipe(message: str) -> None:
        received.append(message)
        raise BrokenPipeError(32, "Broken pipe")

    with (
        patch(
            "sigantry_core.workspace.bootstrap.list_workspaces",
            return_value=iter([_make_workspace("test-ws")]),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.get_workspace",
            return_value=_make_workspace("test-ws"),
        ),
        patch(
            "sigantry_core.workspace.bootstrap.list_folders",
            return_value=iter(_unrelated_layout()),
        ),
        patch("sigantry_core.workspace.bootstrap.create_folder", side_effect=_new_folder) as cf,
        patch(
            "sigantry_core.workspace.bootstrap.connect_or_reconnect", return_value="connected"
        ) as connect,
        patch("sigantry_core.workspace.bootstrap.initialize_connection", return_value=None) as init,
    ):
        result = bootstrap_workspace(
            cfg,
            client=stub_client,
            operator="op",
            audit_dir=tmp_path,
            dry_run=dry_run,
            on_warning=broken_pipe,
        )

    assert len(result.warnings) == 1
    assert received == list(result.warnings)
    # The sink's failure is logged at WARNING with its traceback and the
    # warning it could not deliver.
    failures = [r for r in caplog.records if r.name == _BOOTSTRAP_LOGGER and r.exc_info]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].exc_info is not None
    assert failures[0].exc_info[0] is BrokenPipeError
    assert result.warnings[0] in failures[0].getMessage()
    # Every step after the folders still ran (or, in a dry run, was planned).
    assert result.step_outcomes == {
        "workspace": "already-converged",
        "capacity": "already-converged",
        "folders": "created",
        "git": "created",
        "initialize": "created",
    }
    assert result.record.verify_hash()
    ledger = tmp_path / "bootstraps.jsonl"
    if dry_run:
        cf.assert_not_called()
        connect.assert_not_called()
        init.assert_not_called()
        assert not ledger.exists()
    else:
        assert cf.call_count == 8
        connect.assert_called_once()
        init.assert_called_once()
        lines = ledger.read_text("utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["audit_hash"] == result.record.audit_hash


def test_cli_prints_the_warning_on_stderr_and_in_the_report_only_when_it_fires(
    tmp_path: Path,
) -> None:
    cfg_path = _write_yaml(tmp_path, _minimal_yaml())
    cfg = load_and_validate(cfg_path)
    client = MagicMock(name="FabricRestClient")
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=None)

    # ``Any`` rather than click's Result: typer vendors its own click, and
    # click is not a dependency of this project.
    def invoke(listed: list[Folder], *extra: str) -> tuple[Any, MagicMock]:
        with (
            patch("sigantry_core.workspace.cli._client_factory", return_value=client),
            patch(
                "sigantry_core.workspace.bootstrap.list_workspaces",
                return_value=iter([_make_workspace("test-ws")]),
            ),
            patch(
                "sigantry_core.workspace.bootstrap.get_workspace",
                return_value=_make_workspace("test-ws"),
            ),
            patch("sigantry_core.workspace.bootstrap.list_folders", return_value=iter(listed)),
            patch("sigantry_core.workspace.bootstrap.create_folder", side_effect=_new_folder) as cf,
        ):
            result = runner.invoke(
                app,
                [
                    "workspace",
                    "bootstrap",
                    str(cfg_path),
                    "--audit-dir",
                    str(tmp_path / "audit"),
                    "--operator",
                    "op",
                    *extra,
                ],
            )
        return result, cf

    # The plan (dry run) and a real run both carry the warning, on stderr and
    # in the JSON report, and the two say the same thing.
    for extra, action in ((("--dry-run",), "would create"), ((), "creates")):
        result, cf = invoke(_unrelated_layout(), *extra)
        assert result.exit_code == 0, result.output
        assert cf.call_count == (0 if extra else 8)
        report = json.loads(result.stdout)
        assert set(report) == _REPORT_KEYS_1_0_0 | {"warnings"}
        assert len(report["warnings"]) == 1
        assert f"bootstrap {action} all 8" in report["warnings"][0]
        assert result.stderr == f"sigantry: warning: {report['warnings'][0]}\n"
        for name in (*_UNRELATED, _NESTED):
            assert name not in result.stderr

    # A run that does not warn prints no warning line and no ``warnings`` key.
    result, cf = invoke(_folders(cfg.folder_list))
    assert result.exit_code == 0, result.output
    cf.assert_not_called()
    assert "sigantry: warning:" not in result.stderr
    assert set(json.loads(result.stdout)) == _REPORT_KEYS_1_0_0
