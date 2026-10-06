"""``sigantry sync pull`` prints item names, paths and error text as written.

Rich reads ``[...]`` in a string it prints as markup. ``sync pull`` refuses a
workspace item whose display name contains ``/``, and its message quotes that
name, so an item named ``Sales [/old]`` raised ``MarkupError`` inside the
refusal itself (exit 1 with a traceback in place of the message), and a word
in square brackets such as ``[draft]`` was dropped.

``pull_workspace`` is replaced in every test, so no tenant is contacted.
Paths are relative to the test's working directory, so a line stays short
enough that Rich never splits a path inside a word.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core.sync import cli as sync_cli_mod
from sigantry_core.sync.cli import sync_app
from sigantry_core.sync.errors import PullTargetNotEmptyError
from sigantry_core.sync.pull import SyncPullReport, _local_source_dir
from sigantry_core.sync.snapshot import WorkspaceSnapshot
from sigantry_core.workspace.items import Item

runner = CliRunner()


@pytest.fixture(autouse=True)
def _offline_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setenv("SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED", "true")
    sync_cli_mod._PREVIEW_WARNING_EMITTED.clear()
    monkeypatch.chdir(tmp_path)


def _flat(text: str) -> str:
    """Rich wraps long lines at the console width; compare word by word."""
    return " ".join(text.split())


def _pull(into: str = "out") -> list[str]:
    return ["pull", "--workspace-id", "ws-test", "--into", into]


def _item(display_name: str) -> Item:
    return Item(
        id="item-1",
        display_name=display_name,
        type="Notebook",
        workspace_id="ws-test",
        description=None,
        sensitivity_label_id=None,
        folder_id=None,
    )


@pytest.mark.parametrize("display_name", ["Sales [/old]", "a/b [draft]"])
def test_pull_refusal_of_an_item_name_printed_as_written(
    monkeypatch: pytest.MonkeyPatch, display_name: str
) -> None:
    def _refuse(*, into: str, **_: object) -> None:
        _local_source_dir(Path(into), "/", _item(display_name))

    monkeypatch.setattr(sync_cli_mod, "pull_workspace", _refuse)
    result = runner.invoke(sync_app, _pull())
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert "sync pull failed: sync pull refused workspace item item_id=item-1" in flat
    assert f"display_name={display_name!r}" in flat
    assert "rename the item or its folder in the workspace and pull again." in flat


def test_pull_target_not_empty_refusal_printed_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refuse(*, into: str, **_: object) -> None:
        raise PullTargetNotEmptyError(
            f"sync pull target {into} is not empty; pass --force to overwrite", target=into
        )

    monkeypatch.setattr(sync_cli_mod, "pull_workspace", _refuse)
    result = runner.invoke(sync_app, _pull("o[/x]"))
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert "sync pull refused: sync pull target o[/x] is not empty" in flat


def test_pull_success_line_prints_sync_yml_path_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    into = Path("o[/x]")
    report = SyncPullReport(
        workspace_id="ws-test",
        into=into,
        items_pulled=2,
        sync_yml_path=into / "sync.yml",
        snapshot=WorkspaceSnapshot(
            schema_version="1.0.0",
            workspace_id="ws-test",
            folder_path_index={},
            item_to_folder={},
            folders_by_id={},
            items_by_id={},
        ),
    )
    monkeypatch.setattr(sync_cli_mod, "pull_workspace", lambda **_: report)
    result = runner.invoke(sync_app, _pull(str(into)))
    assert result.exit_code == 0, repr(result.exception)
    flat = _flat(result.stdout)
    assert f"sync pull succeeded items_pulled=2 sync_yml={into / 'sync.yml'}" in flat
    assert "note: 2 item(s) pulled." in flat
