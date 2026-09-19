"""CliRunner-driven integration tests for ``sigantry sync`` (Plan 13-04 / 13-05 / D-04).

Three commands ship under the new ``sync`` Typer subapp:

* ``apply`` -- wraps :func:`sigantry_core.sync.apply.apply_sync`. Tested
  here against the manifest-validation failure path (exit 1); the
  success path is exhaustively covered in ``tests/sync/test_apply.py``.
* ``snapshot`` -- the INTROSPECT-01 CLI surface; round-trip tested by
  monkeypatching :func:`sigantry_core.sync.snapshot.snapshot_workspace`.
* ``pull`` -- wraps :func:`sigantry_core.sync.pull.pull_workspace`
  (Plan 13-05). Tested here by monkeypatching the underlying function
  and asserting the CLI surfaces flag-mapping + success rendering;
  the algorithmic surface is exhaustively covered in
  ``tests/sync/test_pull.py``.

The 14th top-level subapp registration on ``sigantry_core/cli.py`` is
verified by importing :data:`sigantry_core.cli.app` and inspecting its
registered groups.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

from typer.testing import CliRunner

from sigantry_core.cli import app as root_app
from sigantry_core.sync.cli import sync_app

runner = CliRunner()


def test_sync_help_lists_three_commands() -> None:
    """``sigantry sync --help`` lists apply / snapshot / pull (D-04)."""
    result = runner.invoke(sync_app, ["--help"])
    assert result.exit_code == 0, result.output
    for cmd in ("apply", "snapshot", "pull"):
        assert cmd in result.output, (
            f"`sigantry sync {cmd}` missing from help; got:\n{result.output}"
        )


def test_sync_pull_invokes_pull_workspace(tmp_path: Path, monkeypatch) -> None:
    """Plan 13-05 real-body test (replaces the Plan 13-04 placeholder).

    Monkeypatches :func:`sigantry_core.sync.pull.pull_workspace` to
    return a synthetic :class:`SyncPullReport`; asserts the CLI:

    * exits 0,
    * threads ``--workspace-id`` / ``--into`` through to the function,
    * splits ``--type Notebook,DataPipeline`` on commas (mirrors
      Phase 11 ``_split_csv``),
    * surfaces ``items_pulled`` + ``sync_yml`` in the success line.
    """
    from sigantry_core.sync import cli as sync_cli_mod
    from sigantry_core.sync.pull import SyncPullReport
    from sigantry_core.sync.snapshot import WorkspaceSnapshot

    captured: dict[str, object] = {}

    def fake_pull(workspace_id, *, into, item_types=None, force=False, client=None):
        captured["workspace_id"] = workspace_id
        captured["into"] = into
        captured["item_types"] = item_types
        captured["force"] = force
        # Synthetic snapshot -- pydantic v2 enforces the dict types but
        # accepts empty dicts at the schema boundary.
        snap = WorkspaceSnapshot(
            workspace_id=workspace_id,
            folders_by_id={},
            items_by_id={},
            folder_path_index={},
            item_to_folder={},
        )
        return SyncPullReport(
            workspace_id=workspace_id,
            into=Path(into),
            items_pulled=3,
            sync_yml_path=Path(into) / "sync.yml",
            snapshot=snap,
        )

    monkeypatch.setattr(sync_cli_mod, "pull_workspace", fake_pull)

    out = tmp_path / "out"
    result = runner.invoke(
        sync_app,
        [
            "pull",
            "--workspace-id",
            "ws-x",
            "--into",
            str(out),
            "--type",
            "Notebook, DataPipeline",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "items_pulled=3" in result.output
    # Rich's Console wraps long paths across newlines; assert the
    # stable ``sync_yml=`` prefix and verify the joined output also
    # carries the trailing ``sync.yml`` token.
    assert "sync_yml=" in result.output
    flat = result.output.replace("\n", "")
    assert "sync.yml" in flat

    # Flag-mapping invariants.
    assert captured["workspace_id"] == "ws-x"
    assert captured["into"] == str(out)
    assert captured["item_types"] == ["Notebook", "DataPipeline"]
    assert captured["force"] is False


def test_sync_pull_force_flag_threads_through(tmp_path: Path, monkeypatch) -> None:
    """``--force`` propagates to ``pull_workspace`` (D-21)."""
    from sigantry_core.sync import cli as sync_cli_mod
    from sigantry_core.sync.pull import SyncPullReport
    from sigantry_core.sync.snapshot import WorkspaceSnapshot

    captured: dict[str, object] = {}

    def fake_pull(workspace_id, *, into, item_types=None, force=False, client=None):
        captured["force"] = force
        snap = WorkspaceSnapshot(
            workspace_id=workspace_id,
            folders_by_id={},
            items_by_id={},
            folder_path_index={},
            item_to_folder={},
        )
        return SyncPullReport(
            workspace_id=workspace_id,
            into=Path(into),
            items_pulled=0,
            sync_yml_path=Path(into) / "sync.yml",
            snapshot=snap,
        )

    monkeypatch.setattr(sync_cli_mod, "pull_workspace", fake_pull)

    out = tmp_path / "out"
    result = runner.invoke(
        sync_app,
        [
            "pull",
            "--workspace-id",
            "ws-x",
            "--into",
            str(out),
            "--force",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["force"] is True


def test_sync_pull_target_not_empty_exits_1(tmp_path: Path, monkeypatch) -> None:
    """:class:`PullTargetNotEmptyError` -> exit 1 with a friendly message."""
    from sigantry_core.sync import cli as sync_cli_mod
    from sigantry_core.sync.errors import PullTargetNotEmptyError

    def fake_pull(*_a, **_k):
        raise PullTargetNotEmptyError(
            "synthetic target-not-empty",
            target="/tmp/synthetic",
        )

    monkeypatch.setattr(sync_cli_mod, "pull_workspace", fake_pull)

    result = runner.invoke(
        sync_app,
        ["pull", "--workspace-id", "ws-x", "--into", str(tmp_path / "out")],
    )
    assert result.exit_code == 1, result.output
    assert "refused" in result.output.lower()


def test_sync_snapshot_cli_emits_json(tmp_path: Path, monkeypatch) -> None:
    """INTROSPECT-01 CLI: ``--output`` writes a stable JSON shape.

    The on-disk JSON exposes ``workspace_id``, ``folder_path_index``,
    ``item_to_folder``, ``folders_by_id``, and ``items_by_id`` so
    downstream tooling (Plan 13-06 diff, Plan 13-05 pull) can consume
    the snapshot deterministically without instantiating the pydantic
    model.
    """
    from sigantry_core.sync import cli as sync_cli_mod
    from sigantry_core.sync.snapshot import WorkspaceSnapshot
    from sigantry_core.workspace.folders import Folder
    from sigantry_core.workspace.items import Item

    fake_folder = Folder(
        id="f1",
        display_name="raw",
        parent_folder_id=None,
        workspace_id="ws-x",
    )
    fake_item = Item(
        id="i1",
        display_name="Foo",
        type="Notebook",
        workspace_id="ws-x",
        description=None,
        sensitivity_label_id=None,
        folder_id="f1",
    )
    fake_snap = WorkspaceSnapshot(
        workspace_id="ws-x",
        folders_by_id={"f1": fake_folder},
        items_by_id={"i1": fake_item},
        folder_path_index={"/raw": "f1"},
        item_to_folder={"i1": "f1"},
    )

    def fake_snapshot_workspace(workspace_id: str, *, client=None):
        assert workspace_id == "ws-x"
        return fake_snap

    monkeypatch.setattr(sync_cli_mod, "snapshot_workspace", fake_snapshot_workspace)

    out_path = tmp_path / "snap.json"
    result = runner.invoke(
        sync_app,
        ["snapshot", "--workspace-id", "ws-x", "--output", str(out_path)],
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    payload = _json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["workspace_id"] == "ws-x"
    assert payload["folder_path_index"] == {"/raw": "f1"}
    assert payload["item_to_folder"] == {"i1": "f1"}
    assert payload["folders_by_id"]["f1"]["display_name"] == "raw"
    assert payload["items_by_id"]["i1"]["type"] == "Notebook"


def test_sync_apply_with_invalid_manifest_exits_1(tmp_path: Path) -> None:
    """ManifestValidationError -> exit 1 with a violations dump."""
    bad = tmp_path / "sync.yml"
    bad.write_text(
        "schema_version: 'broken'\nitems: []\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        sync_app,
        [
            "apply",
            "--manifest",
            str(bad),
            "--workspace-id",
            "ws-x",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "validation" in result.output.lower()


def test_sync_subapp_registered_as_14th_on_root_cli() -> None:
    """The root ``sigantry_core.cli.app`` carries ``sync`` after the existing
    13 subapps; the registration is byte-additive (no prior groups removed).
    """
    # Typer keeps registered groups on ``app.registered_groups``; each
    # entry has a ``name`` matching the ``add_typer(..., name=...)``
    # argument.
    names = [g.name for g in root_app.registered_groups]
    assert "sync" in names, f"sync subapp not registered on the root sigantry CLI; got {names}"
    # The 13 prior subapps stay; sync is the 14th. The order of
    # ``add_typer`` calls is captured in the registration order.
    expected_prior = [
        "workspace",
        "capacity",
        "label-sync",
        "rbac-audit",
        "tenant-settings",
        "deploy",
        "fabric-item",
        "git",
        "variable-library",
        "env",
        "dq",
        "doctor",
        "release",
    ]
    for name in expected_prior:
        assert name in names, (
            f"prior subapp {name} disappeared from the root CLI; current registrations: {names}"
        )
    assert names.index("sync") > names.index("release"), (
        f"sync should register AFTER release (the 13th subapp); got registration order: {names}"
    )
