# Sigantry demo -- public adoption surface

> The demo runs end to end on a Fabric capacity (an F SKU or a Fabric trial); a Power BI Premium Per User
> capacity hosts only its semantic model.
>
> This tree is the demo. It lives in the sigantry repository as
> `templates/demo/` and is not provisioned as its own repository; to
> run its CI, copy it into a GitHub repo or ADO project of your own.

## What this is

A demo of Sigantry -- Apache-2.0 open-source
Fabric DataOps. A prospect or evaluator can:

1. **Try** Sigantry on their own laptop via
   `docs/demo/QUICKSTART.md`.
2. **Read** this repo's demo CI (deploy + record + diff against a
   dedicated demo Fabric tenant) and its PR bot.

## Layout

| Path | Purpose |
| --- | --- |
| `parameters.yml` | fabric-cicd parameter substitution for the demo tenant |
| `sync.yml` | sync manifest enumerating the 3 sample items |
| `fabric_items/LoadOrders.Notebook/` | demo notebook (Synapse pyspark) |
| `fabric_items/RefreshOrdersDaily.DataPipeline/` | demo data pipeline |
| `fabric_items/OrdersAnalytics.SemanticModel/` | demo semantic model (TMDL) |
| `.github/workflows/sigantry-demo-ci.yml` | demo CI -- deploy + record + diff (Plan 15-03) |
| `.azuredevops/sigantry-demo-ci.yml` | ADO equivalent of the demo CI workflow (Plan 15-03) |
| `.github/workflows/pr-bot.yml` | PR-bot from `sigantry-starter` (byte-equal to starter) |
| `.azuredevops/jobs/pr-bot.yml` | ADO PR-bot job template (byte-equal to starter) |

## Quickstart

See `docs/demo/QUICKSTART.md` (the walkthrough; Plan 15-04
ships the canonical content; this template's `docs/QUICKSTART.md`
is a stub pointing back to the canonical location).

## Operator runbook

`docs/runbooks/demo-tenant-operator.md` (Plan 15-04) covers tenant
provisioning, secret rotation, and the
Lakehouse Git limitation (table data lives in OneLake, not Git, and is
outside what `sigantry diff` compares: item names, types and folders).

## License

Apache-2.0 (per ADR-0010 and the Sigantry product brief).
