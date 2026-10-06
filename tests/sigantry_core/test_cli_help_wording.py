"""CLI help describes the release ledger and rollback as they behave.

The deploy ledger is hash-chained but unkeyed: anyone who can write the file
can re-seal it, and the help text describes the ledger as unkeyed. A rollback
publishes the named items of the types in ``--item-types`` with content from
``--source``; its help must say so, without references to planning files.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from sigantry_core.cli import app

runner = CliRunner()

# A file name, a short ticket-style id, or a numbered planning note.
_PLANNING_REF = re.compile(r"\.md\b|\b[A-Z]{1,3}-\d{2}\b|\bPattern \d\b|\bAudit-\d{4}")


def _joined_help(args: list[str]) -> str:
    result = runner.invoke(app, [*args, "--help"])
    assert result.exit_code == 0, args
    # Join wrapped lines so a phrase split across the help box still counts.
    return " ".join(result.stdout.replace("│", " ").split())


def test_release_help_describes_the_ledger_as_unkeyed() -> None:
    for args in (["--help"], ["release", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, args
        assert "unkeyed" in result.stdout, args
        assert "immutable" not in result.stdout.lower(), args


def test_rollback_help_names_its_scope() -> None:
    text = _joined_help(["deploy", "run"])
    assert "--rollback-force" in text
    assert "overwrites" in text
    match = _PLANNING_REF.search(text)
    assert match is None, match


def test_release_list_and_diff_help_name_no_planning_files() -> None:
    for command, option in (("list", "--workspace"), ("diff", "--json")):
        text = _joined_help(["release", command])
        assert option in text, command
        match = _PLANNING_REF.search(text)
        assert match is None, (command, match)
