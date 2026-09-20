# ADR-0017 — the PyPI distribution is `sigantry`, not `sigantry-core`

- **Status:** Accepted
- **Date:** 2026-09-20
- **Milestone:** v1.0 (open-source release)
- **Deciders:** maintainer + product owner
- **Context:** [ADR-0011](ADR-0011-rename-to-sigantry.md) decided the base distribution
  would be published as `sigantry-core`, keeping the bare `sigantry` name reserved for
  the CLI, the docs site, the GitHub organisation and a possible future meta-package.
  The v1.0.0 open-source release did not do that: `pyproject.toml` declares
  `name = "sigantry"`, and that is what was uploaded. This ADR records the change that
  actually shipped, which ADR-0011 never anticipated.

## Context

The decision record and the shipped artifact disagreed, and the artifact won by being
public:

| Claim | State |
|---|---|
| ADR-0011 naming table | base distribution is `sigantry-core` |
| `pyproject.toml:6` | `name = "sigantry"` |
| PyPI simple index, `sigantry` | **200** — version 1.0.0, wheel + sdist, Apache-2.0 |
| PyPI simple index, `sigantry-core` | **404** — no such distribution |

No ADR recorded the change. The consequence was mechanical: documentation, templates and
two of the project's own workflows kept telling readers to
`pip install sigantry-core`, an instruction that has never worked against public PyPI,
because they were faithfully implementing the decision on record.

## Decision

**The shipped distribution name `sigantry` is canonical. ADR-0011's naming table is
superseded in part.**

| Old (ADR-0011) | Canonical from v1.0.0 |
|---|---|
| Package root (dist) | ~~`sigantry-core`~~ → **`sigantry`** |
| Package root (import) | `sigantry_core` — **unchanged** |
| CLI entry point | `sigantry` — unchanged |
| Plugin distributions | unchanged — ADR-0011's plugin rows are unaffected |

Everything else in ADR-0011 stands: the product name, the `sigantry_core` import
namespace, the PowerShell module names and the plugin distribution names are
unaffected (see ADR-0011's naming table for those).

## Rationale

1. **A published release is immutable.** PyPI `sigantry` 1.0.0 is public and cannot be
   withdrawn into a different name. Republishing as `sigantry-core` would mean two names
   on the index, two import stories for the same code, and abandoning a live release —
   strictly worse than amending a decision record.
2. **The import name never moved.** ADR-0011's substantive decision — the `sigantry_core`
   namespace and the `sigantry` CLI — shipped exactly as decided. Only the distribution
   label differs, and the distribution label is the one part with a public, immutable
   record.
3. **The reserved-root rationale has been overtaken.** ADR-0011 kept `sigantry` free for
   "the CLI, the docs site, the GitHub org, and potentially a future meta-package". The
   CLI, the site and the org do not consume a PyPI name, and the base package now holds
   the root name directly, so there is nothing left for a meta-package to add. If a
   meta-package is ever wanted it takes a suffixed name, not the root.
4. **Fixing the text without fixing the record re-arms the failure.** The stale install
   instructions were not carelessness; they were consistent with the only decision on
   file. Correcting them while leaving ADR-0011 standing would reproduce them.

## Consequences

- Every install instruction reads `pip install sigantry`. Documentation is corrected in
  the same change set as this ADR.
- **`sigantry-core` does not resolve on PyPI and is not owned by this project.** An
  unregistered name that the project's own documentation once told people to install is
  a dependency-confusion surface. Removing the documented instruction removes the
  documented victim path; registering `sigantry-core` defensively — as an **empty,
  yanked reservation**, never a working alias that would silently resurrect the stale
  instructions — is recommended and is an owner action. PyPI's current name-reservation
  policy should be confirmed from `docs.pypi.org` before acting; it has not been
  confirmed here.
- Shipped templates and the project's own workflows still name `sigantry-core` in places.
  They are corrected separately: template copies already taken by adopters cannot be
  repaired by a `pip install --upgrade`, so that change ships with a migration note.
- Historical documents that describe the v2.x → v3.0 rename keep saying `sigantry-core`,
  because that is what was true then. They point here for the current name rather than
  being rewritten.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Republish as `sigantry-core` to match ADR-0011** | Abandons an immutable public 1.0.0, creates a second index entry for one codebase, and breaks anyone who already installed `sigantry`. Changes the thing that cannot be changed to match the thing that can. |
| **Ship a working `sigantry-core` alias that depends on `sigantry`** | Makes every stale instruction silently work again, removing the pressure to fix them and permanently supporting two install names. |
| **Leave ADR-0011 alone and fix only the docs** | The decision record would still contradict the shipped artifact — exactly the condition that produced the stale instructions. |

## Supersedes / superseded by

- **Supersedes in part:** [ADR-0011 — Rename to Sigantry](ADR-0011-rename-to-sigantry.md)
  (the base-distribution row of its naming table, and the "`sigantry` as sole prefix"
  rejection in its Alternatives section). The rest of ADR-0011 stands.
- Superseded by: none.
- Related: [ADR-0010 — Commercial Model](ADR-0010-commercial-model.md);
  [migration guide](../migration/2.x-to-3.0.md).
