"""Monitor-subpackage configuration stub.

The v1 ``STREAM_TO_ENV_VAR`` dict and ``load_monitor_config`` helpers were
consumer-specific (hard-coded stream names, vendor env vars, ``bicep/outputs/``
fallback). They have been removed as part of PROD-05 and PROD-08: the base
package does not carry a transport-specific or consumer-named configuration
surface.

Generic telemetry settings live on
:class:`sigantry_core.config.TelemetrySettings`; per-plugin config
(stream names, DCR immutable IDs, DCE URIs, credentials) lives inside the
plugin's own pydantic model. See ``docs/reference/protocols.md``.

This module is kept as a placeholder so external ``import
sigantry_core.monitor.config`` statements do not crash; it exports
no symbols.
"""

from __future__ import annotations

__all__: list[str] = []
