"""Contract-test pytest fixture plugin (PRODUCTIZATION.md Section 10.1 row 3).

Every plugin package is expected to run the seven per-seam contract tests in
``tests/contract/`` against its own implementations. Rather than ship a
separate ``fabric-dataops-tests-adapter`` distribution, the base package
exposes a pytest plugin module that is auto-loaded via the ``pytest11``
entry-point group (see base ``pyproject.toml``).

Consumers install the base package once, then their contract tests pick
up the fixtures below transparently:

    from sigantry_core.testing.doubles import FakeDeployProfile
    import pytest

    @pytest.mark.contract
    def test_deploy_profile_has_name(fdt_deploy_profile_contract):
        fdt_deploy_profile_contract(FakeDeployProfile)

Three kinds of fixtures are exposed:

* **Instance fixtures** (``fdt_registry``, ``fdt_in_memory_sink`` etc.)
  hand back a fresh double usable in one assertion.
* **Parametrisable contract helpers** (``fdt_deploy_profile_contract``
  and peers) are callables that take a ``profile_cls_or_instance`` and run
  the three standard protocol assertions for that seam.
* **Settings TOML fixture** (``fdt_settings_toml``) stages a tmp-path
  ``.fabric-dataops.toml`` with configurable plugin sections for tests
  that exercise ``FabricDataOps.from_config`` end-to-end.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sigantry_core.protocols import (
    AuthProvider,
    CapacityAction,
    CapacityApplyResult,
    CapacityContext,
    CapacityPolicy,
    DataQualityGate,
    DataRef,
    DeployContext,
    DeployPlan,
    DeployProfile,
    DeployResult,
    GateResult,
    RunbookRegistry,
    Secret,
    TelemetryEvent,
    TelemetrySink,
    WorkItemProvider,
)
from sigantry_core.registry import Registry
from sigantry_core.testing.doubles import (
    FakeAuth,
    FakeDeployProfile,
    FakeWorkItemProvider,
    InMemoryTelemetrySink,
    NoopCapacityPolicy,
    NoopGate,
    StaticRunbookRegistry,
)

# ---------------------------------------------------------------------------
# Instance fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fdt_registry() -> Registry:
    """Return a fresh, empty plugin ``Registry``.

    Unlike :func:`sigantry_core.registry.default_registry`, this
    fixture does NOT trigger entry-point discovery; tests can call
    ``.discover()`` explicitly or register plugins by hand.
    """
    return Registry()


@pytest.fixture
def fdt_in_memory_sink() -> InMemoryTelemetrySink:
    """Return a fresh :class:`InMemoryTelemetrySink` with an empty events list."""
    return InMemoryTelemetrySink()


@pytest.fixture
def fdt_noop_gate() -> NoopGate:
    """Return a fresh :class:`NoopGate` (always-success DQ gate)."""
    return NoopGate()


@pytest.fixture
def fdt_fake_auth() -> FakeAuth:
    """Return a fresh :class:`FakeAuth` handing out a static ``Secret``."""
    return FakeAuth()


@pytest.fixture
def fdt_static_runbooks() -> StaticRunbookRegistry:
    """Return an empty :class:`StaticRunbookRegistry`."""
    return StaticRunbookRegistry({})


@pytest.fixture
def fdt_noop_capacity() -> NoopCapacityPolicy:
    """Return a fresh :class:`NoopCapacityPolicy`."""
    return NoopCapacityPolicy()


@pytest.fixture
def fdt_fake_deploy_profile() -> FakeDeployProfile:
    """Return a :class:`FakeDeployProfile` producing a single-action plan."""
    return FakeDeployProfile(plan_fn=lambda _ctx: DeployPlan(actions=[{"kind": "placeholder"}]))


@pytest.fixture
def fdt_fake_work_item_provider() -> FakeWorkItemProvider:
    """Return a fresh :class:`FakeWorkItemProvider` (Phase 11 seventh seam)."""
    return FakeWorkItemProvider()


# ---------------------------------------------------------------------------
# Settings TOML fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def fdt_settings_toml(tmp_path: Path) -> Callable[..., Path]:
    """Factory fixture: write a ``.fabric-dataops.toml`` at ``tmp_path``.

    Usage::

        def test_from_config(fdt_settings_toml):
            cfg = fdt_settings_toml(
                core={"tenant_id": "t1"},
                telemetry={"sink": "log_analytics"},
            )
            # cfg is a Path to the written file.
    """

    def _write(**sections: dict[str, Any]) -> Path:
        core = dict(sections.pop("core", {"tenant_id": "test-tenant"}))
        lines: list[str] = ["[core]"]
        for k, v in core.items():
            lines.append(f'{k} = "{v}"')
        for section_name, body in sections.items():
            lines.append("")
            lines.append(f"[{section_name}]")
            for k, v in (body or {}).items():
                if isinstance(v, dict):
                    continue
                lines.append(f'{k} = "{v}"')
            # Nested namespaced tables (e.g. telemetry.log_analytics) pass
            # through untouched for plugin-owned validation.
            for k, v in (body or {}).items():
                if isinstance(v, dict):
                    lines.append("")
                    lines.append(f"[{section_name}.{k}]")
                    for sub_k, sub_v in v.items():
                        lines.append(f'{sub_k} = "{sub_v}"')

        path = tmp_path / ".fabric-dataops.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Round-trip validates the produced TOML parses cleanly.
        tomllib.loads(path.read_text(encoding="utf-8"))
        return path

    return _write


# ---------------------------------------------------------------------------
# Parametrisable contract helpers
# ---------------------------------------------------------------------------
#
# Each helper returns a callable. The callable accepts either a plugin CLASS
# (which it instantiates with no args) or a plugin INSTANCE and runs the
# three standard protocol assertions for the seam. Plugin authors call these
# in their own pytest suite to opt into the contract battery.


def _as_instance(cls_or_instance: Any) -> Any:
    if isinstance(cls_or_instance, type):
        return cls_or_instance()
    return cls_or_instance


@pytest.fixture
def fdt_deploy_profile_contract() -> Callable[[Any], None]:
    """Return a callable asserting the three DeployProfile contract rules."""

    def _run(profile_cls_or_instance: Any) -> None:
        profile = _as_instance(profile_cls_or_instance)
        # (1) name is a non-empty string.
        assert isinstance(profile.name, str) and profile.name, (
            "DeployProfile.name must be a non-empty string"
        )
        # (2) plan(ctx) returns DeployPlan.
        ctx = DeployContext(workspace_id="contract-ws", environment="test", parameters={})
        plan = profile.plan(ctx)
        assert isinstance(plan, DeployPlan), (
            f"DeployProfile.plan must return DeployPlan, got {type(plan).__name__}"
        )
        # (3) protocol conformance (runtime-checkable Protocol).
        assert isinstance(profile, DeployProfile), (
            "Plugin does not satisfy the DeployProfile runtime protocol"
        )

    return _run


@pytest.fixture
def fdt_dq_gate_contract() -> Callable[[Any], None]:
    """Return a callable asserting the three DataQualityGate contract rules."""

    def _run(gate_cls_or_instance: Any) -> None:
        gate = _as_instance(gate_cls_or_instance)
        assert isinstance(gate.name, str) and gate.name, (
            "DataQualityGate.name must be a non-empty string"
        )
        assert isinstance(gate, DataQualityGate), (
            "Plugin does not satisfy the DataQualityGate runtime protocol"
        )
        assert callable(getattr(gate, "run", None)), "DataQualityGate.run must be callable"

    return _run


@pytest.fixture
def fdt_telemetry_sink_contract() -> Callable[[Any], None]:
    """Return a callable asserting the three TelemetrySink contract rules."""

    def _run(sink_cls_or_instance: Any) -> None:
        sink = _as_instance(sink_cls_or_instance)
        assert isinstance(sink.name, str) and sink.name, (
            "TelemetrySink.name must be a non-empty string"
        )
        assert isinstance(sink, TelemetrySink), (
            "Plugin does not satisfy the TelemetrySink runtime protocol"
        )
        # emit + flush must be callable. We do NOT invoke them here (sinks may
        # require network / credentials). Callers can drive them via narrower
        # tests using ``fdt_in_memory_sink`` for the safe-by-default path.
        assert callable(sink.emit), "TelemetrySink.emit must be callable"
        assert callable(sink.flush), "TelemetrySink.flush must be callable"

    return _run


@pytest.fixture
def fdt_auth_provider_contract() -> Callable[[Any], None]:
    """Return a callable asserting the three AuthProvider contract rules."""

    def _run(auth_cls_or_instance: Any) -> None:
        provider = _as_instance(auth_cls_or_instance)
        assert isinstance(provider.name, str) and provider.name, (
            "AuthProvider.name must be a non-empty string"
        )
        assert isinstance(provider, AuthProvider), (
            "Plugin does not satisfy the AuthProvider runtime protocol"
        )
        assert callable(provider.get_token), "AuthProvider.get_token must be callable"

    return _run


@pytest.fixture
def fdt_runbook_registry_contract() -> Callable[[Any], None]:
    """Return a callable asserting the three RunbookRegistry contract rules."""

    def _run(registry_cls_or_instance: Any) -> None:
        registry = _as_instance(registry_cls_or_instance)
        assert isinstance(registry.name, str) and registry.name, (
            "RunbookRegistry.name must be a non-empty string"
        )
        assert isinstance(registry, RunbookRegistry), (
            "Plugin does not satisfy the RunbookRegistry runtime protocol"
        )
        # Unknown alerts return None per the protocol contract.
        result = registry.resolve("__contract_unknown_alert__")
        assert result is None or isinstance(result, str), (
            "RunbookRegistry.resolve must return str or None"
        )

    return _run


@pytest.fixture
def fdt_capacity_policy_contract() -> Callable[[Any], None]:
    """Return a callable asserting the three CapacityPolicy contract rules."""

    def _run(policy_cls_or_instance: Any) -> None:
        policy = _as_instance(policy_cls_or_instance)
        assert isinstance(policy.name, str) and policy.name, (
            "CapacityPolicy.name must be a non-empty string"
        )
        assert isinstance(policy, CapacityPolicy), (
            "Plugin does not satisfy the CapacityPolicy runtime protocol"
        )
        ctx = CapacityContext(capacity_id="contract-cap", tenant_id="contract-tenant")
        actions = policy.plan(ctx)
        assert isinstance(actions, list), "CapacityPolicy.plan must return a list"
        for action in actions:
            assert isinstance(action, CapacityAction), (
                "CapacityPolicy.plan list entries must be CapacityAction"
            )

    return _run


@pytest.fixture
def fdt_work_item_provider_contract() -> Callable[[Any], None]:
    """Return a callable asserting the WorkItemProvider contract rules.

    Usage::

        def test_my_provider(fdt_work_item_provider_contract):
            fdt_work_item_provider_contract(MyProvider(...))

    Asserts:
      - ``provider.name`` exists and is a non-empty str
      - ``isinstance(provider, WorkItemProvider)`` (runtime_checkable)
      - the three Protocol methods (``ping``, ``fetch_work_items``,
        ``link_release``) exist and ``link_release`` accepts the
        ``release_id`` / ``work_items`` / ``deploy_record`` parameters
      - NO ``api_version`` attribute is declared at the class level
        (planner-mapper resolution: cross-seam widening deferred to v3.1
        so all seven seams gain ``api_version`` together).

    This fixture does NOT exercise live HTTP -- pass a mocked provider
    OR the :class:`FakeWorkItemProvider` double from
    :mod:`sigantry_core.testing.doubles`. Plugin authors verifying their
    own ``WorkItemProvider`` impl in unit tests should instantiate it
    with mocked transport (e.g. ``respx``) before passing it in.
    """
    import inspect

    def _run(provider_cls_or_instance: Any) -> None:
        provider = _as_instance(provider_cls_or_instance)
        # (1) name is a non-empty string.
        assert isinstance(provider.name, str) and provider.name, (
            "WorkItemProvider.name must be a non-empty string"
        )
        # (2) runtime-checkable Protocol membership.
        assert isinstance(provider, WorkItemProvider), (
            "Plugin does not satisfy the WorkItemProvider runtime protocol"
        )
        # (3) the three Protocol method names exist + are callable.
        assert callable(getattr(provider, "ping", None)), "WorkItemProvider.ping must be callable"
        assert callable(getattr(provider, "fetch_work_items", None)), (
            "WorkItemProvider.fetch_work_items must be callable"
        )
        assert callable(getattr(provider, "link_release", None)), (
            "WorkItemProvider.link_release must be callable"
        )
        # (4) link_release signature -- the cross-provider Protocol surface.
        link_sig = inspect.signature(provider.link_release)
        link_params = set(link_sig.parameters.keys())
        assert {"release_id", "work_items", "deploy_record"}.issubset(link_params), (
            "WorkItemProvider.link_release must accept release_id, "
            "work_items, deploy_record (got "
            f"{sorted(link_params)})"
        )
        # (5) NO api_version attribute -- planner-mapper resolution.
        # The asymmetry with future v3.1 cross-seam widening is documented
        # in protocols.py module docstring and ADR-0004. Adding api_version
        # to ANY single seam without updating all seven is a contract break.
        assert not hasattr(type(provider), "api_version") or not isinstance(
            getattr(type(provider), "api_version", None), str
        ), (
            "WorkItemProvider implementations MUST NOT declare api_version "
            "as a class attribute in v3.0 -- cross-seam widening is "
            "deferred to v3.1. See protocols.py module docstring."
        )

    return _run


__all__ = [
    "fdt_auth_provider_contract",
    "fdt_capacity_policy_contract",
    "fdt_deploy_profile_contract",
    "fdt_dq_gate_contract",
    "fdt_fake_auth",
    "fdt_fake_deploy_profile",
    "fdt_fake_work_item_provider",
    "fdt_in_memory_sink",
    "fdt_noop_capacity",
    "fdt_noop_gate",
    "fdt_registry",
    "fdt_runbook_registry_contract",
    "fdt_settings_toml",
    "fdt_static_runbooks",
    "fdt_telemetry_sink_contract",
    "fdt_work_item_provider_contract",
]


# Re-export supporting symbols so plugin authors can import them from the
# fixture module in a single line.
_SUPPORT_TYPES = (
    CapacityApplyResult,
    DataRef,
    DeployResult,
    GateResult,
    Secret,
    TelemetryEvent,
)
