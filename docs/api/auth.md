# sigantry_core.auth

DefaultAzureCredential chain, token acquisition, and the `diagnose-auth` CLI (AUTH-01..05).

Public surface: `TokenProvider`, `get_fabric_token`, `get_powerbi_token`, `get_purview_token`, `Secret` (opaque, `<redacted>` repr), `resolve_secret` for `kv://` URIs.

`TokenProvider(tenant_id=...)` pins a tenant: every token, including those a caller of `get_credential()` such as fabric-cicd requests itself, is requested from that tenant, and a token whose `tid` claim names another tenant or cannot be read raises `TenantMismatchError` (a `TokenAcquisitionError`). A `tenant_id` that is not a tenant ID GUID raises `InvalidTenantIdError` (a `ValueError`) before any token is requested. Without `tenant_id` the credential is used as it is.

::: sigantry_core.auth
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      separate_signature: true
      show_signature_annotations: true
