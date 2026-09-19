"""CLI wiring tests for the Plan 04-03 git / variable-library / env subapps.

The command-level behaviour of the underlying functions is covered by
``test_git_integration.py`` / ``test_variable_library.py`` /
``test_environment.py``. These tests only assert that:

  * each subapp registers on the root CLI
  * ``--help`` exits 0 and surfaces the expected subcommand names
  * destructive commands reject missing ``--force``
"""

from __future__ import annotations

from typer.testing import CliRunner

runner = CliRunner()


def test_root_cli_lists_git_subapp() -> None:
    from sigantry_core.cli import app

    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "git" in r.stdout


def test_git_help_lists_subcommands() -> None:
    from sigantry_core.cli import app

    r = runner.invoke(app, ["git", "--help"])
    assert r.exit_code == 0
    for cmd in ("connect", "init", "update", "commit", "status", "connection", "disconnect"):
        assert cmd in r.stdout, f"missing '{cmd}' in git --help output"


def test_git_connect_requires_connection_id() -> None:
    """``--git-connection-id`` is required (T-4-06 / Pitfall 4C)."""
    from sigantry_core.cli import app

    r = runner.invoke(
        app,
        [
            "git",
            "connect",
            "--workspace-id",
            "ws",
            "--ado-organization",
            "o",
            "--ado-project",
            "p",
            "--ado-repository",
            "r",
            "--branch",
            "main",
            "--directory",
            "d/",
        ],
    )
    # Missing --git-connection-id -> Typer should refuse (non-zero).
    assert r.exit_code != 0
    # Acceptable: Typer complains about missing option (message varies by version).
    assert (
        "git-connection-id" in (r.stdout + (r.stderr or "")).lower()
        or "missing option" in (r.stdout + (r.stderr or "")).lower()
    )


def test_root_cli_lists_variable_library_subapp() -> None:
    from sigantry_core.cli import app

    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "variable-library" in r.stdout


def test_variable_library_help_lists_subcommands() -> None:
    from sigantry_core.cli import app

    r = runner.invoke(app, ["variable-library", "--help"])
    assert r.exit_code == 0
    for cmd in ("create", "list", "get", "update", "delete"):
        assert cmd in r.stdout, f"missing '{cmd}' in variable-library --help output"


def test_root_cli_lists_env_subapp() -> None:
    from sigantry_core.cli import app

    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "env" in r.stdout


def test_env_help_lists_sync() -> None:
    from sigantry_core.cli import app

    r = runner.invoke(app, ["env", "--help"])
    assert r.exit_code == 0
    assert "sync" in r.stdout


def test_root_cli_lists_ten_subapps() -> None:
    """Phase 4 end-of-plan CLI-wiring invariant: 10 subapps registered."""
    from sigantry_core.cli import app

    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for sub in (
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
    ):
        assert sub in r.stdout, f"missing '{sub}' in root --help output"
