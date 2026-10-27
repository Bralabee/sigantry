# Demo quickstart

> The demo runs end to end on a Fabric capacity (an F or P SKU, or a
> Fabric trial) through its GitHub Actions CI; what it needs is set out
> at the top of `docs/demo/QUICKSTART.md` in the sigantry repository.
>
> The canonical quickstart for trying Sigantry against the
> demo lives at `docs/demo/QUICKSTART.md` in the sigantry
> monorepo (Plan 15-04 / DEMO-04). This file is the in-template
> stub; it travels with any copy of `templates/demo/`. The demo is
> not provisioned as its own repository.

## Try it

See the canonical version: <DOCS-URL-DEMO-QUICKSTART>

## Prerequisites

- Python 3.11 or later (`requires-python` is `>=3.11`)
- `pip install sigantry`
- Access to the demo Fabric tenant (its operator provisions it per
  `docs/runbooks/demo-tenant-operator.md` in the sigantry monorepo).

## Steps

1. Copy `templates/demo/` out of the sigantry monorepo into a working
   directory of your own and `cd` into it.
2. Set environment variables:
   - `SIGANTRY_DEMO_TENANT_ID`
   - `SIGANTRY_DEMO_WORKSPACE_ID`
   - `SIGANTRY_DEMO_CAPACITY_ID`
   - `SIGANTRY_DEMO_FABRIC_TOKEN`
3. Validate parameters: `sigantry config validate parameters.yml --environment DEV`
4. Reconcile the workspace's folder layout with the manifest: `sigantry sync apply --manifest sync.yml --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID`
5. Compare the workspace with the manifest: `sigantry diff --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID --manifest sync.yml`
6. Push a change under `fabric_items/` to `main` for the demo CI
   workflow, whose steps are deploy, record and diff.
