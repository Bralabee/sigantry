"""emit_telemetry: generic dispatcher over the TelemetrySink protocol seam.

PROD-05: the base package carries no transport specifics, no stream names,
and no environment-variable reads. A plugin (for example an Azure Monitor
``LogAnalyticsSink``) registers itself under
``fabric_dataops_toolkits.telemetry_sinks`` and owns the stream / DCR /
credentials concerns.

Resolution order for ``emit_telemetry``:

1. An explicit ``sink`` kwarg (direct DI).
2. The module-level default set via
   :func:`sigantry_core.monitor.dispatcher.set_default_sink`.
3. Otherwise the call is a silent no-op — observability is best-effort at
   the base layer.

``strict=True`` re-raises any exception thrown by ``sink.emit``; default is
best-effort swallow.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sigantry_core.monitor.dispatcher import get_default_sink
from sigantry_core.protocols import TelemetryEvent, TelemetrySink

logger = logging.getLogger("sigantry_core.monitor")


def emit_telemetry(
    event_name: str,
    properties: dict[str, Any] | None = None,
    *,
    sink: TelemetrySink | None = None,
    strict: bool = False,
) -> None:
    """Emit one structured event to the resolved telemetry sink.

    Parameters
    ----------
    event_name
        Logical event name (e.g. ``"deploy_started"``).
    properties
        Arbitrary event properties. Defaults to an empty dict.
    sink
        Optional direct-DI sink; takes precedence over the module-level
        default. When ``None`` the dispatcher's default sink is used; if that
        too is ``None`` the call is a silent no-op.
    strict
        If ``True``, re-raise any exception from ``sink.emit``. Default
        ``False`` swallows errors so a telemetry outage never breaks a caller.
    """
    resolved = sink if sink is not None else get_default_sink()
    if resolved is None:
        return

    event = TelemetryEvent(
        name=event_name,
        properties=dict(properties) if properties else {},
        timestamp=datetime.now(UTC),
    )

    try:
        resolved.emit(event)
    except Exception:  # best-effort by default
        logger.warning("emit_telemetry failed for %s", event_name, exc_info=True)
        if strict:
            raise


__all__ = ["emit_telemetry"]
