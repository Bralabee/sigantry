"""DeployProfile contract tests.

Runs the three standard :class:`~sigantry_core.protocols.DeployProfile`
assertions against the in-memory double ``FakeDeployProfile``.

Every test is marked with ``@pytest.mark.contract`` per PROD-18 convention.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    DeployContext,
    DeployPlan,
    DeployProfile,
)
from sigantry_core.testing.doubles import FakeDeployProfile


@pytest.mark.contract
def test_deploy_profile_double_has_name(fdt_deploy_profile_contract) -> None:
    fdt_deploy_profile_contract(FakeDeployProfile())


@pytest.mark.contract
def test_deploy_profile_double_plan_returns_deploy_plan() -> None:
    profile = FakeDeployProfile(plan_fn=lambda _ctx: DeployPlan(actions=[{"kind": "noop"}]))
    ctx = DeployContext(workspace_id="ws", environment="test", parameters={})
    plan = profile.plan(ctx)
    assert isinstance(plan, DeployPlan)
    assert plan.actions == [{"kind": "noop"}]


@pytest.mark.contract
def test_deploy_profile_double_satisfies_runtime_protocol() -> None:
    assert isinstance(FakeDeployProfile(), DeployProfile)
