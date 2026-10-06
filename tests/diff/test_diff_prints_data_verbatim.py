"""``sigantry diff`` prints manifest, workspace and error text as it is.

Rich reads ``[...]`` in a string it prints as markup and ``:name:`` as an
emoji code. Text from a manifest, a workspace or an exception reached Rich as
markup, so a closing tag such as ``[/old]`` raised ``MarkupError`` (exit 1,
which is the drift exit code, with a traceback in place of the message), a
tag-like word such as ``[draft]`` was dropped, and ``:fire:`` in a quoted YAML
line was replaced by an emoji.

Errors print on stdout in human mode, as in 1.0.0, and on stderr under
``--output json`` or ``--output html``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import sigantry_core.diff_cli as diff_cli
from sigantry_core.cli import app
from sigantry_core.sync.diff import DriftReport
from sigantry_core.sync.errors import WorkspacePendingGitUpdateError

runner = CliRunner()

CLOSING_TAG = "[/old]"
TAG_LIKE = "[draft]"


def _flat(text: str) -> str:
    """Rich wraps long lines at the console width; compare word by word."""
    return " ".join(text.split())


def _manifest(tmp_path: Path, display_name: str) -> Path:
    path = tmp_path / "sync.yml"
    path.write_text(
        "schema_version: '1.0.0'\n"
        "items:\n"
        "  - local_path: nb\n"
        "    type: Notebook\n"
        f"    display_name: {display_name}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("output", ["human", "json"])
@pytest.mark.parametrize(
    "display_name",
    [f"Sales {CLOSING_TAG} v1.2", f"Q3 report {TAG_LIKE} v1.2"],
    ids=["closing-tag-exits-2", "tag-like-word-kept"],
)
def test_rejected_manifest_value_is_printed_as_written(
    tmp_path: Path, display_name: str, output: str
) -> None:
    """The real loader rejects the '.', and its message quotes the value back."""
    result = runner.invoke(
        app,
        [
            "diff",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(_manifest(tmp_path, display_name)),
            "--output",
            output,
        ],
    )
    assert result.exit_code == 2, repr(result.exception)
    shown, other = (
        (result.stdout, result.stderr) if output == "human" else (result.stderr, result.stdout)
    )
    assert other == ""
    assert "Manifest validation failed" in shown
    assert f"display_name '{display_name}'" in _flat(shown)


def test_manifest_path_is_printed_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary line names the manifest path; ``x[/y]`` is a closing tag."""
    folder = tmp_path / "x[" / "y]"
    folder.mkdir(parents=True)
    _manifest(folder, "Sales v1.2")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["diff", "--workspace-id", "ws-x", "--manifest", "x[/y]/sync.yml"],
    )
    assert result.exit_code == 2, repr(result.exception)
    assert result.stderr == ""
    shown = Path("x[/y]") / "sync.yml"  # the loader prints the path as given
    assert f"Manifest validation failed: sync.yml at {shown}" in _flat(result.stdout)


def test_quoted_yaml_line_keeps_emoji_codes(tmp_path: Path) -> None:
    """A PyYAML error quotes the offending line; it must match the file."""
    result = runner.invoke(
        app,
        [
            "diff",
            "--workspace-id",
            "ws-x",
            "--manifest",
            str(_manifest(tmp_path, "Q3 :fire: v1")),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 2, repr(result.exception)
    # The line is quoted twice: in the summary and in the violation.
    assert _flat(result.stderr).count("display_name: Q3 :fire: v1") == 2
    assert "\N{FIRE}" not in result.stderr


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError(f"tenant {CLOSING_TAG} unreachable {TAG_LIKE}"),
        WorkspacePendingGitUpdateError(
            f"sync {CLOSING_TAG} pending {TAG_LIKE}", sync_state="Pending"
        ),
    ],
    ids=["operational-error", "pending-git-update"],
)
def test_error_text_is_printed_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise exc

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", boom)
    result = runner.invoke(
        app,
        ["diff", "--workspace-id", "ws-x", "--manifest", str(tmp_path / "sync.yml")],
    )
    assert result.exit_code == 2, repr(result.exception)
    assert result.stderr == ""
    assert str(exc) in _flat(result.stdout)


def test_invalid_output_value_is_printed_as_written() -> None:
    result = runner.invoke(
        app,
        [
            "diff",
            "--workspace-id",
            "ws-x",
            "--manifest",
            "sync.yml",
            "--output",
            CLOSING_TAG,
        ],
    )
    assert result.exit_code == 2, repr(result.exception)
    assert result.stderr == ""
    assert f"Invalid --output '{CLOSING_TAG}'" in result.stdout


def _report(token: str) -> DriftReport:
    return DriftReport(
        added=[
            {
                "type": f"Notebook{token}",
                "display_name": f"add{token}",
                "folder_path": f"/f{token}",
                "logical_id": f"lid{token}",
            }
        ],
        removed=[
            {
                "type": f"Lakehouse{token}",
                "display_name": f"rem{token}",
                "folder_path": f"/r{token}",
                "logical_id": f"rid{token}",
            }
        ],
        modified=[{"logical_id": f"mod{token}", "fields_changed": ["folder_path"]}],
        unchanged=[{"logical_id": f"unch{token}"}],
    )


@pytest.mark.parametrize("token", [CLOSING_TAG, TAG_LIKE], ids=["closing-tag", "tag-like-word"])
def test_human_table_prints_names_as_written(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setattr(
        diff_cli, "diff_workspace_against_manifest", lambda *_a, **_k: _report(token)
    )
    result = runner.invoke(
        app,
        [
            "diff",
            "-e",
            f"env{token}",
            "--workspace-id",
            "ws-x",
            "--manifest",
            "sync.yml",
        ],
    )
    assert result.exit_code == 0, repr(result.exception)
    for text in (
        f"environment='env{token}'",
        f"Notebook{token}",
        f"add{token}",
        f"/f{token}",
        f"lid{token}",
        f"Lakehouse{token}",
        f"rem{token}",
        f"/r{token}",
        f"rid{token}",
        f"mod{token}",
        f"unch{token}",
    ):
        assert text in result.stdout, text


@pytest.mark.parametrize(
    "name",
    ["x[/y].html", f"report{TAG_LIKE}.html"],
    ids=["closing-tag", "tag-like-word"],
)
def test_html_out_path_is_printed_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (tmp_path / "x[").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", lambda *_a, **_k: _report(""))
    result = runner.invoke(
        app,
        [
            "diff",
            "--workspace-id",
            "ws-x",
            "--manifest",
            "sync.yml",
            "--output",
            "html",
            "--html-out",
            name,
        ],
    )
    assert result.exit_code == 0, repr(result.exception)
    assert (tmp_path / name).is_file()
    assert f"HTML drift report written to: {name}" in _flat(result.stdout)
