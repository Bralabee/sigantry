"""RunbookRegistry contract tests."""

from __future__ import annotations

import pytest

from sigantry_core.protocols import RunbookRegistry
from sigantry_core.testing.doubles import StaticRunbookRegistry


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
