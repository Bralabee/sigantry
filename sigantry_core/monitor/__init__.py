"""sigantry_core.monitor - telemetry dispatcher layer (PROD-05).

Consumers call :func:`emit_telemetry` with a direct-DI sink or rely on the
module-level default set via :func:`set_default_sink`. Sink implementations
(Azure Monitor, OpenTelemetry, stdout, ...) ship in plugin packages and
register under the ``fabric_dataops_toolkits.telemetry_sinks`` entry-point
group.

The legacy consumer-specific DCR config (stream names, DCE URIs, vendor env
reads) has been removed — see PRODUCTIZATION.md Section 2.1 for the audit
trail.
"""

from sigantry_core.monitor.dispatcher import (
    get_default_sink,
    resolve_sink,
    set_default_sink,
)
from sigantry_core.monitor.emit import emit_telemetry
from sigantry_core.protocols import TelemetryEvent

__all__ = [
    "TelemetryEvent",
    "emit_telemetry",
    "get_default_sink",
    "resolve_sink",
    "set_default_sink",
]
