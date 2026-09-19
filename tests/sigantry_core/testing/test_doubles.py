"""Tests for :mod:`sigantry_core.testing.doubles` (PROD-04)."""

from __future__ import annotations

from datetime import UTC, datetime

from sigantry_core.protocols import (
    AuthProvider,
    CapacityContext,
    CapacityPolicy,
    DataQualityGate,
    DataRef,
    DeployContext,
    DeployPlan,
    DeployProfile,
    RunbookRegistry,
    TelemetryEvent,
    TelemetrySink,
    WorkItem,
    WorkItemProvider,
)
from sigantry_core.release.record import DeployRecord
from sigantry_core.testing import (
    FakeAuth,
    FakeDeployProfile,
    InMemoryTelemetrySink,
    NoopCapacityPolicy,
    NoopGate,
    StaticRunbookRegistry,
)
from sigantry_core.testing.doubles import FakeWorkItemProvider

# -- InMemoryTelemetrySink -------------------------------------------------


def test_in_memory_telemetry_sink_records() -> None:
    sink = InMemoryTelemetrySink()
    event_a = TelemetryEvent(name="a", properties={"k": 1}, timestamp=datetime.now())
    event_b = TelemetryEvent(name="b", properties={}, timestamp=datetime.now())

    sink.emit(event_a)
    sink.emit(event_b)

    assert sink.events == [event_a, event_b]


def test_in_memory_telemetry_sink_flush_is_noop_but_counts() -> None:
    sink = InMemoryTelemetrySink()
    assert sink.flushed == 0
    sink.flush()
    sink.flush(timeout_s=2.0)
    assert sink.flushed == 2


# -- NoopGate --------------------------------------------------------------


def test_noop_gate_returns_success() -> None:
    gate = NoopGate()
    result = gate.run("suite-1", DataRef(name="tbl", path="/p"))
    assert result.success is True
    assert result.suite == "suite-1"
    assert result.violations == 0
    assert result.run_id == "noop"


# -- FakeAuth --------------------------------------------------------------


def test_fake_auth_returns_static_secret() -> None:
    auth = FakeAuth(token="abc123")
    secret = auth.get_token("https://api.fabric.microsoft.com/.default")
    assert secret.value == "abc123"


def test_fake_auth_default_token() -> None:
    auth = FakeAuth()
    assert auth.get_token("scope").value == "fake-token"


# -- StaticRunbookRegistry -------------------------------------------------


def test_static_runbook_registry_resolves() -> None:
    reg = StaticRunbookRegistry({"CapacityCU": "https://runbooks/cu"})
    assert reg.resolve("CapacityCU") == "https://runbooks/cu"


def test_static_runbook_registry_returns_none_for_unknown() -> None:
    reg = StaticRunbookRegistry({"known": "url"})
    assert reg.resolve("unknown") is None


def test_static_runbook_registry_handles_empty_default() -> None:
    reg = StaticRunbookRegistry()
    assert reg.resolve("anything") is None


# -- NoopCapacityPolicy ----------------------------------------------------


def test_noop_capacity_policy_plan_is_empty() -> None:
    pol = NoopCapacityPolicy()
    ctx = CapacityContext(capacity_id="cap-1", tenant_id="t-1")
    assert pol.plan(ctx) == []


def test_noop_capacity_policy_apply_reports_zero_applied() -> None:
    pol = NoopCapacityPolicy()
    ctx = CapacityContext(capacity_id="cap-1", tenant_id="t-1")
    result = pol.apply(ctx, [])
    assert result.applied == 0
    assert result.skipped == 0
    assert result.errors == []


# -- FakeDeployProfile -----------------------------------------------------


def test_fake_deploy_profile_default_empty_plan() -> None:
    profile = FakeDeployProfile()
    ctx = DeployContext(workspace_id="ws-1", environment="dev", parameters={})
    plan = profile.plan(ctx)
    assert plan.actions == []

    result = profile.apply(ctx, plan)
    assert result.workspace_id == "ws-1"
    assert result.items_published == 0
    assert result.items_failed == 0


def test_fake_deploy_profile_honours_plan_fn() -> None:
    planned = DeployPlan(actions=[{"kind": "publish", "item": "x"}])
    profile = FakeDeployProfile(plan_fn=lambda _ctx: planned)

    ctx = DeployContext(workspace_id="ws-2", environment="prod", parameters={})
    plan = profile.plan(ctx)
    assert plan is planned

    result = profile.apply(ctx, plan)
    assert result.items_published == 1
    assert profile.apply_calls == [{"ctx": ctx, "plan": plan}]


# -- FakeWorkItemProvider --------------------------------------------------


def _record_for_test() -> DeployRecord:
    return DeployRecord(
        workspace="ws-test",
        release_id="R-1",
        work_items=["1"],
        approver="alice@example.invalid",
        audit_hash="",
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    ).with_hash()


def test_fake_work_item_provider_has_name_and_default_state() -> None:
    p = FakeWorkItemProvider()
    assert p.name == "fake"
    assert p.pinged == 0
    assert p.fetch_calls == []
    assert p.fetch_returns == []
    assert p.linked == []


def test_fake_work_item_provider_ping_increments_counter() -> None:
    p = FakeWorkItemProvider()
    p.ping()
    p.ping()
    assert p.pinged == 2


def test_fake_work_item_provider_fetch_records_calls_and_returns_seed() -> None:
    seed = [
        WorkItem(
            id="1",
            title="t",
            work_item_type="Bug",
            state="Active",
            assigned_to=None,
            provider_name="fake",
        )
    ]
    p = FakeWorkItemProvider(fetch_returns=seed)
    out = p.fetch_work_items(["1", "2"])
    assert p.fetch_calls == [["1", "2"]]
    # Returns a copy of the seed list (not the same object).
    assert out == seed
    assert out is not seed


def test_fake_work_item_provider_link_release_records_args() -> None:
    record = _record_for_test()
    p = FakeWorkItemProvider()
    p.link_release("R-1", ["1", "2"], record)
    assert len(p.linked) == 1
    rid, ids, rec = p.linked[0]
    assert rid == "R-1"
    assert ids == ["1", "2"]
    assert rec.audit_hash == record.audit_hash


def test_fake_work_item_provider_satisfies_runtime_protocol() -> None:
    assert isinstance(FakeWorkItemProvider(), WorkItemProvider)


# -- Protocol conformance --------------------------------------------------


def test_doubles_satisfy_protocols() -> None:
    """Every double is recognised by its matching ``runtime_checkable`` protocol."""
    assert isinstance(InMemoryTelemetrySink(), TelemetrySink)
    assert isinstance(NoopGate(), DataQualityGate)
    assert isinstance(FakeAuth(), AuthProvider)
    assert isinstance(StaticRunbookRegistry({}), RunbookRegistry)
    assert isinstance(NoopCapacityPolicy(), CapacityPolicy)
    assert isinstance(FakeDeployProfile(), DeployProfile)
    assert isinstance(FakeWorkItemProvider(), WorkItemProvider)
