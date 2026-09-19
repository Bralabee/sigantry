"""Unit tests for :mod:`sigantry_core.deploy.orchestrator`.

PROD-07 invariant: ``deploy`` resolves a :class:`DeployProfile` via the
plugin registry and runs its ``plan`` + ``apply`` pipeline — nothing more.
"""

from __future__ import annotations

import pytest

from sigantry_core.deploy.orchestrator import deploy
from sigantry_core.protocols import (
    DeployContext,
    DeployPlan,
    DeployResult,
)
from sigantry_core.registry import Registry
from sigantry_core.testing.doubles import FakeDeployProfile


def _ctx() -> DeployContext:
    return DeployContext(
        workspace_id="ws-123",
        environment="DEV",
        parameters={"foo": "bar"},
    )


def test_deploy_with_injected_profile_returns_result() -> None:
    """Direct DI: FakeDeployProfile's plan + apply chain run through."""
    profile = FakeDeployProfile(
        plan_fn=lambda _ctx: DeployPlan(actions=[{"op": "publish", "id": "a"}])
    )

    result = deploy(_ctx(), profile=profile)

    assert isinstance(result, DeployResult)
    assert result.workspace_id == "ws-123"
    assert result.items_published == 1
    assert result.items_failed == 0
    assert len(profile.apply_calls) == 1
    assert profile.apply_calls[0]["ctx"] == _ctx()


def test_deploy_resolves_via_registry() -> None:
    """``profile_name`` drives a registry lookup + class instantiation."""
    reg = Registry()
    reg.register("sigantry.deploy_profiles", "fake", FakeDeployProfile)

    result = deploy(_ctx(), profile_name="fake", registry=reg)
    assert isinstance(result, DeployResult)
    assert result.workspace_id == "ws-123"


def test_deploy_resolves_instance_factories() -> None:
    """Already-instantiated profiles are used as-is."""
    pre_built = FakeDeployProfile()
    reg = Registry()
    reg.register("sigantry.deploy_profiles", "fake", pre_built)

    result = deploy(_ctx(), profile_name="fake", registry=reg)
    assert isinstance(result, DeployResult)
    assert len(pre_built.apply_calls) == 1


def test_deploy_missing_both_raises_value_error() -> None:
    """Missing profile + profile_name -> ValueError naming the wiring."""
    with pytest.raises(ValueError, match="profile_name"):
        deploy(_ctx())


def test_deploy_unknown_name_raises_key_error() -> None:
    """Unregistered profile name -> KeyError mentioning the name."""
    reg = Registry()
    with pytest.raises(KeyError, match="nope"):
        deploy(_ctx(), profile_name="nope", registry=reg)


class _BoomPlanProfile:
    name = "boom-plan"

    def plan(self, ctx: DeployContext) -> DeployPlan:
        raise RuntimeError("plan failed")

    def apply(self, ctx: DeployContext, plan: DeployPlan) -> DeployResult:
        raise AssertionError("apply should not be called when plan raises")


def test_deploy_plan_failure_propagates_without_apply() -> None:
    """A ``plan()`` exception propagates and ``apply`` is never invoked."""
    with pytest.raises(RuntimeError, match="plan failed"):
        deploy(_ctx(), profile=_BoomPlanProfile())


class _BoomApplyProfile:
    name = "boom-apply"

    def plan(self, ctx: DeployContext) -> DeployPlan:
        return DeployPlan(actions=[{"op": "publish"}])

    def apply(self, ctx: DeployContext, plan: DeployPlan) -> DeployResult:
        raise RuntimeError("apply failed")


def test_deploy_apply_failure_propagates() -> None:
    """An ``apply()`` exception propagates verbatim."""
    with pytest.raises(RuntimeError, match="apply failed"):
        deploy(_ctx(), profile=_BoomApplyProfile())
