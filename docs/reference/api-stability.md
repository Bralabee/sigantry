# Fabric REST API Stability Matrix

**Phase 13 (Council D #1).** Sigantry depends on a mix of GA (general availability) and Preview Microsoft Fabric REST endpoints. This document catalogues which is which, the implications for production use, and the operator-facing acknowledgement gate.

## 1. Overview

Microsoft Fabric REST endpoints carry one of two stability classes:

- **GA** -- backwards-compatible per Microsoft's REST versioning policy. Breaking changes happen only behind a new API version.
- **Preview** -- explicitly subject to breaking change without a new API version. Microsoft documents this directly on each Preview endpoint.

Sigantry's design decision (Council D constraint #1, locked Feb 2026): **the Folders REST endpoint family is load-bearing for INTROSPECT + SYNC**, and we accept the Preview risk in v3.0. We document that risk clearly and gate it behind an explicit operator acknowledgement so consumers cannot silently inherit it.

## 2. Stability matrix

| Endpoint family | Stability | Used by | Notes |
|-----------------|-----------|---------|-------|
| `GET /v1/workspaces/{id}/items` | **GA** | Phase 3 + INTROSPECT-01 (snapshot) + DRIFT-01 (diff) + Phase 12 release diff | `folderId` is first-class on each item record. Source of truth for item placement. |
| `GET /v1/workspaces/{id}/notebooks/{itemId}/getDefinition` (with `format=ipynb`) | **GA** | SYNC-05 (pull) | Council A confirmed; cited [Notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition). |
| `POST /v1/workspaces/{id}/items` | **GA** | Phase 4 deploy + SYNC-04 (apply, via `fabric-cicd`) | Item create/update/delete. Used through `fabric-cicd 1.0.0`. |
| `GET /v1/workspaces/{id}/folders` | **Preview** | INTROSPECT-01 (snapshot) | List folders with paginated cursor. **Breaking change risk.** |
| `POST /v1/workspaces/{id}/folders` | **Preview** | SYNC-04 (apply, via reconciler) | Create folder. **Breaking change risk.** |
| `PATCH /v1/workspaces/{id}/folders/{folderId}` | **Preview** | SYNC-04 (apply, via reconciler) | Update folder display name. **Breaking change risk.** |
| `POST /v1/workspaces/{id}/folders/{folderId}/move` | **Preview** | SYNC-04 (apply, via reconciler) | Move folder under a new parent. **Breaking change risk.** |
| `DELETE /v1/workspaces/{id}/folders/{folderId}` | **Preview** | SYNC-04 (apply, via `fabric-cicd._unpublish_folders`) | Delete folder. **Breaking change risk.** |
| `POST /v1/workspaces/{id}/items/{itemId}/move` | **Preview** | SYNC-04 (apply, via reconciler) | Move item to a new folder. **Breaking change risk.** |
| Phase 11/12 endpoints (work-item providers, deploy ledger) | **GA** | TRACE-01..08, PIPELINE-01..05 | Fully GA. |

## 3. Risk acceptance and the runtime gate (`workflow.preview_apis_acknowledged`)

Sigantry locks against the Preview Folders + item-move endpoints in v3.0 because they are the load-bearing primitive for SYNC + INTROSPECT. The `WorkflowSettings.preview_apis_acknowledged` config flag is the operator's explicit acknowledgement that they understand the implications.

**Default behaviour:** the flag is `False`. On the first invocation of `sigantry sync apply` or `sigantry sync pull` per process, Sigantry emits a single `WARNING`:

```text
Sigantry depends on the Preview Microsoft Fabric Folders REST endpoint
(Council D #1). Set `workflow.preview_apis_acknowledged = true` in
.sigantry.toml (or SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED=true)
to acknowledge and suppress this warning.
```

The warning fires through the stdlib `logging` module (`sigantry_core.sync.cli` logger, level `WARNING`) AND prints to stderr via the Rich console so operators see it during interactive runs. It fires at most once per process to avoid log spam in batch jobs.

`sigantry sync snapshot` does NOT emit the warning. Snapshot is read-only, operator-explicit, and frequently used for diagnostics; we do not want to interrupt the operator's grep-and-jq loop with a Preview API warning every time.

### Acknowledging the gate

Add to `.sigantry.toml`:

```toml
[workflow]
preview_apis_acknowledged = true
```

Or set the env var (CI runners, ephemeral shells):

```bash
export SIGANTRY_WORKFLOW__PREVIEW_APIS_ACKNOWLEDGED=true
```

After acknowledgement, no warning fires for the rest of the process.

### How to revisit the decision

If Microsoft promotes the Folders endpoints to GA (or marks them GA-equivalent under a new API version), Sigantry will:

1. Remove the warning.
2. Default the flag to `True` and deprecate it in `v3.<minor+1>`.
3. Document the cutover in the CHANGELOG.

If Microsoft ships a breaking change to the Folders endpoints under the same API version (the canonical Preview risk), Sigantry will:

1. Update the endpoint binding in `sigantry_core.workspace.folders` and `sigantry_core.sync.snapshot`.
2. Pin the consumer to the latest patch release of `sigantry-core`.
3. Document the migration in the CHANGELOG.

## 4. Notification module httpx-direct carve-out

Project convention (CLAUDE.md) is "one HTTP client" -- everything Fabric-bound flows through `sigantry_core.client.FabricRestClient`. Phase 13's notification sinks (`sigantry_core/sync/notifications.py`, D-30) carry a documented carve-out: `TeamsWebhookSink` / `SlackWebhookSink` POST against operator-supplied webhook URLs that are NOT Fabric API endpoints. Routing them through `FabricRestClient` would force a `TokenProvider` on a credential-less webhook URL, breaking the Teams / Slack contract (webhook URLs encode the secret in the URL itself).

The carve-out is narrowly scoped: only Teams + Slack webhook POSTs use `httpx` directly; every other Sigantry HTTP call against Fabric / ADO / GitHub stays on the shared client.

## 5. Cross-references

- [`sync-schema.md`](sync-schema.md) -- the manifest contract that drives the Folders + items REST surface.
- [`../runbooks/sync/apply.md`](../runbooks/sync/apply.md) -- where the warning appears in operator output.
- [`../runbooks/sync/snapshot-freshness.md`](../runbooks/sync/snapshot-freshness.md) -- TTL'd cache discipline (D-10).
- [`protocols.md`](protocols.md) -- existing seam stability documentation.
- Microsoft Learn: [Folders REST API](https://learn.microsoft.com/en-us/rest/api/fabric/core/folders) (Preview), [Items - List Items](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items) (GA), [Notebook definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/notebook-definition).
