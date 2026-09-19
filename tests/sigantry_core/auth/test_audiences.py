"""Scope constants sanity."""

from __future__ import annotations

from sigantry_core.auth.audiences import (
    ALL_SCOPES,
    AZURE_DEVOPS_SCOPE,
    AZURE_MONITOR_INGESTION_AUDIENCE,
    AZURE_MONITOR_INGESTION_SCOPE,
    AZURE_RM_SCOPE,
    FABRIC_AUDIENCE,
    FABRIC_SCOPE,
    GRAPH_AUDIENCE,
    GRAPH_SCOPE,
    POWERBI_SCOPE,
    PURVIEW_SCOPE,
)


def test_every_scope_ends_with_default_suffix() -> None:
    for s in ALL_SCOPES:
        assert s.endswith("/.default"), s


def test_fabric_scope_value() -> None:
    assert FABRIC_SCOPE == "https://api.fabric.microsoft.com/.default"
    assert FABRIC_AUDIENCE == "https://api.fabric.microsoft.com"


def test_all_scopes_are_unique() -> None:
    assert len(set(ALL_SCOPES)) == len(ALL_SCOPES)


def test_all_scopes_contains_every_constant() -> None:
    # Parity invariant: if a new scope is added, ALL_SCOPES must reflect it.
    expected = {
        FABRIC_SCOPE,
        POWERBI_SCOPE,
        GRAPH_SCOPE,
        PURVIEW_SCOPE,
        AZURE_RM_SCOPE,
        AZURE_MONITOR_INGESTION_SCOPE,
        AZURE_DEVOPS_SCOPE,
    }
    assert set(ALL_SCOPES) == expected


def test_azure_devops_scope_value() -> None:
    # Phase 11: Azure DevOps Services first-party app id (Microsoft Entra).
    assert AZURE_DEVOPS_SCOPE == "499b84ac-1321-427f-aa17-267ca6975798/.default"


def test_azure_monitor_ingestion_scope_value() -> None:
    assert AZURE_MONITOR_INGESTION_SCOPE == "https://monitor.azure.com/.default"
    assert AZURE_MONITOR_INGESTION_AUDIENCE == "https://monitor.azure.com"


def test_powerbi_scope_matches_analysis_windows_net() -> None:
    # Pin the legacy name; Power BI REST uses analysis.windows.net, not powerbi.com.
    assert POWERBI_SCOPE.startswith("https://analysis.windows.net/powerbi/api")


def test_graph_audience_matches_graph_microsoft_com() -> None:
    assert GRAPH_AUDIENCE == "https://graph.microsoft.com"
