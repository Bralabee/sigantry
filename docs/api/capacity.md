# sigantry_core.capacity

Capacity inspection + pause/resume lifecycle via ARM. Covers CAP-01..CAP-03.

CLI mirror: `sigantry capacity {list|pause|resume}`. Destructive operations (`pause`, `resume`) require both `--force` and `--runbook-id` - plugin packages ship the project-specific capacity runbooks.

::: sigantry_core.capacity
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      separate_signature: true
      show_signature_annotations: true
