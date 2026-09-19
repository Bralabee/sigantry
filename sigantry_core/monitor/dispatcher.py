"""Telemetry dispatcher (PROD-05).

Thin resolution layer between consumer code and a registered
:class:`~sigantry_core.protocols.TelemetrySink` implementation. The
dispatcher never carries business logic: it looks up a sink by name (via the
plugin :class:`~sigantry_core.registry.Registry`) and hands the
event off.

Two helpers are also exposed for the common case where callers want to
configure a process-wide default sink (e.g. CLI entry points wiring a sink at
startup) without threading the sink through every call-site:

- :func:`set_default_sink` stores a module-level default.
- :func:`get_default_sink` returns the current default (``None`` if unset).

See ``docs/reference/protocols.md`` and PRODUCTIZATION.md Section 2.1 for the
consumer-strip rationale: no default stream constant, no vendor env reads, no
hard-coded string literals on the base layer.

Audit-2026-05-07 W2.2: ``resolve_sink`` is now a thin wrapper around the
canonical :func:`sigantry_core._dispatch.resolve_seam` helper -- the
namespaced-kwargs path it used to carry inline is now part of the single
seam resolution algorithm shared with every other dispatcher.
"""

from __future__ import annotations

from sigantry_core._dispatch import resolve_seam
from sigantry_core.config import ToolkitSettings
from sigantry_core.protocols import TelemetrySink
from sigantry_core.registry import GROUP_TELEMETRY_SINKS, Registry

_default_sink: TelemetrySink | None = None


def set_default_sink(sink: TelemetrySink | None) -> None:
    """Set the module-level default sink used by ``emit_telemetry`` when no
    explicit sink is passed and no DI sink is supplied on a front-door object.

    Pass ``None`` to clear the default (no-op emission).
    """
    global _default_sink
    _default_sink = sink


def get_default_sink() -> TelemetrySink | None:
    """Return the current module-level default sink (``None`` if unset)."""
    return _default_sink


def resolve_sink(
    settings: ToolkitSettings | None = None,
    registry: Registry | None = None,
) -> TelemetrySink | None:
    """Resolve the configured sink via the plugin registry.

    Resolution order:

    1. ``settings is None`` or ``settings.telemetry.sink is None`` -> ``None``.
    2. Otherwise the registry (``registry`` kwarg, else
       :func:`default_registry`) is discovered and asked to resolve
       the named implementation under group
       :data:`sigantry_core.registry.GROUP_TELEMETRY_SINKS`.
    3. Class results go through
       :func:`sigantry_core._dispatch.resolve_seam` (which honours
       ``cls.from_settings`` then ``[telemetry.<sink_name>]`` kwargs
       then a zero-arg fallback). Pre-instantiated impls are returned
       as-is.

    Raises ``KeyError`` if the name is not registered.
    """
    if settings is None:
        return None
    return resolve_seam(
        GROUP_TELEMETRY_SINKS,
        settings.telemetry.sink,
        registry=registry,
        settings=settings,
    )


__all__ = [
    "get_default_sink",
    "resolve_sink",
    "set_default_sink",
]
