"""Per-endpoint Fabric REST rate limits.

# NOTE [ASSUMED]: Microsoft Learn's Throttling article
# (learn.microsoft.com/en-us/rest/api/fabric/articles/throttling) states
# "Every Fabric admin and core public API call is throttled" but does NOT
# publish a canonical per-endpoint limit table. The figures below are sourced
# from the project PITFALLS.md catalogue (Pitfall 13), the Phase 2 orchestrator
# prompt, and community reports. They are CONSERVATIVE (set at or BELOW
# published / observed limits) and warrant quarterly re-verification.
#
# Cross-references:
# - .planning/research/PITFALLS.md Pitfall 13 (per-endpoint-per-user, not global)
# - .planning/phases/02-rest-api-client-layer/02-RESEARCH.md Pattern 5
# - https://learn.microsoft.com/en-us/rest/api/fabric/articles/throttling
"""

from __future__ import annotations

from typing import Final

# Shape: { (method, path_pattern) : (max_calls, period_seconds) }
# path_pattern may contain ``{placeholder}`` (single-segment wildcard) or
# ``*`` (greedy wildcard). The catch-all ``("*", "*")`` MUST be the last
# fallback; find_bucket_key scoring will only choose it when nothing more
# specific matches.
RATE_LIMITS: Final[dict[tuple[str, str], tuple[int, int]]] = {
    # Fabric Core - workspaces list bounded by 200/min per user [ASSUMED]
    ("GET", "/v1/workspaces"): (200, 60),
    ("POST", "/v1/workspaces"): (30, 60),
    # Workspace items CRUD - conservative 100/min [ASSUMED]
    ("GET", "/v1/workspaces/{id}/items"): (100, 60),
    ("POST", "/v1/workspaces/{id}/items"): (30, 60),
    ("DELETE", "/v1/workspaces/{id}/items/{itemId}"): (30, 60),
    # Workspace capacity assignment (Plan 03-01, WKSP-03) - 202 LRO, 30/min [ASSUMED]
    ("POST", "/v1/workspaces/{id}/assignToCapacity"): (30, 60),
    # Capacities list (Plan 03-02, WKSP-04) - 60/min [ASSUMED]
    ("GET", "/v1/capacities"): (60, 60),
    # Data Pipelines - Pitfall 13 cites 10/min specifically
    ("POST", "/v1/workspaces/{id}/dataPipelines"): (10, 60),
    # Admin APIs - hour-scoped bucket [ASSUMED 200/hour]
    ("GET", "/v1/admin/*"): (200, 3600),
    ("POST", "/v1/admin/*"): (200, 3600),
    # Tenant-settings baseline export (Plan 03-04, GOV-05) - documented at
    # 25 req/minute (Pitfall 8, NOT hour-scoped). 60% headroom = 15/min.
    # Specific entry MUST resolve at runtime ahead of the /v1/admin/*
    # wildcard; regression-guarded by test_rate_limit.py W-5 test.
    ("GET", "/v1/admin/tenantsettings"): (15, 60),
    # Sensitivity-label sync (Plan 03-03, GOV-02) - both endpoints documented
    # at 25 req/hr; 60% headroom to absorb pyrate-limiter clock skew (Pitfall 9).
    ("POST", "/v1/admin/items/bulkSetLabels"): (15, 3600),
    ("POST", "/v1.0/myorg/admin/informationprotection/setLabels"): (15, 3600),
    # Fabric Core Git integration (Plan 04-03, DEPLOY-05) - Microsoft Learn
    # does not publish a per-endpoint table; figures are CONSERVATIVE and
    # reflect that Git ops are low-volume admin actions (connect / init /
    # update / commit are typically per-deploy, not per-request).
    ("POST", "/v1/workspaces/{id}/git/connect"): (30, 60),
    ("POST", "/v1/workspaces/{id}/git/initializeConnection"): (15, 60),
    ("POST", "/v1/workspaces/{id}/git/updateFromGit"): (15, 60),
    ("POST", "/v1/workspaces/{id}/git/commitToGit"): (15, 60),
    # Fabric Variable Library (Plan 04-03, DEPLOY-06) - per-library GET/PATCH/
    # DELETE fall through to the catch-all (sufficient for Phase 4); the
    # workspace-scoped create + list get dedicated buckets.
    ("POST", "/v1/workspaces/{id}/variableLibraries"): (30, 60),
    ("GET", "/v1/workspaces/{id}/variableLibraries"): (60, 60),
    # Fabric Environment staging (Plan 04-03, Pitfall 6) - wheel uploads are
    # low-volume (deploys, not per-request). Conservative 10/min for both
    # staging endpoints pending Microsoft Learn publication of the real limit.
    (
        "POST",
        "/v1/workspaces/{id}/environments/{env}/staging/libraries",
    ): (10, 60),
    (
        "POST",
        "/v1/workspaces/{id}/environments/{env}/staging/publish",
    ): (10, 60),
    # LRO state polling - high volume expected; let client-side throttle pace it
    ("GET", "/v1/operations/{id}"): (500, 60),
    ("GET", "/v1/operations/{id}/result"): (500, 60),
    # Catch-all fallback (keep last - scoring in find_bucket_key guarantees
    # a more specific entry wins; this is the safety net for un-catalogued paths)
    ("*", "*"): (1000, 60),
}
