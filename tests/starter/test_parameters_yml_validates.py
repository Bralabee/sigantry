"""Plan 14-01 STARTER-02: starter parameters.yml validates clean.

Two tests:

1. Direct call to ``load_and_validate`` over ``templates/starter/parameters.yml``
   with the six SIGANTRY_FABRIC_* env vars pre-set via ``monkeypatch.setenv``;
   asserts the file parses, no exception is raised, and the
   environments-seen set equals ``{DEV, PREPROD, PROD}`` (no ``_ALL_``).
2. Same assertion through the new ``sigantry config validate <file>``
   CLI subcommand (added in Task 2). Verifies exit code 0 and the
   ``OK -- 3 environment(s) parsed: DEV, PREPROD, PROD`` stdout line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

_STARTER_DIR = Path(__file__).resolve().parents[2] / "templates" / "starter"
_STARTER_PARAMS = _STARTER_DIR / "parameters.yml"

# All SIGANTRY_FABRIC_* env-var names referenced in templates/starter/parameters.yml.
# The validator's `_resolve_env_references` pass requires every $ENV:<VAR>
# reference to resolve to a SET environment variable; for CI we set them to
# dummy values to prove the file is structurally clean without requiring
# real Fabric credentials.
_STARTER_ENV_VARS = (
    "SIGANTRY_FABRIC_WORKSPACE_ID_DEV",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_DEV",
    "SIGANTRY_FABRIC_CAPACITY_ID_PREPROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_PROD",
)


def test_starter_parameters_yml_validates_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """sigantry_core.deploy.parameters.load_and_validate(starter/parameters.yml) returns a Settings instance with no errors."""
    from sigantry_core.deploy.parameters import load_and_validate

    for var in _STARTER_ENV_VARS:
        monkeypatch.setenv(var, "00000000-0000-0000-0000-000000000001")

    result = load_and_validate(_STARTER_PARAMS)
    assert result.environments_seen == frozenset({"DEV", "PREPROD", "PROD"}), (
        f"expected DEV/PREPROD/PROD; got {result.environments_seen}"
    )
    assert result.path == str(_STARTER_PARAMS)


def test_sigantry_config_validate_cli_exits_zero_on_clean_starter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sigantry config validate templates/starter/parameters.yml` exits 0 with no stderr (D-16)."""
    from sigantry_core.cli import app

    for var in _STARTER_ENV_VARS:
        monkeypatch.setenv(var, "00000000-0000-0000-0000-000000000001")

    runner = CliRunner()
    result = runner.invoke(app, ["config", "validate", str(_STARTER_PARAMS)])
    assert result.exit_code == 0, (
        f"expected exit 0; got {result.exit_code}; output={result.output!r}"
    )
    assert "OK -- 3 environment(s) parsed: DEV, PREPROD, PROD" in result.output, result.output
