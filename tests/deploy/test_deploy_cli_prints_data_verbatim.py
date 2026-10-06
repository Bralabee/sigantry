"""``sigantry deploy`` prints paths, release ids and error text as written.

Rich reads ``[...]`` in a string it prints as markup. A ``--params`` path, a
``--source`` path or a release id that held a closing tag such as ``[/x]``
raised ``MarkupError`` (exit 1 with a traceback in place of the message).

Paths are given relative to the test's working directory, so a line stays
short enough that Rich never splits a path inside a word.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app

runner = CliRunner()

PARAMS = "p[/x].yml"  # a file "x].yml" in a directory "p[": the path text holds a closing tag


@pytest.fixture(autouse=True)
def _offline_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    temp = tmp_path / "tmp"
    home.mkdir()
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.chdir(tmp_path)


def _flat(text: str) -> str:
    """Rich wraps long lines at the console width; compare word by word."""
    return " ".join(text.split())


def _validate(source: str) -> list[str]:
    return ["deploy", "validate", "--source", source, "--params", PARAMS, "--skip-pre-commit"]


def test_validate_missing_params_path_printed_as_written() -> None:
    Path("src").mkdir()
    result = runner.invoke(app, _validate("src"))
    assert result.exit_code == 2, repr(result.exception)
    flat = _flat(result.stdout)
    assert "FAIL parameters missing:" in flat
    assert str(Path(PARAMS)) in flat


def test_validate_params_and_graph_paths_printed_as_written() -> None:
    Path("src").mkdir()
    Path(PARAMS).parent.mkdir()
    Path(PARAMS).write_text("find_replace: []\n", encoding="utf-8")
    result = runner.invoke(app, [*_validate("src"), "--dot-output", "g[/z]"])
    assert result.exit_code == 0, repr(result.exception)
    flat = _flat(result.stdout)
    assert f"OK parameters: {PARAMS}" in flat
    assert f"OK dependency graph ({Path('g[/z]') / 'dep-graph.dot'})" in flat


def test_validate_invalid_params_error_printed_as_written() -> None:
    Path("src").mkdir()
    Path(PARAMS).parent.mkdir()
    Path(PARAMS).write_text("just a string [/z]\n", encoding="utf-8")
    result = runner.invoke(app, _validate("src"))
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert f"FAIL parameters: parameters.yml at {Path(PARAMS)} must be a mapping" in flat


def test_rollback_failure_message_prints_release_id_as_written() -> None:
    Path("audit").mkdir()
    result = runner.invoke(
        app,
        [
            "deploy",
            "run",
            "--source",
            ".",
            "--workspace-id",
            "ws-test",
            "--environment",
            "DEV",
            "--rollback",
            "--to-release",
            "[/x]",
            "--rollback-force",
            "--audit-dir",
            "audit",
        ],
    )
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert "rollback failed:" in flat
    assert "No release '[/x]'" in flat


def test_deploy_run_failure_message_printed_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    import sigantry_core.auth as auth_mod
    import sigantry_core.deploy.cli as deploy_cli

    def _fail(**_: object) -> None:
        raise RuntimeError("item 'nb [/old]' failed to publish [draft]")

    monkeypatch.setattr(auth_mod.TokenProvider, "from_defaults", MagicMock())
    monkeypatch.setattr(deploy_cli, "deploy_workspace", _fail)
    result = runner.invoke(
        app,
        ["deploy", "run", "--source", ".", "--workspace-id", "ws-test", "--environment", "DEV"],
    )
    assert result.exit_code == 1, repr(result.exception)
    assert "deploy failed: item 'nb [/old]' failed to publish [draft]" in _flat(result.stdout)


def test_fabric_item_copy_failure_prints_path_as_written() -> None:
    Path("s[").mkdir()
    Path("s[/x]").mkdir()
    result = runner.invoke(
        app, ["fabric-item", "copy", "s[/x]", "d", "--new-display-name", "n"]
    )
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert f"fabric-item copy failed: {Path('s[/x]')} does not contain a .platform file" in flat


def test_fabric_item_copy_success_prints_paths_as_written() -> None:
    Path("s[").mkdir()
    Path("s[/x]").mkdir()
    (Path("s[/x]") / ".platform").write_text('{"metadata": {}, "config": {}}', encoding="utf-8")
    result = runner.invoke(
        app, ["fabric-item", "copy", "s[/x]", "d[/y]", "--new-display-name", "n"]
    )
    assert result.exit_code == 0, repr(result.exception)
    assert "Copied s[/x] -> d[/y] (new logicalId=" in _flat(result.stdout)


def _binding_args() -> list[str]:
    return [
        "fabric-item",
        "set-binding",
        "--workspace-id",
        "ws-test",
        "--item-id",
        "nb[/i]",
        "--environment-id",
        "env-test",
    ]


def _stub_binding_client(monkeypatch: pytest.MonkeyPatch, binding: object) -> None:
    import sigantry_core.deploy.cli as deploy_cli

    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(deploy_cli, "_client", lambda _tenant: client)
    monkeypatch.setattr(deploy_cli, "set_notebook_binding", binding)


def test_set_binding_failure_message_printed_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sigantry_core.deploy.notebook_binding import NotebookBindingError

    def _fail(*_: object, **__: object) -> None:
        raise NotebookBindingError("notebook 'nb [/old]' has no ipynb part [draft]")

    _stub_binding_client(monkeypatch, _fail)
    result = runner.invoke(app, _binding_args())
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert "fabric-item set-binding failed: notebook 'nb [/old]' has no ipynb part [draft]" in flat


@pytest.mark.parametrize("changed", [True, False])
def test_set_binding_result_printed_as_written(
    monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    from sigantry_core.deploy.notebook_binding import BindingResult

    result_obj = MagicMock(spec=BindingResult)
    result_obj.changed = changed
    result_obj.environment = "env [/e]"
    result_obj.lakehouse = "lh [draft]"
    _stub_binding_client(monkeypatch, lambda *_a, **_k: result_obj)
    result = runner.invoke(app, _binding_args())
    assert result.exit_code == 0, repr(result.exception)
    flat = _flat(result.stdout)
    if changed:
        assert "bound item nb[/i]: environment=env [/e] lakehouse=lh [draft]" in flat
    else:
        assert "no change item nb[/i] already had the requested binding." in flat


def test_env_sync_all_manifest_failure_printed_as_written() -> None:
    Path("m[").mkdir()
    Path("m[/x].yml").write_text("targets: [draft]\n", encoding="utf-8")
    result = runner.invoke(app, ["env", "sync-all", "--manifest", "m[/x].yml", "--dry-run"])
    assert result.exit_code == 1, repr(result.exception)
    flat = _flat(result.stdout)
    assert f"environments manifest validation failed: environments manifest at {Path('m[/x].yml')}" in flat
    assert "- {'field':" in flat
