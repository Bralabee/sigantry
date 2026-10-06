"""Which stream ``sigantry diff`` prints its errors on, per output mode.

1.0.0 printed every error on stdout. Under ``--output json`` and
``--output html`` stdout carries the report, so errors go to stderr there.
In human mode, and for an invalid ``--output`` value, they stay on stdout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import sigantry_core.diff_cli as diff_cli
from sigantry_core.cli import app
from sigantry_core.sync.errors import WorkspacePendingGitUpdateError

runner = CliRunner()


def _bad_manifest(tmp_path: Path) -> Path:
    path = tmp_path / "sync.yml"
    path.write_text(
        "schema_version: '1.0.0'\n"
        "items:\n"
        "  - local_path: nb\n"
        "    type: Notebook\n"
        "    display_name: Sales v1.2\n",
        encoding="utf-8",
    )
    return path


def _raise(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise exc

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", boom)


CASES = ["manifest-validation", "operational-error", "pending-git-update"]


def _arrange(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Return the manifest to pass and a phrase the error message must hold."""
    if case == "manifest-validation":
        return _bad_manifest(tmp_path), "Manifest validation failed"
    if case == "operational-error":
        _raise(monkeypatch, RuntimeError("tenant unreachable"))
        return tmp_path / "sync.yml", "sigantry diff failed: tenant unreachable"
    _raise(monkeypatch, WorkspacePendingGitUpdateError("sync pending", sync_state="Pending"))
    return tmp_path / "sync.yml", "sync pending"


@pytest.mark.parametrize("case", CASES)
def test_human_mode_error_on_stdout_as_1_0_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    manifest, phrase = _arrange(case, tmp_path, monkeypatch)
    result = runner.invoke(app, ["diff", "--workspace-id", "ws-x", "--manifest", str(manifest)])
    assert result.exit_code == 2, repr(result.exception)
    assert phrase in result.stdout
    assert result.stderr == ""


def test_invalid_output_error_on_stdout_as_1_0_0() -> None:
    result = runner.invoke(
        app, ["diff", "--workspace-id", "ws-x", "--manifest", "sync.yml", "--output", "bogus"]
    )
    assert result.exit_code == 2, repr(result.exception)
    assert "Invalid --output 'bogus'" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("output", ["json", "html"])
@pytest.mark.parametrize("case", CASES)
def test_json_and_html_mode_error_on_stderr_stdout_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, output: str
) -> None:
    manifest, phrase = _arrange(case, tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        ["diff", "--workspace-id", "ws-x", "--manifest", str(manifest), "--output", output],
    )
    assert result.exit_code == 2, repr(result.exception)
    assert result.stdout == ""
    assert phrase in result.stderr
