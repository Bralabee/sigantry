# Observation planes

`sigantry-core` has **two distinct observation planes**.
Understanding where each event lands is load-bearing for incident
response and for writing correct consumer dashboards.

## Plane 1 — Business telemetry (pluggable)

**Seam:** `TelemetrySink`
**Entry point group:** `sigantry.telemetry_sinks`
**Invoked via:** `FabricDataOps.emit(event, properties)` OR direct calls from
 plugin internals (e.g. `AimsDeployProfile.apply` emits progress events).

Intended for **what the system is doing** — deploy-started,
deploy-finished, gate-result, capacity-decision, etc. These events are
expected to land in a business-facing dashboard (Log Analytics via
`LogAnalyticsSink`, Application Insights via a hypothetical
`ApplicationInsightsSink`, stdout for local dev, etc.).

**Failure mode:** `emit_telemetry` defaults to `strict=False` and
**swallows sink exceptions**. A broken sink cannot brick a deploy, but
it *can* silently drop events. This is intentional — telemetry outages
must not compound into production outages.

**Implication:** Do NOT put authoritative audit records here. If a sink
silently drops, the record is gone. Set `strict=True` at emit sites
where loss would be dangerous (auditors generally want `strict=True`).

## Plane 2 — Destructive-op audit (non-pluggable)

**Module:** `sigantry_core.governance.audit`
**Decorator:** `@destructive_op(resource_kind, action)`
**Where it lands:** `logging.getLogger("sigantry_core.governance.audit")`

Intended for **what the operator did** — capacity pause, workspace
delete, ARM suspend, etc. Every call wrapped in `@destructive_op`
emits exactly one audit record after the gate checks pass:

- `force=True` must be a **keyword argument** at the call site (the
  decorator raises `DestructiveOpError` if absent or `False`).
- `runbook_id` is required for capacity pause/resume (an empty string
  is rejected before any ARM call fires).
- The audit record carries: resource_kind, action, resource_id,
  principal (inferred from `TokenProvider.last_credential_class` if
  not explicit), runbook_id, timestamp.

**This plane is NOT pluggable.** It always goes to the Python logger
at a well-known name. Consumers wire their log aggregation (Log
Analytics DCR, syslog, file handler, etc.) in the normal logging
config path — not via `TelemetrySink`.

**Failure mode:** Exceptions from the audit emission **propagate**.
No best-effort swallow. This is the correct choice — if the audit
cannot be recorded, the destructive operation has no provenance and
must abort.

## Side-by-side

| Dimension | Business telemetry | Destructive-op audit |
|-----------|--------------------|----------------------|
| Seam? | Yes (`TelemetrySink`) | No |
| Pluggable? | Yes | No (fixed logger) |
| Silent-drop on failure? | Yes (`strict=False` default) | No |
| Guarantee | Best-effort | Mandatory |
| Emitter | `FabricDataOps.emit` + plugins | `@destructive_op` decorator |
| Consumer dashboards | Any (plugin-dependent) | Wire through `logging` |
| Typical sink in prod | `LogAnalyticsSink` → DCR | `logging.FileHandler` → fluentd → SIEM |

## Wiring both to the same backend

If you want both planes in Log Analytics, **do not** route the audit
logger through `TelemetrySink`. Instead, attach a `LoggingHandler`
that pushes the well-known logger to a second DCR stream:

```python
import logging
from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter

audit_logger = logging.getLogger("sigantry_core.governance.audit")
audit_handler = AzureMonitorLogExporter.from_connection_string(
    conn_str="InstrumentationKey=..."
)
audit_logger.addHandler(audit_handler)
```

The two planes remain independent; a broken telemetry sink still
leaves destructive-op audit flowing.

## Why not unify them?

Considered during Phase 8 design. Rejected because:

1. **Swallowing is appropriate for telemetry, wrong for audit.**
   Compressing them would force one policy on both.
2. **Pluggability is a risk surface for audit.** An audit record
   should not go through third-party plugin code that can transform,
   drop, or exfiltrate it.
3. **Log aggregators are good at consuming `logging`.** DCRs, fluentd,
   filebeat, etc. all have first-class support for standard loggers.

Revisit if a consumer provides a concrete case where the duality
causes operational pain.

## Related

- `.planning/phases/08-platform-base-refactor/08-SECURITY.md` T-08-03
  (internal threat model; not published to the wiki)
- [thread-safety.md](thread-safety.md)
