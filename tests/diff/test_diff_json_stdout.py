"""``sigantry diff --output json`` keeps errors off stdout.

Scheduled drift pipelines run ``sigantry diff --output json ... > drift.json``
and hand the file to the notify job. Error text on stdout made that file
non-JSON: on every scheduled run of the shipped workflow (empty inputs) the
command wrote ``sigantry diff failed: [Errno 21] Is a directory: '.'`` to
stdout at exit 2. Errors now go to stderr; stdout stays empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import sigantry_core.diff_cli as diff_cli
from sigantry_core.cli import app
from sigantry_core.sync.errors import ManifestValidationError, WorkspacePendingGitUpdateError

runner = CliRunner()


def test_empty_inputs_leave_stdout_empty() -> None:
    """The exact call a scheduled run with no inputs makes."""
    result = runner.invoke(
        app,
        ["diff", "--environment", "", "--workspace-id", "", "--manifest", "", "--output", "json"],
    )
    assert result.exit_code == 2
    assert result.stdout == "", f"error text on stdout: {result.stdout!r}"
    assert "sigantry diff failed" in result.stderr


@pytest.mark.parametrize(
    ("exc", "text"),
    [
        (RuntimeError("tenant unreachable"), "tenant unreachable"),
        (WorkspacePendingGitUpdateError("sync pending", sync_state="Pending"), "sync pending"),
    ],
    ids=["operational-error", "pending-git-update"],
)
def test_operational_errors_go_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception, text: str
) -> None:
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    def boom(*_a: object, **_k: object) -> None:
        raise exc

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", boom)

    result = runner.invoke(
        app,
        ["diff", "--workspace-id", "ws-x", "--manifest", str(manifest), "--output", "json"],
    )
    assert result.exit_code == 2
    assert result.stdout == ""
    assert text in result.stderr


@pytest.mark.parametrize(
    ("output", "exc", "texts"),
    [
        (
            "json",
            ManifestValidationError(
                "bad manifest",
                violations=[
                    {"field": "items.0.target_folder", "decision_id": "D-05", "severity": "error"}
                ],
            ),
            ("Manifest validation failed", "items.0.target_folder"),
        ),
        ("bogus", None, ("Invalid --output 'bogus'",)),
    ],
    ids=["manifest-validation", "invalid-output"],
)
def test_rejections_go_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    exc: Exception | None,
    texts: tuple[str, ...],
) -> None:
    """The two rejection branches, including each violation line."""
    manifest = tmp_path / "sync.yml"
    manifest.write_text("schema_version: '1.0.0'\nitems: []\n", encoding="utf-8")

    def reject(*_a: object, **_k: object) -> None:
        if exc is None:
            raise AssertionError("an invalid --output must be rejected before diffing")
        raise exc

    monkeypatch.setattr(diff_cli, "diff_workspace_against_manifest", reject)

    result = runner.invoke(
        app,
        ["diff", "--workspace-id", "ws-x", "--manifest", str(manifest), "--output", output],
    )
    assert result.exit_code == 2
    assert result.stdout == "", f"error text on stdout: {result.stdout!r}"
    for text in texts:
        assert text in result.stderr
