"""Tests for :mod:`sigantry_core.api` (PROD-01..08 glue)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigantry_core.api import FabricDataOps
from sigantry_core.protocols import (
    DataRef,
    DeployContext,
    DeployPlan,
    DeployResult,
    GateResult,
)
from sigantry_core.registry import Registry
from sigantry_core.testing.doubles import (
    FakeDeployProfile,
    InMemoryTelemetrySink,
    NoopGate,
)

SAMPLE_TOML_MIN = """\
[core]
tenant_id = "t-1"
"""

SAMPLE_TOML_NAMED = """\
[core]
tenant_id = "t-1"

[telemetry]
sink = "in_memory"
"""

SAMPLE_TOML_MISSING = """\
[core]
tenant_id = "t-1"

[deploy]
profile = "nonexistent-profile"
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".fabric-dataops.toml"
    p.write_text(body, encoding="utf-8")
    return p


class _FakeTelemetry:
    name = "in_memory"

    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event):
        self.events.append(event)

    def flush(self, timeout_s: float = 5.0):
        return None


def test_fabric_dataops_direct_di() -> None:
    """Direct kwarg DI stashes the seam instances on the front-door object."""
    sink = _FakeTelemetry()
    fdo = FabricDataOps(telemetry=sink)
    assert fdo.telemetry is sink
    assert fdo.auth is None
    assert fdo.dq_gate is None
    assert fdo.deploy_profile is None
    assert fdo.runbooks is None
    assert fdo.capacity is None


def test_fabric_dataops_from_config_empty_registry(tmp_path: Path) -> None:
    """An empty registry + minimal TOML yields an instance with ``None`` plugins."""
    path = _write(tmp_path, SAMPLE_TOML_MIN)
    fdo = FabricDataOps.from_config(path, registry=Registry())
    assert fdo.settings is not None
    assert fdo.settings.core.tenant_id == "t-1"
    assert fdo.telemetry is None
    assert fdo.auth is None
    assert fdo.dq_gate is None
    assert fdo.deploy_profile is None
    assert fdo.runbooks is None
    assert fdo.capacity is None


def test_fabric_dataops_from_config_resolves_named_plugin(tmp_path: Path) -> None:
    """A named plugin (``telemetry.sink = "in_memory"``) is resolved and instantiated."""
    path = _write(tmp_path, SAMPLE_TOML_NAMED)
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "in_memory", _FakeTelemetry)

    fdo = FabricDataOps.from_config(path, registry=reg)
    assert isinstance(fdo.telemetry, _FakeTelemetry)
    assert fdo.auth is None


def test_fabric_dataops_from_config_missing_plugin_raises(tmp_path: Path) -> None:
    """Naming a plugin that is not registered raises ``KeyError``."""
    path = _write(tmp_path, SAMPLE_TOML_MISSING)
    with pytest.raises(KeyError, match="nonexistent-profile"):
        FabricDataOps.from_config(path, registry=Registry())


def test_fabric_dataops_from_config_accepts_instance_factories(
    tmp_path: Path,
) -> None:
    """If the registered impl is an instance (not a class), it is used as-is."""
    path = _write(tmp_path, SAMPLE_TOML_NAMED)
    pre_built = _FakeTelemetry()
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "in_memory", pre_built)

    fdo = FabricDataOps.from_config(path, registry=reg)
    assert fdo.telemetry is pre_built


# ---------------------------------------------------------------------------
# Plugin config passthrough (bug-fix: LogAnalyticsSink-style plugins that need
# constructor args cannot be resolved from a config-driven path otherwise)
# ---------------------------------------------------------------------------


class _SinkWithRequiredArgs:
    name = "needs_args"

    def __init__(self, *, endpoint: str, dcr_id: str, stream: str = "default") -> None:
        self.endpoint = endpoint
        self.dcr_id = dcr_id
        self.stream = stream
        self.events: list[object] = []

    def emit(self, event) -> None:
        self.events.append(event)

    def flush(self, timeout_s: float = 5.0) -> None:
        return None


class _SinkWithFromSettings:
    name = "factory_driven"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.events: list[object] = []

    @classmethod
    def from_settings(cls, settings: dict) -> _SinkWithFromSettings:
        # Consumes the raw dict however it likes -- here we remap a key.
        return cls(endpoint=settings["url"])

    def emit(self, event) -> None:
        self.events.append(event)

    def flush(self, timeout_s: float = 5.0) -> None:
        return None


def test_from_config_passes_plugin_subsection_as_kwargs(tmp_path: Path) -> None:
    """``[telemetry.needs_args]`` TOML subsection is passed as ``**kwargs``."""
    body = """\
[core]
tenant_id = "t-1"

[telemetry]
sink = "needs_args"

[telemetry.needs_args]
endpoint = "https://dce.example.com"
dcr_id = "dcr-abc-123"
stream = "Custom-App"
"""
    path = _write(tmp_path, body)
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "needs_args", _SinkWithRequiredArgs)

    fdo = FabricDataOps.from_config(path, registry=reg)

    assert isinstance(fdo.telemetry, _SinkWithRequiredArgs)
    assert fdo.telemetry.endpoint == "https://dce.example.com"
    assert fdo.telemetry.dcr_id == "dcr-abc-123"
    assert fdo.telemetry.stream == "Custom-App"


def test_from_config_prefers_from_settings_classmethod(tmp_path: Path) -> None:
    """If ``cls.from_settings`` exists, it is used instead of kwarg splatting."""
    body = """\
[core]
tenant_id = "t-1"

[telemetry]
sink = "factory_driven"

[telemetry.factory_driven]
url = "https://custom.example.com"
"""
    path = _write(tmp_path, body)
    reg = Registry()
    reg.register(
        "sigantry.telemetry_sinks",
        "factory_driven",
        _SinkWithFromSettings,
    )

    fdo = FabricDataOps.from_config(path, registry=reg)

    assert isinstance(fdo.telemetry, _SinkWithFromSettings)
    assert fdo.telemetry.endpoint == "https://custom.example.com"


def test_from_config_missing_plugin_config_reports_actionable_error(
    tmp_path: Path,
) -> None:
    """Plugin needs args but TOML has no subsection -- the error is actionable."""
    body = """\
[core]
tenant_id = "t-1"

[telemetry]
sink = "needs_args"
"""
    path = _write(tmp_path, body)
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "needs_args", _SinkWithRequiredArgs)

    with pytest.raises(TypeError, match=r"\[telemetry\.needs_args\]"):
        FabricDataOps.from_config(path, registry=reg)


# ---------------------------------------------------------------------------
# Lifecycle / Closeable contract (P1-3 fix)
# ---------------------------------------------------------------------------


class _ClosingSink:
    """Test double that records close() calls."""

    name = "closing_sink"

    def __init__(self) -> None:
        self.events: list[object] = []
        self.closed = 0

    def emit(self, event) -> None:
        self.events.append(event)

    def flush(self, timeout_s: float = 5.0) -> None:
        return None

    def close(self) -> None:
        self.closed += 1


class _ClosingGate:
    """Test double that raises during close() — must not prevent other closes."""

    name = "bad_closer"

    def __init__(self) -> None:
        self.closed = 0

    def run(self, suite, data_ref) -> GateResult:  # pragma: no cover - unused
        return GateResult(suite=suite, success=True, violations=[], evaluated=0, run_id="rid")

    def close(self) -> None:
        self.closed += 1
        raise RuntimeError("simulated close() failure from plugin")


def test_fabric_dataops_close_calls_closeable_seams() -> None:
    """``close()`` calls ``.close()`` on every seam satisfying Closeable."""
    sink = _ClosingSink()
    fdo = FabricDataOps(telemetry=sink)
    fdo.close()
    assert sink.closed == 1


def test_fabric_dataops_close_skips_non_closeable_seams() -> None:
    """Seams without ``close()`` are skipped (NoopGate is not Closeable)."""
    fdo = FabricDataOps(dq_gate=NoopGate(), telemetry=_ClosingSink())
    # Should not raise despite NoopGate having no close()
    fdo.close()


def test_fabric_dataops_close_swallows_plugin_errors() -> None:
    """One plugin's failing close() must not prevent others from closing."""
    good_sink = _ClosingSink()
    bad_gate = _ClosingGate()
    fdo = FabricDataOps(telemetry=good_sink, dq_gate=bad_gate)
    # Must not raise despite bad_gate.close() throwing.
    fdo.close()
    assert good_sink.closed == 1
    assert bad_gate.closed == 1


def test_fabric_dataops_context_manager_closes_on_exit() -> None:
    """``with FabricDataOps(...) as fdo:`` calls close() on block exit."""
    sink = _ClosingSink()
    with FabricDataOps(telemetry=sink) as fdo:
        fdo.emit("inside.block")
        assert sink.closed == 0
    assert sink.closed == 1


def test_fabric_dataops_context_manager_closes_on_exception() -> None:
    """Exceptions propagating out of the with-block still trigger close()."""
    sink = _ClosingSink()
    with pytest.raises(ValueError), FabricDataOps(telemetry=sink):
        raise ValueError("boom")
    assert sink.closed == 1


def test_from_config_non_table_subsection_rejected(tmp_path: Path) -> None:
    """A scalar value under ``[telemetry.needs_args]`` is rejected fast."""
    body = """\
[core]
tenant_id = "t-1"

[telemetry]
sink = "needs_args"
needs_args = "not-a-table"
"""
    path = _write(tmp_path, body)
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "needs_args", _SinkWithRequiredArgs)

    with pytest.raises(TypeError, match="must be a TOML table"):
        FabricDataOps.from_config(path, registry=reg)


# ---------------------------------------------------------------------------
# Plan 08-02: behaviour methods are wired (inverted from the Plan 08-01 stubs)
# ---------------------------------------------------------------------------


def test_fabric_dataops_emit_routes_through_injected_sink() -> None:
    """FabricDataOps.emit delegates to emit_telemetry via the injected sink."""
    sink = InMemoryTelemetrySink()
    fdo = FabricDataOps(telemetry=sink)

    fdo.emit("e1", {"k": 1})

    assert len(sink.events) == 1
    assert sink.events[0].name == "e1"
    assert sink.events[0].properties == {"k": 1}


def test_fabric_dataops_emit_noop_without_sink() -> None:
    """Without a telemetry seam the emit call is a silent no-op."""
    fdo = FabricDataOps()
    # Should not raise.
    assert fdo.emit("e1", {}) is None


def test_fabric_dataops_dq_gate_routes_through_injected_gate() -> None:
    """FabricDataOps.run_dq_gate delegates to the dispatcher with the DI gate."""
    fdo = FabricDataOps(dq_gate=NoopGate())

    result = fdo.run_dq_gate("my_suite", DataRef(name="bronze.x", path="/bronze/x"))

    assert isinstance(result, GateResult)
    assert result.success is True
    assert result.suite == "my_suite"


def test_fabric_dataops_dq_gate_resolves_via_registry_and_settings(
    tmp_path: Path,
) -> None:
    """When no seam is injected, the registry + settings.dq.gate resolve the gate."""
    toml = _write(
        tmp_path,
        '[core]\ntenant_id = "t-1"\n\n[dq]\ngate = "noop"\n',
    )
    reg = Registry()
    reg.register("sigantry.dq_gates", "noop", NoopGate)

    fdo = FabricDataOps.from_config(toml, registry=reg)
    result = fdo.run_dq_gate("s", DataRef(name="x", path="/x"))
    assert result.success is True


def test_fabric_dataops_deploy_routes_through_injected_profile() -> None:
    """FabricDataOps.deploy delegates to the orchestrator with the DI profile."""
    profile = FakeDeployProfile(plan_fn=lambda _ctx: DeployPlan(actions=[{"op": "publish"}]))
    fdo = FabricDataOps(deploy_profile=profile)

    result = fdo.deploy(DeployContext(workspace_id="ws-1", environment="DEV"))

    assert isinstance(result, DeployResult)
    assert result.workspace_id == "ws-1"
    assert result.items_published == 1


def test_fabric_dataops_deploy_resolves_via_registry_and_settings(
    tmp_path: Path,
) -> None:
    """When no seam is injected, settings.deploy.profile drives the resolution."""
    toml = _write(
        tmp_path,
        '[core]\ntenant_id = "t-1"\n\n[deploy]\nprofile = "fake"\n',
    )
    reg = Registry()
    reg.register("sigantry.deploy_profiles", "fake", FakeDeployProfile)

    fdo = FabricDataOps.from_config(toml, registry=reg)
    result = fdo.deploy(DeployContext(workspace_id="ws-1", environment="DEV"))
    assert isinstance(result, DeployResult)
    assert result.workspace_id == "ws-1"


def test_fabric_dataops_behaviour_methods_no_longer_raise() -> None:
    """Regression from Plan 08-01 inverted: methods produce results, not NotImplementedError."""
    fdo = FabricDataOps(
        telemetry=InMemoryTelemetrySink(),
        dq_gate=NoopGate(),
        deploy_profile=FakeDeployProfile(),
    )
    # emit: never raises
    fdo.emit("evt", {"k": 1})
    # run_dq_gate: returns GateResult
    gate_result = fdo.run_dq_gate("s", DataRef(name="x", path="/x"))
    assert isinstance(gate_result, GateResult)
    # deploy: returns DeployResult
    deploy_result = fdo.deploy(DeployContext(workspace_id="ws-1", environment="DEV"))
    assert isinstance(deploy_result, DeployResult)


def test_fabric_dataops_dq_gate_without_name_raises() -> None:
    """No gate seam + no gate_name + no settings.dq.gate -> ValueError from dispatcher."""
    fdo = FabricDataOps()
    with pytest.raises(ValueError, match="gate_name"):
        fdo.run_dq_gate("s", DataRef(name="x", path="/x"))


def test_fabric_dataops_deploy_without_name_raises() -> None:
    """No profile seam + no profile_name + no settings.deploy.profile -> ValueError."""
    fdo = FabricDataOps()
    with pytest.raises(ValueError, match="profile_name"):
        fdo.deploy(DeployContext(workspace_id="ws-1", environment="DEV"))


def test_fabric_dataops_emit_strict_propagates_sink_exception() -> None:
    """strict=True surfaces the sink's exception."""

    class _BoomSink:
        name = "boom"

        def emit(self, event):
            raise RuntimeError("boom")

        def flush(self, timeout_s: float = 5.0) -> None:
            return None

    fdo = FabricDataOps(telemetry=_BoomSink())
    with pytest.raises(RuntimeError, match="boom"):
        fdo.emit("evt", {}, strict=True)
