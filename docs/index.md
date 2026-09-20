# Sigantry

> **Renamed to Sigantry in v3.0.** This project (formerly `fabric-dataops-toolkits`) is renamed to `sigantry-core` per [ADR-0011](decisions/ADR-0011-rename-to-sigantry.md). Legacy distribution names shipped as deprecation shims through v3.0 and dropped in v3.1. See [migration/2.x-to-3.0.md](migration/2.x-to-3.0.md) for the consumer upgrade recipe.

Sigantry (formerly fabric-dataops-toolkits) is an Apache-2.0 **governance, audit, and rollback layer on top of Microsoft's official Fabric tooling** (`fabric-cicd`, the `fab` CLI, Fabric REST) for organisations on Azure DevOps or GitHub CI — it wraps that tooling rather than replacing it. See the [PRODUCT-BRIEF](PRODUCT-BRIEF.md) for the product story, the [landscape survey](LANDSCAPE-2026-06.md) for how it sits beside Microsoft's GA tooling, and the [migration guide](migration/2.x-to-3.0.md) for the v2.x -> v3.0 upgrade path.

Vendor-agnostic base platform. Ships the 11 protocol
seams, plugin registry, typed config loader, and the `FabricDataOps`
composition root. Tenant-specific implementations ship in plugin packages
that register themselves via Python entry points under the 11 canonical
`sigantry.<seam>` groups.

## What lives where

- **[Getting started](getting-started/install.md)** - Install the base
  plus one or more plugin packages, wire them via
  `.fabric-dataops.toml`, and see a first deploy with the in-memory
  doubles.
- **[Tutorials](tutorials/index.md)** - Ten hand-holding worked examples
  with expected output and visual walkthroughs: from first contact through
  sync, drift, audit, bootstrap, rollback, alerts, PR bot and governance.
  Every step verified against a live tenant.
- **[Protocol seams](reference/protocols.md)** - Contract reference for the
  `typing.Protocol` classes plugins implement: the six v2 seams
  (`DeployProfile`, `DataQualityGate`, `TelemetrySink`, `AuthProvider`,
  `RunbookRegistry`, `CapacityPolicy`) plus `WorkItemProvider`.
- **[Seams catalogue](reference/seams.md)** - The authoritative
  per-seam reference for all 11 seams, adding the remaining v3 seams
  (`PrReviewBot`, `NotificationSink`, `SecretStore`, `ApprovalGate`)
  and their reference implementations.
- **[API reference](api/index.md)** - Module-level API rendered from
  docstrings by mkdocstrings.
- **[Runbooks (template)](runbooks/INDEX.md)** - How to structure
  runbooks for your plugin.
- **[Contributing](contributing.md)** - Branching, tests, release gate.
- **[Release process](release-process.md)** - SemVer contract, release
  checklist, `CHANGELOG.md` discipline.

## Picking a plugin

Install the base and any plugins you need:

```bash
pip install sigantry
pip install <your-plugin-package>
```

The base ships no concrete plugin. Reference implementations live in
sibling plugin packages that ship alongside the base.

## Current release

v1.0.0 (2026-09-19). Initial open-source standalone release of Sigantry on PyPI.
Includes complete dual-mode workspace bootstrapping (`workspace bootstrap`),
brownfield adoption (`sync pull`), drift detection (`diff`), automated deployment
and rollback (`deploy`), integrity-checked release ledger (`release`,
[threat model](reference/audit-ledger-threat-model.md)),
and headless PR bot (`pr-bot`).
`sigantry env reconcile` — the upgrade-safe wheel
reconcile that removes superseded versions and publishes once (the add-only
<!-- docs-freshness: allow — release history names the version a feature landed in -->
`env sync` fix). v3.3.0 added `sigantry env sync-all` — config-driven fan-out
of a wheel set across many Fabric Environments from an `environments.yml`
manifest, with gating (PROD-safe), pin/float, idempotency and fail-isolation
expressed as data. Plus tutorials 11-12 (runtime-library plane + safe
auto-update).
Follows v3.2.0 (2026-06-11; `rbac-audit` workspace scoping and dated
file output via `--out` / `--out-dir`). Third same-day release:
follows v3.0.0 (first stable v3 release, shipped by operator directive
with the remaining live-tenant UAT gates in `OPERATOR-PUNCHLIST.md`
rescoped to post-ship) and v3.1.0 (shim-window close per ADR-0011 --
the legacy dists, import paths, and first-party legacy entry-point
tables are gone; the registry's legacy-group dual-read remains as a
grace window, removal tracked as V3.X-ROADMAP LEGACY-SURFACE-DROP).
Distribution is via GitHub Release wheel assets while PyPI publish
stays gated on the A-gates. See `docs/migration/2.x-to-3.0.md` for the
upgrade recipe and `CHANGELOG.md` for full release notes.
