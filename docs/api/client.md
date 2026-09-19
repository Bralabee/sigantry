# sigantry_core.client

Single shared HTTP client. The only subpackage that may import `httpx` directly (enforced by Ruff `TID251`). Wraps retries (tenacity), rate limiting (pyrate-limiter), and long-running-operation polling against Fabric REST + ARM.

::: sigantry_core.client
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      separate_signature: true
      show_signature_annotations: true
