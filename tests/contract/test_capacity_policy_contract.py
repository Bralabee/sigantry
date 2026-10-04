"""CapacityPolicy contract tests."""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    CapacityApplyResult,
    CapacityContext,
    CapacityPolicy,
)
from sigantry_core.testing.doubles import NoopCapacityPolicy


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
