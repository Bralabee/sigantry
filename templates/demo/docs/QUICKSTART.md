# Demo quickstart

> The demo does not run end to end on the shipped demo tree yet.
>
> The canonical quickstart for trying Sigantry against the
> demo lives at `docs/demo/QUICKSTART.md` in the sigantry
> monorepo (Plan 15-04 / DEMO-04). This file is the in-template
> stub -- when an operator mirrors `templates/demo/` into the
> public `demo-sigantry` repo, this file becomes the QUICKSTART
> new visitors land on.

## Try it

See the canonical version: <DOCS-URL-DEMO-QUICKSTART>

## Prerequisites

- Python 3.11 or later (`requires-python` is `>=3.11`)
- `pip install sigantry`
- Access to the demo Fabric tenant (its operator provisions it per
  `docs/runbooks/demo-tenant-operator.md` in the sigantry monorepo).

## Steps

1. Clone this repo (`gh repo clone sigantry/demo-sigantry`) and `cd`
   into it. The public mirror is planned but not provisioned yet; until
   it is, work in a copy of `templates/demo/` from the sigantry monorepo.
2. Set environment variables:
   - `SIGANTRY_DEMO_TENANT_ID`
   - `SIGANTRY_DEMO_WORKSPACE_ID`
   - `SIGANTRY_DEMO_CAPACITY_ID`
   - `SIGANTRY_DEMO_FABRIC_TOKEN`
   - `SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD`, `SIGANTRY_FABRIC_WORKSPACE_ID_PROD`,
     `SIGANTRY_FABRIC_CAPACITY_ID_PREPROD` and `SIGANTRY_FABRIC_CAPACITY_ID_PROD`
     (the demo uses only `DEV`, but step 3 needs every `$ENV:` reference
     set; set these to the demo IDs)
3. Validate parameters: `sigantry config validate parameters.yml`
4. Reconcile the workspace's folder layout with the manifest: `sigantry sync apply --manifest sync.yml --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID`
5. Compare the workspace with the manifest: `sigantry diff --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID --manifest sync.yml`
6. Push a change under `fabric_items/` to `main` for the demo CI
   workflow, whose steps are deploy, record and diff.
