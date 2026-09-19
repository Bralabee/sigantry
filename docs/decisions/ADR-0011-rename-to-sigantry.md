# ADR-0011 — Rename `fabric-dataops-toolkits` to `sigantry-core`

- **Status:** Accepted
- **Date:** 2026-04-24
- **Milestone:** v3.0 (Productization)
- **Deciders:** platform team + product owner
- **Context:** The v1.0 + v2.0 codebase shipped under the internal name `fabric-dataops-toolkits` (plus `fabric-dataops-toolkits-hs2` for the HS2 plugin). v3.0 productises the project into an open-source offering and needs a brandable, distinct name. Per the [PRODUCT-BRIEF.md](../PRODUCT-BRIEF.md) and BRIEF-05 / BRIEF-06 scope, the rename must execute in Phase 10 before any v3 code lands so Phase 11+ builds on the `sigantry_core.*` namespace from the start.

## Decision

Rename to **Sigantry**:

| Old | New |
|-----|-----|
| Product name | Sigantry |
| Package root | `sigantry-core` (dist) / `sigantry_core` (import) |
| HS2 plugin | `sigantry-hs2` (dist) / `sigantry_hs2` (import) |
| Future JToye plugin | `sigantry-jtoye` (dist) / `sigantry_jtoye` (import) |
| CLI entry point | `sigantry` |
| PowerShell module (base) | `Sigantry` (was `Fabric`) |
| PowerShell module (HS2) | `SigantryHs2` (was `Hs2Fabric`) |
| GitHub organisation | `sigantry` (pending trademark clearance — see Risk section below) |
| Documentation site | `sigantry.dev` or equivalent (pending domain clearance) |
| Env-var prefix (demo-tier) | `SIGANTRY_DEMO_*` (new; mirrors `HS2_FABRIC_TEST_*` pattern) |

### Shim / backwards-compat strategy

- A thin `fabric-dataops-toolkits` distribution package ships with v3.0 that **re-exports** the `sigantry_core` public API verbatim.
- The shim emits a `DeprecationWarning` on import pointing to the migration guide.
- Shim lifetime: **one minor release only.** Drops in v3.1.
- The same pattern applies to `fabric-dataops-toolkits-hs2` → `sigantry-hs2`.
- The PowerShell `Fabric` module also ships a v3.0 shim manifest that re-dispatches to `Sigantry` with a deprecation warning; drops in v3.1.

### Migration guide

Published as `docs/migration/2.x-to-3.0.md`. Covers:

1. Search-and-replace rules (`import fabric_dataops_toolkits` → `import sigantry_core`, etc.).
2. Config-file migration (`.fabric-dataops.toml` → `.sigantry.toml` — also supports loading the old filename for one minor with a deprecation warning).
3. Env-var migration (`FABRIC_DATAOPS_*` → `SIGANTRY_*`, with the old names honoured for one minor).
4. Entry-point group migration (`fabric_dataops_toolkits.deploy_profiles` → `sigantry.deploy_profiles`; the registry reads both groups during v3.0).
5. PowerShell `Import-Module Fabric` → `Import-Module Sigantry` with alias-only-for-one-minor.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| **Keep `fabric-dataops-toolkits`** | Not brandable, descriptive-only, trademark-unsuitable. Signals "internal utility" to outside adopters. |
| **Rename to a generic like "FabricOps" / "DataOpsKit"** | Both are genuinely taken or too close to existing products; descriptive-generic names are weak trademarks. |
| **`sigantry` as sole prefix (no `-core` suffix)** | The base package is `sigantry-core` deliberately so the root `sigantry` name stays available as the CLI, the docs site, the GitHub org, and potentially a future meta-package. |
| **Defer rename to v3.1 after feature-complete v3.0** | Requires every v3 feature to be built under the old name and then re-migrated. Doubles the rename cost. |
| **Use codename during v3.0, rename before public launch** | Codenames leak. A public `v3.0.0` under the old name would ship npm/PyPI entries and git tags that long outlive the rename window. |

## Rationale

1. **Productisation needs brandable identity.** An adopter recommending "install `fabric-dataops-toolkits`" feels like recommending "install the standard library". A name distinct from "Microsoft Fabric" and from any vendor label is essential for trademark, marketing, and mindshare.
2. **Do the rename before Phase 11** because the `sigantry` CLI, `sigantry release record` command, `SIGANTRY_*` env vars, and `sigantry.*` entry-point groups are all spec'd in v3 REQs. Building them under the old name and migrating after wastes effort.
3. **Shim for exactly one minor.** Longer shim lifetime means perpetual import-path drift in user code. Shorter is a harder break. One minor matches the risk profile of a pre-1.0 OSS product that hasn't yet accumulated deep downstream adoption.

## Consequences

### Inside the repo

- Python packages move: `fabric_dataops_toolkits/` → `sigantry_core/`, `fabric-dataops-toolkits-hs2/` → `sigantry-hs2/` (dir-level renames).
- `pyproject.toml` files updated for both; `[project].name` and entry-point `[project.entry-points.*]` keys change.
- PowerShell: `Fabric/` → `Sigantry/`, `Hs2Fabric/` → `SigantryHs2/`. `.psd1` manifests re-written.
- ADO YAML templates: references to `fabric-dataops-toolkits` repo/artefact names updated.
- Bicep `resourcePrefix` and `tagPrefix` defaults in plugin inputs: **unchanged** — those are consumer-chosen, not product-branded. Only the `runbookBaseUrl` default moves to the Sigantry docs site.
- CLI entry point: `fabric-dataops-toolkits` → `sigantry`. Shim alias `fabric-dataops-toolkits` → `sigantry` for one minor.
- Doctor subcommand: `fabric-dataops-toolkits doctor` → `sigantry doctor`. Shim.
- CHANGELOG / README / mkdocs / README.md: rewritten.
- Git history is preserved; the rename is a tracked rename (`git mv`) so `git blame` continues to work.

### Outside the repo

- New public GitHub org (`sigantry`) created. HS2's existing `Bralabee/fabric_dataops` GitHub mirror becomes the cutover source; history is preserved.
- New PyPI project names (`sigantry-core`, `sigantry-hs2`). Existing ADO Artifacts feed publishes both old and new names during v3.0.
- Sphinx/MkDocs site rebranded; `docs/index.md` opens with the rename notice for v3.0.
- Consumer repos (AIMS, DQ, future JToye) get a one-line search-and-replace migration following the guide.
- Social handles / blog posts / external references are out of scope for this ADR (marketing phase).

## Risks

| Risk | Mitigation |
|------|------------|
| **V3-RISK-1 — trademark/domain/PyPI-name conflict on "Sigantry"** | Per user directive 2026-04-24: **build first, rename again later if clearance surfaces a conflict.** Clearance checks (USPTO TESS, WIPO, PyPI, npm, Docker Hub, GitHub org availability, `.com`/`.dev`/`.io` domain) are scheduled as a Phase 10 closing task but do NOT gate Phase 10 completion. If a conflict is found, a second rename follows in v3.1 — the shim strategy documented here makes that a low-cost operation. |
| Two packages with similar names confusing adopters during the shim window | Deprecation warnings from the shim direct users to the migration guide. Migration guide is published on day 1 of v3.0. |
| Downstream CI pipelines pinned to `fabric-dataops-toolkits==2.x` | The shim package on PyPI/Artifacts keeps `pip install fabric-dataops-toolkits==3.0.x` working; it pulls in `sigantry-core==3.0.x` under the hood. |
| PowerShell galleries / module caches | Shim `Fabric` module is published for v3.0 only; drops in v3.1 per the same schedule as Python. |
| ADO repo rename breaks existing service-connection / pipeline references | The HS2 ADO org remains the source for HS2-specific consumption. GitHub becomes canonical for upstream OSS. HS2's ADO `fabric-dataops-toolkits` repo is renamed on the same branch-cut as the code rename, and service-connection references are rewritten in a dedicated plan inside Phase 10. |
| Documentation link-rot (internal cross-references + external inbound links) | `docs/` link-checker CI step already exists. Add a post-rename redirect map for public inbound links. |

## Execution plan (Phase 10 plan breakdown — preview)

The actual rename is tracked as its own plan inside Phase 10, separate from this ADR. High level:

1. **Plan 10-R1:** Generate a mechanical rename patch via `rg` + `sed` over all text (Python, PS, YAML, Bicep, Markdown, tests) on a dedicated feature branch. Green the full test matrix.
2. **Plan 10-R2:** Rewrite `pyproject.toml` / `.psd1` manifests. Add the shim packages. Publish side-by-side pre-release versions to the ADO feed and PyPI test instance.
3. **Plan 10-R3:** Migration guide `docs/migration/2.x-to-3.0.md` + docs rebrand + CHANGELOG entry.
4. **Plan 10-R4:** Cutover — merge the rename, publish v3.0.0-alpha packages under new + shim names, tag. Trademark clearance background task fires here.

## Supersedes / superseded by

- Supersedes: none (first rename).
- Superseded by: none.
- Related: [ADR-0010 — Commercial Model](ADR-0010-commercial-model.md); [Seam Map](../reference/seam-map.md); [PRODUCT-BRIEF.md](../PRODUCT-BRIEF.md).

---

*Decision captured: 2026-04-24 (milestone v3.0 Phase 10, BRIEF-05). V3-RISK-1 deferred per user directive — clearance is tracked but not a blocker.*
