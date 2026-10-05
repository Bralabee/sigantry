# Sigantry demo -- public adoption surface

> This tree is the in-tree source-of-truth for the public
> **demo-sigantry** repository (mirrored to GitHub + ADO by an
> operator per `.planning/phases/15-public-demo-environment/15-HUMAN-UAT.md`).

## What this is

A demo of Sigantry -- Apache-2.0 open-source
Fabric DataOps. A prospect or evaluator can:

1. **Try** Sigantry on their own laptop via
   `docs/demo/QUICKSTART.md`.
2. **Read** this repo's demo CI (deploy + record + diff against a
   dedicated demo Fabric tenant) and its PR bot.

The demo does not run end to end yet: on this tree
`sigantry sync apply` and the demo CI's deploy step exit with code 1,
and the CI's record step would exit with code 2, so its runs write no
audit record and run no drift check.

## Layout

| Path | Purpose |
| --- | --- |
| `parameters.yml` | fabric-cicd parameter substitution for the demo tenant |
| `sync.yml` | sync manifest enumerating the 4 sample items |
| `fabric_items/Sales.Lakehouse/` | demo lakehouse (bronze tier) |
| `fabric_items/LoadOrders.Notebook/` | demo notebook (Synapse pyspark) |
| `fabric_items/RefreshOrdersDaily.DataPipeline/` | demo data pipeline |
| `fabric_items/OrdersAnalytics.SemanticModel/` | demo semantic model (TMDL) |
| `.github/workflows/sigantry-demo-ci.yml` | demo CI -- deploy + record + diff (does not complete yet; Plan 15-03) |
| `.azuredevops/sigantry-demo-ci.yml` | ADO equivalent of the demo CI workflow (Plan 15-03) |
| `.github/workflows/pr-bot.yml` | PR-bot from `sigantry-starter` (byte-equal to starter) |
| `.azuredevops/jobs/pr-bot.yml` | ADO PR-bot job template (byte-equal to starter) |

## Quickstart

See `docs/demo/QUICKSTART.md` (15-minute walkthrough; Plan 15-04
ships the canonical content; this template's `docs/QUICKSTART.md`
is a stub pointing back to the canonical location).

## Operator runbook

`docs/runbooks/demo-tenant-operator.md` (Plan 15-04) covers tenant
provisioning, secret rotation, and the
Lakehouse Git limitation (table data lives in OneLake, not Git --
expect `sigantry diff` to report metadata-only changes).

## License

Apache-2.0 (per ADR-0010 and the Sigantry product brief).
