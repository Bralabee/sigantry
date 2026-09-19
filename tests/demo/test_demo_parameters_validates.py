"""Plan 15-01 / DEMO-01 -- parameters.yml validity for the demo template.

Wave 0 (Plan 15-00) stamped three xfail stubs; Plan 15-01 ships
templates/demo/parameters.yml with the DEV ENV-var refs swapped to
SIGANTRY_DEMO_* (CONTEXT D-06; RESEARCH §Pitfall 9 -- DEV/PREPROD/PROD
env names retained because that's sigantry config validate's whitelist;
no `DEMO` env name introduced).

Three real assertions:
  1. sigantry_core.deploy.parameters.load_and_validate succeeds and
     reports DEV/PREPROD/PROD as the environments seen.
  2. The find_replace block uses ONLY DEV/PREPROD/PROD as env keys
     (Pitfall 9 -- never `DEMO` as an env name).
  3. The DEV slot of every find_replace block carries `$ENV:SIGANTRY_DEMO_*`
     refs (D-06 -- demo CI-supplied secrets), NOT `$ENV:SIGANTRY_FABRIC_*`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_DEMO_PARAMS = _REPO / "templates" / "demo" / "parameters.yml"

# All env vars referenced from templates/demo/parameters.yml; load_and_validate's
# `_resolve_env_references` pass requires these to be set at validation time.
_DEMO_ENV_VARS = (
    "SIGANTRY_DEMO_WORKSPACE_ID",
    "SIGANTRY_DEMO_CAPACITY_ID",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD",
    "SIGANTRY_FABRIC_WORKSPACE_ID_PROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_PREPROD",
    "SIGANTRY_FABRIC_CAPACITY_ID_PROD",
)


def test_demo_parameters_yml_validates_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """sigantry_core.deploy.parameters.load_and_validate(demo/parameters.yml) returns DEV/PREPROD/PROD env set."""
    from sigantry_core.deploy.parameters import load_and_validate

    for var in _DEMO_ENV_VARS:
        monkeypatch.setenv(var, "00000000-0000-0000-0000-000000000001")

    result = load_and_validate(_DEMO_PARAMS)
    assert result.environments_seen == frozenset({"DEV", "PREPROD", "PROD"}), (
        f"expected DEV/PREPROD/PROD; got {result.environments_seen}"
    )
    assert result.path == str(_DEMO_PARAMS)


def test_demo_parameters_uses_DEV_environment_only_in_workflows() -> None:  # noqa: N802 -- DEV is an env-name acronym
    """parameters.yml uses DEV/PREPROD/PROD env keys only (Pitfall 9 -- no `DEMO` env name)."""
    doc = yaml.safe_load(_DEMO_PARAMS.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), doc

    seen_env_keys: set[str] = set()
    entries = doc.get("find_replace", []) or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        replace_value: Any = entry.get("replace_value") or {}
        if isinstance(replace_value, dict):
            seen_env_keys.update(replace_value.keys())

    assert seen_env_keys == {"DEV", "PREPROD", "PROD"}, (
        f"demo parameters.yml must use DEV/PREPROD/PROD env keys only "
        f"(Pitfall 9 -- never introduce a `DEMO` env name); got {seen_env_keys}"
    )


def test_demo_parameters_uses_SIGANTRY_DEMO_env_refs() -> None:  # noqa: N802 -- SIGANTRY_DEMO is an env-var prefix
    """DEV slot of every find_replace block uses `$ENV:SIGANTRY_DEMO_*`, NOT `$ENV:SIGANTRY_FABRIC_*` (D-06)."""
    doc = yaml.safe_load(_DEMO_PARAMS.read_text(encoding="utf-8"))
    entries = doc.get("find_replace", []) or []
    assert entries, "demo parameters.yml must define at least one find_replace entry"

    for entry in entries:
        replace_value = entry.get("replace_value") or {}
        dev_ref = replace_value.get("DEV")
        assert isinstance(dev_ref, str), f"find_replace entry missing DEV ref: {entry!r}"
        assert dev_ref.startswith("$ENV:SIGANTRY_DEMO_"), (
            f"DEV slot must use `$ENV:SIGANTRY_DEMO_*` per CONTEXT D-06; got {dev_ref!r}"
        )
        assert "SIGANTRY_FABRIC_" not in dev_ref, (
            f"DEV slot must NOT use `$ENV:SIGANTRY_FABRIC_*` "
            f"(those are starter / preprod / prod refs); got {dev_ref!r}"
        )
