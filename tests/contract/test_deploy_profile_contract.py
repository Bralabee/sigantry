"""DeployProfile contract tests.

Runs the three standard :class:`~sigantry_core.protocols.DeployProfile`
assertions against:

(a) the in-memory double ``FakeDeployProfile`` (always available), and
(b) the plugin's ``AimsDeployProfile`` (if the HS2 plugin is installed).

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


def _plugin_profile_or_skip():
    plugin_pkg = pytest.importorskip("sigantry_hs2")
    from sigantry_hs2.deploy.aims_profile import (
        AimsDeployProfile,
    )

    # Touch the import-skip pkg to keep it referenced (linter quieter).
    assert plugin_pkg is not None
    return AimsDeployProfile()


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


@pytest.mark.contract
def test_deploy_profile_plugin_has_name(fdt_deploy_profile_contract) -> None:
    profile = _plugin_profile_or_skip()
    fdt_deploy_profile_contract(profile)


@pytest.mark.contract
def test_deploy_profile_plugin_plan_returns_deploy_plan() -> None:
    profile = _plugin_profile_or_skip()
    ctx = DeployContext(
        workspace_id="plugin-ws",
        environment="test",
        parameters={"wheel_path": "dist/sample.whl", "environment_id": "env-1"},
    )
    plan = profile.plan(ctx)
    assert isinstance(plan, DeployPlan)
    assert len(plan.actions) >= 1


@pytest.mark.contract
def test_deploy_profile_plugin_satisfies_runtime_protocol() -> None:
    profile = _plugin_profile_or_skip()
    assert isinstance(profile, DeployProfile)
