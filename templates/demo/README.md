# Sigantry demo -- public adoption surface

> This tree is the in-tree source-of-truth for the public
> **demo-sigantry** repository (mirrored to GitHub + ADO by an
> operator per `.planning/phases/15-public-demo-environment/15-HUMAN-UAT.md`).

## What this is

A reproducible, end-to-end demo of Sigantry -- Apache-2.0 open-source
Fabric DataOps. A prospect or evaluator can:

1. **Watch** the 90-second walkthrough mp4 (link below -- published
   to GitHub Releases on first operator capture).
2. **Try** Sigantry on their own laptop in 15 minutes via
   `docs/demo/QUICKSTART.md`.
3. **Inspect** the audit ledger, drift detection, rollback, and
   PR-bot output produced by THIS repo's own CI runs against a
   dedicated demo Fabric tenant.

The demo IS Sigantry dogfooded in public: every feature is
exercised end-to-end on a real (demo) tenant.

## Layout

| Path | Purpose |
| --- | --- |
| `parameters.yml` | fabric-cicd parameter substitution for the demo tenant |
| `sync.yml` | sync manifest enumerating the 4 sample items |
| `fabric_items/Sales.Lakehouse/` | demo lakehouse (bronze tier) |
| `fabric_items/LoadOrders.Notebook/` | demo notebook (Synapse pyspark) |
| `fabric_items/RefreshOrdersDaily.DataPipeline/` | demo data pipeline |
| `fabric_items/OrdersAnalytics.SemanticModel/` | demo semantic model (TMDL) |
| `.github/workflows/sigantry-demo-ci.yml` | demo CI -- runs deploy + record + diff on every push (Plan 15-03) |
| `.azuredevops/sigantry-demo-ci.yml` | ADO equivalent of the demo CI workflow (Plan 15-03) |
| `.github/workflows/pr-bot.yml` | PR-bot from `sigantry-starter` (byte-equal to starter) |
| `.azuredevops/jobs/pr-bot.yml` | ADO PR-bot job template (byte-equal to starter) |

## Quickstart

See `docs/demo/QUICKSTART.md` (15-minute walkthrough; Plan 15-04
ships the canonical content; this template's `docs/QUICKSTART.md`
is a stub pointing back to the canonical location).

## Walkthrough mp4

A 90-second mp4 walkthrough produced by the Remotion pipeline at
`scripts/remotion/` is published to this repo's GitHub Releases
after operator capture (15-HUMAN-UAT Test 3). Initial placeholder
URL: `<DEMO-URL>` -- operator updates after Test 1 mirror succeeds.

## Operator runbook

`docs/runbooks/demo-tenant-operator.md` (Plan 15-04) covers tenant
provisioning, secret rotation, mp4 re-recording cadence, and the
Lakehouse Git limitation (table data lives in OneLake, not Git --
expect `sigantry diff` to report metadata-only changes).

## License

Apache-2.0 (per ADR-0010 and the Sigantry product brief).
