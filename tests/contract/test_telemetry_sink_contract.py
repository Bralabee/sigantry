"""TelemetrySink contract tests.

Runs the contract against the ``InMemoryTelemetrySink`` double: protocol
conformance, ``name``, and that ``emit`` and ``flush`` return ``None``.
"""

from __future__ import annotations

import pytest

from sigantry_core.protocols import (
    TelemetryEvent,
    TelemetrySink,
)
from sigantry_core.testing.doubles import InMemoryTelemetrySink


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
