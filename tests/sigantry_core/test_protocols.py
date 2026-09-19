"""Structural tests for :mod:`sigantry_core.protocols` (PROD-01).

YAGNI guardrails (do NOT re-open):

- No protocol carries an ``api_version`` field (deferred to v2.1 per
  PRODUCTIZATION.md Section 10.1).
- Each protocol carries exactly ``name: str`` as a class-var annotation.
- Supporting dataclasses are frozen + slotted.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import get_type_hints

import pytest

from sigantry_core import protocols as p_mod

PROTOCOL_NAMES = [
    "DeployProfile",
    "DataQualityGate",
    "TelemetrySink",
    "AuthProvider",
    "RunbookRegistry",
    "CapacityPolicy",
]

SUPPORTING_DATACLASSES = [
    "DeployContext",
    "DeployPlan",
    "DeployResult",
    "DataRef",
    "GateResult",
    "TelemetryEvent",
    "Secret",
    "CapacityContext",
    "CapacityAction",
    "CapacityApplyResult",
]


def _protocols() -> list[type]:
    return [getattr(p_mod, n) for n in PROTOCOL_NAMES]


def test_six_protocols_exported() -> None:
    """Exactly six protocols are exported under their canonical names."""
    for n in PROTOCOL_NAMES:
        assert hasattr(p_mod, n), f"{n} not exported from sigantry_core.protocols"
    assert set(PROTOCOL_NAMES).issubset(set(p_mod.__all__))


def test_no_protocol_has_api_version() -> None:
    """YAGNI guard: no protocol carries ``api_version`` (deferred to v2.1)."""
    for cls in _protocols():
        hints = get_type_hints(cls)
        assert "api_version" not in hints, (
            f"{cls.__name__} must NOT declare api_version at v2.0 "
            "(YAGNI §10.1 -- deferred to v2.1)."
        )


def test_every_protocol_has_name_field() -> None:
    """Each protocol must declare ``name: str`` (plugin identifier)."""
    for cls in _protocols():
        hints = get_type_hints(cls)
        assert "name" in hints, f"{cls.__name__} missing name annotation"
        assert hints["name"] is str, (
            f"{cls.__name__}.name must be annotated as str, got {hints['name']!r}"
        )


def test_deploy_profile_is_runtime_checkable() -> None:
    """``isinstance`` against ``DeployProfile`` succeeds for a conformant stub."""

    class StubDeploy:
        name = "stub"

        def plan(self, ctx):
            return p_mod.DeployPlan(actions=[])

        def apply(self, ctx, plan):
            return p_mod.DeployResult(
                workspace_id=ctx.workspace_id, items_published=0, items_failed=0
            )

    assert isinstance(StubDeploy(), p_mod.DeployProfile)


def test_data_quality_gate_is_runtime_checkable() -> None:
    class StubGate:
        name = "stub"

        def run(self, suite, data_ref):
            return p_mod.GateResult(
                suite=suite, success=True, violations=0, evaluated=0, run_id="x"
            )

    assert isinstance(StubGate(), p_mod.DataQualityGate)


def test_telemetry_sink_is_runtime_checkable() -> None:
    class StubSink:
        name = "stub"

        def emit(self, event):
            return None

        def flush(self, timeout_s=5.0):
            return None

    assert isinstance(StubSink(), p_mod.TelemetrySink)


def test_auth_provider_is_runtime_checkable() -> None:
    class StubAuth:
        name = "stub"

        def get_token(self, scope, *, tenant_id=None):
            return p_mod.Secret(value="x")

    assert isinstance(StubAuth(), p_mod.AuthProvider)


def test_runbook_registry_is_runtime_checkable() -> None:
    class StubRegistry:
        name = "stub"

        def resolve(self, alert_name):
            return None

    assert isinstance(StubRegistry(), p_mod.RunbookRegistry)


def test_capacity_policy_is_runtime_checkable_and_symmetric() -> None:
    """``CapacityPolicy`` must expose the ``plan``/``apply`` pair (D-04)."""

    class StubPolicy:
        name = "stub"

        def plan(self, ctx):
            return []

        def apply(self, ctx, actions):
            return p_mod.CapacityApplyResult(applied=0, skipped=0)

    stub = StubPolicy()
    assert isinstance(stub, p_mod.CapacityPolicy)
    ctx = p_mod.CapacityContext(capacity_id="cap", tenant_id="t")
    assert stub.plan(ctx) == []
    assert stub.apply(ctx, []).applied == 0


@pytest.mark.parametrize("dc_name", SUPPORTING_DATACLASSES)
def test_supporting_dataclasses_are_frozen(dc_name: str) -> None:
    cls = getattr(p_mod, dc_name)
    assert is_dataclass(cls), f"{dc_name} is not a dataclass"
    assert cls.__dataclass_params__.frozen, f"{dc_name} must be frozen=True"


@pytest.mark.parametrize("dc_name", SUPPORTING_DATACLASSES)
def test_supporting_dataclasses_are_slotted(dc_name: str) -> None:
    cls = getattr(p_mod, dc_name)
    # slots=True => __slots__ is set on the class body.
    assert "__slots__" in cls.__dict__, f"{dc_name} must be slots=True"


def test_deploy_context_exposes_expected_fields() -> None:
    names = {f.name for f in fields(p_mod.DeployContext)}
    assert names == {"workspace_id", "environment", "parameters"}


def test_gate_result_exposes_expected_fields() -> None:
    names = {f.name for f in fields(p_mod.GateResult)}
    assert names == {"suite", "success", "violations", "evaluated", "run_id"}


# ---------------------------------------------------------------------------
# Secret scrubbing — regression guard for 08.1 P0 security fix.
# A Secret that prints its token is a direct exfiltration path via exception
# messages and telemetry properties dicts.
# ---------------------------------------------------------------------------


def test_secret_repr_does_not_leak_token() -> None:
    """``repr(Secret(...))`` must NOT contain the token bytes."""
    token = "eyJ0eXA.sensitive-token.payload.redact-me"
    s = p_mod.Secret(value=token)
    rendered = repr(s)
    assert token not in rendered, (
        f"Secret.__repr__ leaked the token: {rendered!r} -- this would expose "
        "credentials via exception messages and telemetry properties."
    )
    assert "***" in rendered


def test_secret_str_does_not_leak_token() -> None:
    """``str(Secret(...))`` must NOT contain the token bytes either."""
    token = "eyJ0eXA.another-token"
    s = p_mod.Secret(value=token)
    assert token not in str(s)


def test_secret_value_still_accessible() -> None:
    """Scrubbing only applies to string rendering; ``.value`` still works."""
    token = "eyJ0eXA.real-token"
    s = p_mod.Secret(value=token)
    assert s.value == token


def test_secret_formatted_in_exception_does_not_leak() -> None:
    """A Secret baked into an exception message is scrubbed via __str__."""
    token = "eyJ0eXA.token-leaked-through-exception"
    s = p_mod.Secret(value=token)
    try:
        raise RuntimeError(f"auth failed with {s}")
    except RuntimeError as exc:
        assert token not in str(exc)


def test_secret_formatted_in_dict_does_not_leak_via_repr() -> None:
    """A Secret inside a dict rendered via repr is scrubbed (telemetry path)."""
    token = "eyJ0eXA.token-leaked-via-telemetry-dict"
    s = p_mod.Secret(value=token)
    properties = {"auth_token": s}
    assert token not in repr(properties)


# ---------------------------------------------------------------------------
# Phase 11 (TRACE-01) -- seventh runtime_checkable Protocol seam.
# ``WorkItemProvider`` joins the six v2.0 seams in v3.0. Per planner-mapper
# resolution, it preserves the api_version-omission symmetry; cross-seam
# widening is a v3.1 candidate.
# ---------------------------------------------------------------------------


class TestWorkItemProvider:
    """Phase 11 (TRACE-01) -- seventh runtime_checkable Protocol seam."""

    def test_work_item_provider_in_dunder_all(self) -> None:
        from sigantry_core.protocols import __all__

        assert "WorkItemProvider" in __all__
        assert "WorkItem" in __all__

    def test_work_item_provider_satisfies_runtime_checkable(self) -> None:
        from sigantry_core.protocols import WorkItemProvider

        class _Fake:
            name = "fake"

            def link_release(self, release_id, work_items, deploy_record):
                return None

            def fetch_work_items(self, ids):
                return []

            def ping(self):
                return None

        assert isinstance(_Fake(), WorkItemProvider)

    def test_work_item_provider_omits_api_version_for_cross_seam_symmetry(self) -> None:
        """Per planner-mapper resolution: api_version is deferred to v3.1
        cross-seam widening so all seven seams gain the field together.
        """
        from sigantry_core.protocols import WorkItemProvider

        # Protocol class itself does not declare api_version -- consistent
        # with DeployProfile, DataQualityGate, TelemetrySink, AuthProvider,
        # RunbookRegistry, CapacityPolicy.
        assert "api_version" not in get_type_hints(WorkItemProvider)

    def test_work_item_value_object_is_frozen_slots_dataclass(self) -> None:
        import dataclasses

        from sigantry_core.protocols import WorkItem

        assert dataclasses.is_dataclass(WorkItem)
        assert WorkItem.__dataclass_params__.frozen
        assert hasattr(WorkItem, "__slots__")
