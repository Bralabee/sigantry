# Demo quickstart

> The canonical 15-minute quickstart for trying Sigantry against the
> demo lives at `docs/demo/QUICKSTART.md` in the sigantry
> monorepo (Plan 15-04 / DEMO-04). This file is the in-template
> stub -- when an operator mirrors `templates/demo/` into the
> public `demo-sigantry` repo, this file becomes the QUICKSTART
> new visitors land on.

## Try it in 15 minutes

See the canonical version: <DOCS-URL-DEMO-QUICKSTART>

## Prerequisites

- Python 3.11 or later (`requires-python` is `>=3.11`)
- `pip install sigantry`
- Access to a Fabric tenant (a free trial is fine -- see
  `docs/runbooks/demo-tenant-operator.md`).

## Steps

1. Clone this repo: `git clone https://github.com/Bralabee/sigantry.git`
2. Set environment variables (or copy `.env.example` to `.env.live`):
   - `SIGANTRY_DEMO_TENANT_ID`
   - `SIGANTRY_DEMO_WORKSPACE_ID`
   - `SIGANTRY_DEMO_CAPACITY_ID`
   - `SIGANTRY_DEMO_FABRIC_TOKEN`
   - `SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD`, `SIGANTRY_FABRIC_WORKSPACE_ID_PROD`,
     `SIGANTRY_FABRIC_CAPACITY_ID_PREPROD` and `SIGANTRY_FABRIC_CAPACITY_ID_PROD`
     (the demo uses only `DEV`, but step 3 needs every `$ENV:` reference
     set; the demo IDs will do)
3. Validate parameters: `sigantry config validate parameters.yml`
4. Sync demo items: `sigantry sync apply --manifest sync.yml --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID`
   (on this tree it exits with code 1 before it changes the workspace)
5. Compare the workspace with the manifest: `sigantry diff --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID --manifest sync.yml`
6. Push a change to `main` and watch the demo CI workflow run. It does
   not complete yet: its deploy step exits with code 1.

## What's next

Open the demo workspace in the Fabric portal. The steps above do not
put the four sample items there yet (see the canonical quickstart).
