"""sigantry_core.auth - the single authentication surface for the toolkit.

Every downstream phase (client layer, workspace, governance, deploy, monitor)
imports tokens and secrets from here. The contract:

- `get_fabric_token()` returns a bearer token for the Fabric API on any of the
  three supported runtimes (laptop, ADO pipeline, Fabric notebook) without
  code change.
- `resolve_secret(ref)` resolves a `kv://<vault>/<name>` URI (or returns a
  pass-through `Secret(value=ref)` for plain strings) using the same
  credential chain as token acquisition.
- `Secret` is an opaque wrapper: its `repr`/`str` never leak the cleartext.
- `FabricAuthError` and its four subclasses carry `credential_used`, `scope`,
  and `remediation` attributes for actionable logging.
"""

from __future__ import annotations

from sigantry_core.auth.audiences import (
    ALL_SCOPES,
    AZURE_RM_SCOPE,
    FABRIC_AUDIENCE,
    FABRIC_SCOPE,
    GRAPH_AUDIENCE,
    GRAPH_SCOPE,
    POWERBI_SCOPE,
    PURVIEW_SCOPE,
)
from sigantry_core.auth.errors import (
    FabricAuthError,
    GroupMembershipError,
    KeyVaultResolutionError,
    TenantSettingError,
    TokenAcquisitionError,
)
from sigantry_core.auth.keyvault import Secret, resolve_secret
from sigantry_core.auth.token_provider import (
    TokenProvider,
    TokenProviderProtocol,
    get_azure_rm_token,
    get_default_credential,
    get_fabric_token,
    get_graph_token,
    get_powerbi_token,
    get_purview_token,
    get_token,
    get_token_provider,
    reset_token_provider,
)

__all__ = [
    "ALL_SCOPES",
    "AZURE_RM_SCOPE",
    "FABRIC_AUDIENCE",
    "FABRIC_SCOPE",
    "GRAPH_AUDIENCE",
    "GRAPH_SCOPE",
    "POWERBI_SCOPE",
    "PURVIEW_SCOPE",
    "FabricAuthError",
    "GroupMembershipError",
    "KeyVaultResolutionError",
    "Secret",
    "TenantSettingError",
    "TokenAcquisitionError",
    "TokenProvider",
    "TokenProviderProtocol",
    "get_azure_rm_token",
    "get_default_credential",
    "get_fabric_token",
    "get_graph_token",
    "get_powerbi_token",
    "get_purview_token",
    "get_token",
    "get_token_provider",
    "reset_token_provider",
    "resolve_secret",
]
