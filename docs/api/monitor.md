# sigantry_core.monitor

Telemetry dispatcher + event envelope. The base package ships only the
dispatcher contract; concrete sinks (Log Analytics, App Insights, OTel,
etc.) register under the
`sigantry.telemetry_sinks` entry-point group from plugin
packages.

Public surface: `emit_telemetry`, `TelemetryEvent`, dispatcher helpers
(`get_default_sink`, `set_default_sink`).

::: sigantry_core.monitor
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      separate_signature: true
      show_signature_annotations: true
