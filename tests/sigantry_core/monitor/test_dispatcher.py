"""Unit tests for :mod:`sigantry_core.monitor.dispatcher`.

PROD-05 invariant: ``resolve_sink`` is a thin registry lookup returning the
named :class:`TelemetrySink` — nothing more. No HS2 strings, no env reads.
"""

from __future__ import annotations

import pytest

from sigantry_core.config import ToolkitSettings
from sigantry_core.monitor.dispatcher import (
    get_default_sink,
    resolve_sink,
    set_default_sink,
)
from sigantry_core.registry import Registry
from sigantry_core.testing.doubles import InMemoryTelemetrySink


def _settings(**telemetry: object) -> ToolkitSettings:
    """Helper: construct a ToolkitSettings with a minimal core + telemetry override."""
    data: dict = {"core": {"tenant_id": "t-1"}}
    if telemetry:
        data["telemetry"] = telemetry
    return ToolkitSettings(**data)


def test_resolve_sink_returns_none_when_settings_is_none() -> None:
    """No settings passed -> no resolution attempted."""
    assert resolve_sink(None, Registry()) is None


def test_resolve_sink_returns_none_without_sink_name() -> None:
    """``telemetry.sink = None`` -> dispatcher short-circuits to None."""
    settings = _settings()
    assert resolve_sink(settings, Registry()) is None


def test_resolve_sink_returns_in_memory_when_configured() -> None:
    """Named sink + registered class -> instantiated instance returned."""
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "in_memory", InMemoryTelemetrySink)
    settings = _settings(sink="in_memory")

    sink = resolve_sink(settings, reg)
    assert isinstance(sink, InMemoryTelemetrySink)


def test_resolve_sink_unknown_name_raises_key_error() -> None:
    """Unregistered name -> KeyError surfacing the name."""
    reg = Registry()
    settings = _settings(sink="does-not-exist")
    with pytest.raises(KeyError, match="does-not-exist"):
        resolve_sink(settings, reg)


def test_resolve_sink_accepts_instance_factories() -> None:
    """A pre-built instance registered under a name is returned as-is."""
    pre_built = InMemoryTelemetrySink()
    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "pre_built", pre_built)
    settings = _settings(sink="pre_built")

    resolved = resolve_sink(settings, reg)
    assert resolved is pre_built


def test_resolve_sink_passes_namespaced_config_as_kwargs() -> None:
    """If a namespaced ``[telemetry.<sink>]`` table exists, it flows as kwargs."""

    class _ConfigurableSink:
        name = "configurable"

        def __init__(self, *, label: str = "unset", buffer: int = 0) -> None:
            self.label = label
            self.buffer = buffer

        def emit(self, event) -> None:  # pragma: no cover - not exercised here
            return None

        def flush(self, timeout_s: float = 5.0) -> None:  # pragma: no cover
            return None

    reg = Registry()
    reg.register("sigantry.telemetry_sinks", "configurable", _ConfigurableSink)
    settings = _settings(sink="configurable", configurable={"label": "hello", "buffer": 32})

    resolved = resolve_sink(settings, reg)
    assert isinstance(resolved, _ConfigurableSink)
    assert resolved.label == "hello"
    assert resolved.buffer == 32


def test_resolve_sink_respects_explicit_registry() -> None:
    """An explicit ``registry`` kwarg wins over ``default_registry()``."""
    custom = Registry()
    custom.register("sigantry.telemetry_sinks", "in_memory", InMemoryTelemetrySink)
    settings = _settings(sink="in_memory")

    # No plugin in default_registry -> this only works because we pass `custom`.
    resolved = resolve_sink(settings, custom)
    assert isinstance(resolved, InMemoryTelemetrySink)


def test_set_default_sink_roundtrips_through_get_default_sink() -> None:
    """set + get exchanges the module-level reference verbatim."""
    assert get_default_sink() is None
    sink = InMemoryTelemetrySink()
    try:
        set_default_sink(sink)
        assert get_default_sink() is sink
    finally:
        set_default_sink(None)
    assert get_default_sink() is None
