"""Fabric / Power BI / MS Graph / Purview / Azure RM / Azure Monitor OAuth audiences.

Scope strings (with the trailing `/.default`) are what azure-identity's
`get_token(scope, ...)` expects. The corresponding PowerShell
`Get-AzAccessToken -ResourceUrl ...` audience drops the `/.default` suffix
- the PS parity cmdlet `Get-FabricToken` handles that mapping itself.
"""

from __future__ import annotations

from typing import Final

FABRIC_SCOPE: Final[str] = "https://api.fabric.microsoft.com/.default"
POWERBI_SCOPE: Final[str] = "https://analysis.windows.net/powerbi/api/.default"
GRAPH_SCOPE: Final[str] = "https://graph.microsoft.com/.default"
PURVIEW_SCOPE: Final[str] = "https://purview.azure.net/.default"
AZURE_RM_SCOPE: Final[str] = "https://management.azure.com/.default"
AZURE_MONITOR_INGESTION_SCOPE: Final[str] = "https://monitor.azure.com/.default"

AZURE_DEVOPS_SCOPE: Final[str] = "499b84ac-1321-427f-aa17-267ca6975798/.default"
"""Azure DevOps Services bearer-token scope (Microsoft Entra ID).

The resource id is the canonical Visual Studio / Azure DevOps Services
first-party application id; the ``/.default`` suffix requests all
delegated/application permissions granted to the SPN. Used by the
Phase 11 ``AdoWorkItemProvider``.

Source: https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/service-principal-managed-identity
"""

ALL_SCOPES: Final[tuple[str, ...]] = (
    FABRIC_SCOPE,
    POWERBI_SCOPE,
    GRAPH_SCOPE,
    PURVIEW_SCOPE,
    AZURE_RM_SCOPE,
    AZURE_MONITOR_INGESTION_SCOPE,
    AZURE_DEVOPS_SCOPE,
)

# Audiences (no `/.default` suffix) - used by the Fabric Admin REST probes in
# `sigantry_core.auth.diagnose` for the Authorization header Bearer value.
FABRIC_AUDIENCE: Final[str] = "https://api.fabric.microsoft.com"
GRAPH_AUDIENCE: Final[str] = "https://graph.microsoft.com"
AZURE_MONITOR_INGESTION_AUDIENCE: Final[str] = "https://monitor.azure.com"
