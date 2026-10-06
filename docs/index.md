# Sigantry

> **Renamed.** The project was renamed to Sigantry per [ADR-0011](decisions/ADR-0011-rename-to-sigantry.md) and is published on PyPI as `sigantry` ([ADR-0017](decisions/ADR-0017-distribution-name-sigantry.md)), starting at 1.0.0. See [migration/2.x-to-3.0.md](migration/2.x-to-3.0.md) for the old names, what replaces each one, and which of them the code still reads, and [Current release](#current-release) for how the version numbers in older pages relate to the public releases.

Sigantry (formerly fabric-dataops-toolkits) is an Apache-2.0 **governance, audit, and rollback layer on top of Microsoft's official Fabric tooling** (`fabric-cicd`, the `fab` CLI, Fabric REST) for organisations on Azure DevOps or GitHub CI — it wraps that tooling rather than replacing it. See the [PRODUCT-BRIEF](PRODUCT-BRIEF.md) for the product story, the [landscape survey](LANDSCAPE-2026-06.md) for how it sits beside Microsoft's GA tooling, and the [legacy-names note](migration/2.x-to-3.0.md) for the pre-rename names the code still reads.

Vendor-agnostic base platform. Ships the 11 protocol
seams, plugin registry, typed config loader, and the `FabricDataOps`
composition root. Tenant-specific implementations ship in plugin packages
that register themselves via Python entry points under the 11 canonical
`sigantry.<seam>` groups.

## What lives where

- **[Getting started](getting-started/install.md)** - Install the base
  plus one or more plugin packages, wire them via
  `.sigantry.toml`, and see a first deploy with the in-memory
  doubles.
- **[Tutorials](tutorials/index.md)** - Twelve hand-holding worked examples
  with expected output and visual walkthroughs: from first contact through
  sync, drift, audit, bootstrap, rollback, alerts, PR bot, governance and
  Fabric Environments.
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

v1.0.1 (2026-10-06). Reads the `.sigantry.toml` config file and the
`SIGANTRY_<SECTION>__<KEY>` settings overrides that these docs use, and still
reads the old names, `.fabric-dataops.toml` and `FDT_<SECTION>__<KEY>` (see the
[legacy-names note](migration/2.x-to-3.0.md)). It also makes the `diagnose-auth`
group check configurable, gives the workspace-bootstrap blueprints new folder
names, and carries security and bug fixes. `CHANGELOG.md` lists the changes;
read its "Upgrading from 1.0.0" section before upgrading.

v1.0.0 (2026-09-19). Initial open-source standalone release of Sigantry on PyPI.
Includes dual-mode workspace bootstrapping (`workspace bootstrap`),
brownfield adoption (`sync pull`), drift detection (`diff`), deployment
and rollback (`deploy`), integrity-checked release ledger (`release`,
[threat model](reference/audit-ledger-threat-model.md)),
headless PR bot (`pr-bot`), and Fabric Environment wheel management
(`env sync`, `env sync-all`, and `env reconcile`, which removes superseded
wheel versions and publishes once).

**Version numbers.** 1.0.0 is the first public release of `sigantry`. Its
code continues an internal 3.x line: public 1.0.0 corresponds to internal
3.4.x, and the version numbers restarted at 1.0.0 for the public package.
Version numbers such as v2.0, v3.0 or v3.1 in older pages, ADRs and
runbooks refer to that internal line; none of them is a release of
`sigantry` on PyPI.

See `docs/migration/2.x-to-3.0.md` for the legacy names the code
still reads and `CHANGELOG.md` for full release notes.
