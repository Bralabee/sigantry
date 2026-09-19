# sigantry_core.dq

Data-quality gate dispatcher. The base package ships only the dispatcher
contract; concrete gates register under the
`sigantry.dq_gates` entry-point group from plugin
packages.

Public surface: `run_gate` (dispatcher), `GateResult`, `dq_app` (Typer
subapp). Telemetry propagation happens through the
:class:`TelemetrySink` seam wired on the composing `FabricDataOps`.

CLI mirror: `sigantry dq gate --suite <name> --dataset
<name>`. Exit codes: 0 healthy, 1 gate violation, 2 config error.

::: sigantry_core.dq
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      separate_signature: true
      show_signature_annotations: true
