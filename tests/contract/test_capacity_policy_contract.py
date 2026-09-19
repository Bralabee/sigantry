"""CapacityPolicy contract tests."""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    CapacityApplyResult,
    CapacityContext,
    CapacityPolicy,
)
from sigantry_core.testing.doubles import NoopCapacityPolicy


def _plugin_policy_or_skip():
    pytest.importorskip("sigantry_hs2")
    from sigantry_hs2.capacity.hs2_capacity_policy import (
        Hs2CapacityPolicy,
    )

    return Hs2CapacityPolicy()


@pytest.mark.contract
def test_capacity_policy_double_has_name(fdt_capacity_policy_contract) -> None:
    fdt_capacity_policy_contract(NoopCapacityPolicy())


@pytest.mark.contract
def test_capacity_policy_double_plan_returns_list() -> None:
    pol = NoopCapacityPolicy()
    ctx = CapacityContext(capacity_id="cap-1", tenant_id="tenant-1")
    actions = pol.plan(ctx)
    assert actions == []


@pytest.mark.contract
def test_capacity_policy_double_apply_returns_capacity_apply_result() -> None:
    pol = NoopCapacityPolicy()
    ctx = CapacityContext(capacity_id="cap-1", tenant_id="tenant-1")
    result = pol.apply(ctx, [])
    assert isinstance(result, CapacityApplyResult)


@pytest.mark.contract
def test_capacity_policy_double_satisfies_runtime_protocol() -> None:
    assert isinstance(NoopCapacityPolicy(), CapacityPolicy)


@pytest.mark.contract
def test_capacity_policy_plugin_has_name(fdt_capacity_policy_contract) -> None:
    fdt_capacity_policy_contract(_plugin_policy_or_skip())


@pytest.mark.contract
def test_capacity_policy_plugin_apply_returns_capacity_apply_result() -> None:
    pol = _plugin_policy_or_skip()
    ctx = CapacityContext(capacity_id="cap-plugin", tenant_id="tenant-plugin")
    result = pol.apply(ctx, [])
    assert isinstance(result, CapacityApplyResult)
