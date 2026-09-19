"""TelemetrySink contract tests.

Covers both the in-memory double and the HS2 ``LogAnalyticsSink`` plugin.
The plugin's ``emit`` path is NOT exercised here (it requires real Azure
credentials); protocol conformance, ``name``, and ``flush`` safety are.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    TelemetryEvent,
    TelemetrySink,
)
from sigantry_core.testing.doubles import InMemoryTelemetrySink


def _plugin_sink_or_skip():
    pytest.importorskip("sigantry_hs2")
    from sigantry_hs2.telemetry.log_analytics_sink import (
        LogAnalyticsSink,
    )

    return LogAnalyticsSink(
        dce_uri="https://contract.example.invalid",
        dcr_immutable_id="dcr-contract-immutable-id",
    )


@pytest.mark.contract
def test_telemetry_sink_double_has_name(fdt_telemetry_sink_contract) -> None:
    fdt_telemetry_sink_contract(InMemoryTelemetrySink())


@pytest.mark.contract
def test_telemetry_sink_double_emit_returns_none() -> None:
    sink = InMemoryTelemetrySink()
    event = TelemetryEvent(name="hello", properties={"k": "v"})
    assert sink.emit(event) is None
    assert sink.events == [event]


@pytest.mark.contract
def test_telemetry_sink_double_flush_returns_none() -> None:
    sink = InMemoryTelemetrySink()
    assert sink.flush() is None
    assert sink.flushed == 1


@pytest.mark.contract
def test_telemetry_sink_double_satisfies_runtime_protocol() -> None:
    assert isinstance(InMemoryTelemetrySink(), TelemetrySink)


@pytest.mark.contract
def test_telemetry_sink_plugin_has_name(fdt_telemetry_sink_contract) -> None:
    fdt_telemetry_sink_contract(_plugin_sink_or_skip())


@pytest.mark.contract
def test_telemetry_sink_plugin_flush_is_safe() -> None:
    """Plugin's ``flush`` is a no-op and never raises — protocol invariant."""
    sink = _plugin_sink_or_skip()
    assert sink.flush() is None
