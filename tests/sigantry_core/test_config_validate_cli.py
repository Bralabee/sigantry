"""Plan 14-01 Task 2: ``sigantry config validate`` Typer subcommand tests.

Four scenarios:

1. Clean starter file (with the six SIGANTRY_FABRIC_* env vars set)
   exits 0 and prints ``OK -- 3 environment(s) parsed: DEV, PREPROD, PROD``.
2. Missing file -> exit 2; stderr contains ``"not found"``.
3. Hard-coded GUID outside an allowed prefix -> exit 1; stderr contains
   ``"hard-coded GUID"`` and the dotted-path location.
4. Unresolved ``$ENV:<VAR>`` reference -> exit 1; stderr contains the
   variable name.

CliRunner default in Click 8.3+ has ``mix_stderr=True``, so stderr is
folded into ``result.output``. We assert against ``result.output``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sigantry_core.cli import app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STARTER_PARAMS = _REPO_ROOT / "templates" / "starter" / "parameters.yml"

_STARTER_ENV_VARS = (
    "SIGANTRY_FABRIC_WORKSPACE_ID_DEV",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_DEV",
    "SIGANTRY_FABRIC_CAPACITY_ID_PREPROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_PROD",
)


def test_config_validate_clean_starter_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Starter parameters.yml with $ENV vars set -> exit 0 + OK summary line."""
    for var in _STARTER_ENV_VARS:
        monkeypatch.setenv(var, "00000000-0000-0000-0000-000000000001")

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", str(_STARTER_PARAMS)])

    assert result.exit_code == 0, (
        f"expected exit 0; got {result.exit_code}; output={result.output!r}"
    )
    assert "OK -- 3 environment(s) parsed: DEV, PREPROD, PROD" in result.output, result.output


def test_config_validate_missing_file_exits_two(tmp_path: Path) -> None:
    """Path that does not exist -> exit 2 + 'not found' on stderr."""
    runner = CliRunner()
    nonexistent = tmp_path / "nope.yml"
    result = runner.invoke(app, ["config", "validate", str(nonexistent)])

    assert result.exit_code == 2, (
        f"expected exit 2; got {result.exit_code}; output={result.output!r}"
    )
    assert "not found" in result.output, result.output


def test_config_validate_hardcoded_guid_exits_one(tmp_path: Path) -> None:
    """Raw GUID outside an allowed prefix -> exit 1 with dotted-path error."""
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "find_replace:\n"
        "  - find_value: 'placeholder-bronze-ws'\n"
        "    item_type: Notebook\n"
        "    replace_value:\n"
        "      DEV: '12345678-1234-1234-1234-123456789012'\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", str(bad)])

    assert result.exit_code == 1, (
        f"expected exit 1; got {result.exit_code}; output={result.output!r}"
    )
    assert "hard-coded GUID" in result.output, result.output
    assert "find_replace" in result.output, result.output


def test_config_validate_unresolved_env_ref_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unresolved $ENV:<VAR> reference -> exit 1 with the variable name in stderr."""
    monkeypatch.delenv("NONEXISTENT_VAR_FOR_TEST", raising=False)
    bad = tmp_path / "unresolved.yml"
    bad.write_text(
        "find_replace:\n"
        "  - find_value: 'placeholder-bronze-ws'\n"
        "    item_type: Notebook\n"
        "    replace_value:\n"
        "      DEV: '$ENV:NONEXISTENT_VAR_FOR_TEST'\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", str(bad)])

    assert result.exit_code == 1, (
        f"expected exit 1; got {result.exit_code}; output={result.output!r}"
    )
    assert "NONEXISTENT_VAR_FOR_TEST" in result.output, result.output
