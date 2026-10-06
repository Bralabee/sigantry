"""``sigantry deploy`` prints paths, release ids and error text as written.

Rich reads ``[...]`` in a string it prints as markup. A ``--params`` path, a
``--source`` path or a release id that held a closing tag such as ``[/x]``
raised ``MarkupError`` (exit 1 with a traceback in place of the message).

Paths are given relative to the test's working directory, so a line stays
short enough that Rich never splits a path inside a word.
"""

from __future__ import annotations

from pathlib import Path

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
