"""Unit tests for :func:`sigantry_core.monitor.emit_telemetry`.

Rewritten for Plan 08-02: direct-DI with ``InMemoryTelemetrySink``; no HS2
env-var fixtures; no ``stream=`` kwarg.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from sigantry_core.monitor import dispatcher as dispatcher_module
from sigantry_core.monitor.emit import emit_telemetry
from sigantry_core.protocols import TelemetryEvent
from sigantry_core.testing.doubles import InMemoryTelemetrySink


@pytest.fixture(autouse=True)
def _reset_default_sink() -> None:
    """Clear the module-level default sink between tests."""
    dispatcher_module.set_default_sink(None)
    yield
    dispatcher_module.set_default_sink(None)


def test_emit_telemetry_uses_injected_sink() -> None:
    """DI sink kwarg routes the event verbatim."""
    sink = InMemoryTelemetrySink()
    emit_telemetry("deploy_started", {"k": 1}, sink=sink)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert isinstance(event, TelemetryEvent)
    assert event.name == "deploy_started"
    assert event.properties == {"k": 1}
    assert event.timestamp is not None


def test_emit_telemetry_noop_when_no_sink_and_no_default() -> None:
    """No sink + no default -> returns None silently, no exception."""
    assert dispatcher_module.get_default_sink() is None
    # Should not raise
    assert emit_telemetry("evt", {"k": 1}) is None


def test_emit_telemetry_uses_module_default_when_set() -> None:
    """set_default_sink stores a process-wide fallback."""
    sink = InMemoryTelemetrySink()
    dispatcher_module.set_default_sink(sink)

    emit_telemetry("evt", {"a": 2})
    assert len(sink.events) == 1
    assert sink.events[0].name == "evt"
    assert sink.events[0].properties == {"a": 2}


def test_emit_telemetry_explicit_sink_overrides_module_default() -> None:
    """Explicit ``sink=`` kwarg wins over the module default."""
    default_sink = InMemoryTelemetrySink()
    override_sink = InMemoryTelemetrySink()
    dispatcher_module.set_default_sink(default_sink)

    emit_telemetry("evt", {}, sink=override_sink)

    assert default_sink.events == []
    assert len(override_sink.events) == 1


class _BoomSink:
    name = "boom"

    def emit(self, event: TelemetryEvent) -> None:
        raise RuntimeError("boom")

    def flush(self, timeout_s: float = 5.0) -> None:
        return None


def test_emit_telemetry_swallows_sink_errors_by_default(caplog: pytest.LogCaptureFixture) -> None:
    """Default ``strict=False`` swallows sink exceptions and logs a warning."""
    with caplog.at_level(logging.WARNING, logger="sigantry_core.monitor"):
        assert emit_telemetry("evt", {}, sink=_BoomSink()) is None
    assert any("emit_telemetry failed" in rec.getMessage() for rec in caplog.records), (
        "expected a WARNING about emit_telemetry failure"
    )


def test_emit_telemetry_strict_reraises() -> None:
    """``strict=True`` re-raises the underlying sink exception."""
    with pytest.raises(RuntimeError, match="boom"):
        emit_telemetry("evt", {}, sink=_BoomSink(), strict=True)


def test_emit_telemetry_has_no_stream_kwarg() -> None:
    """The legacy HS2 ``stream="Custom-Hs2Deploy"`` kwarg is removed."""
    sig = inspect.signature(emit_telemetry)
    assert "stream" not in sig.parameters, (
        "emit_telemetry must not accept a `stream` kwarg; stream routing is "
        "a sink-plugin concern (PROD-05)."
    )


def test_emit_module_reads_no_hs2_env_vars() -> None:
    """``sigantry_core/monitor/emit.py`` contains no ``HS2_`` literal."""
    source = Path("sigantry_core/monitor/emit.py").read_text(encoding="utf-8")
    assert "HS2_" not in source, (
        "emit.py must not read HS2_* environment variables (PROD-05 HS2 strip)."
    )


def test_emit_module_has_no_custom_hs2_literal() -> None:
    """``sigantry_core/monitor/emit.py`` contains no ``Custom-Hs2`` literal."""
    source = Path("sigantry_core/monitor/emit.py").read_text(encoding="utf-8")
    assert "Custom-Hs2" not in source


def test_emit_telemetry_properties_defaults_to_empty_dict() -> None:
    """Passing ``properties=None`` yields an empty-dict event payload."""
    sink = InMemoryTelemetrySink()
    emit_telemetry("evt", None, sink=sink)
    assert sink.events[0].properties == {}


def test_emit_telemetry_returns_none() -> None:
    """Return value is always ``None`` (dispatcher never propagates a result)."""
    sink = InMemoryTelemetrySink()
    assert emit_telemetry("evt", {}, sink=sink) is None
