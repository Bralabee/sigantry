"""CLI help describes the release ledger and rollback as they behave.

The deploy ledger is hash-chained but unkeyed: anyone who can write the file
can re-seal it, so help text must not call it immutable. A rollback publishes
the named items of the types in ``--item-types`` with content from
``--source``; its help must say so, without internal review references.
"""

from __future__ import annotations

from typer.testing import CliRunner

from sigantry_core.cli import app

runner = CliRunner()


def test_release_help_describes_the_ledger_as_unkeyed() -> None:
    for args in (["--help"], ["release", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, args
        assert "unkeyed" in result.stdout, args
        assert "immutable" not in result.stdout.lower(), args


def test_rollback_help_names_its_scope() -> None:
    result = runner.invoke(app, ["deploy", "run", "--help"])
    assert result.exit_code == 0
    assert "--rollback-force" in result.stdout
    assert "overwrites" in result.stdout
    for stale in ("supplants", "Audit-2026", "@destructive_op"):
        assert stale not in result.stdout, stale


def test_release_list_and_diff_help_name_no_planning_files() -> None:
    for command, option in (("list", "--workspace"), ("diff", "--json")):
        result = runner.invoke(app, ["release", command, "--help"])
        assert result.exit_code == 0, command
        # Join wrapped lines so a phrase split across the help box still counts.
        text = " ".join(result.stdout.replace("\u2502", " ").split())
        assert option in text, command
        for stale in ("REVIEW.md", "WR-04", "Pattern 4", "Pattern 5", "D-06"):
            assert stale not in text, (command, stale)
