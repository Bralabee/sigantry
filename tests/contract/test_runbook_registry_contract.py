"""RunbookRegistry contract tests."""

from __future__ import annotations

import pytest

from sigantry_core.protocols import RunbookRegistry
from sigantry_core.testing.doubles import StaticRunbookRegistry


def _plugin_registry_or_skip():
    pytest.importorskip("sigantry_hs2")
    from sigantry_hs2.runbooks.hs2_teams_registry import (
        Hs2TeamsRunbookRegistry,
    )

    return Hs2TeamsRunbookRegistry()


@pytest.mark.contract
def test_runbook_registry_double_has_name(fdt_runbook_registry_contract) -> None:
    fdt_runbook_registry_contract(StaticRunbookRegistry({}))


@pytest.mark.contract
def test_runbook_registry_double_resolves_known_alert() -> None:
    reg = StaticRunbookRegistry({"alert-a": "https://wiki.example.invalid/runbook-a"})
    assert reg.resolve("alert-a") == "https://wiki.example.invalid/runbook-a"


@pytest.mark.contract
def test_runbook_registry_double_unknown_alert_returns_none() -> None:
    reg = StaticRunbookRegistry({})
    assert reg.resolve("nope") is None


@pytest.mark.contract
def test_runbook_registry_double_satisfies_runtime_protocol() -> None:
    assert isinstance(StaticRunbookRegistry({}), RunbookRegistry)


@pytest.mark.contract
def test_runbook_registry_plugin_has_name(fdt_runbook_registry_contract) -> None:
    fdt_runbook_registry_contract(_plugin_registry_or_skip())


@pytest.mark.contract
def test_runbook_registry_plugin_resolves_known_alert() -> None:
    reg = _plugin_registry_or_skip()
    url = reg.resolve("pipeline_run_failed")
    assert url is not None and url.startswith("http")
