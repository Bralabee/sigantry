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

- Python 3.11 or 3.12 (`requires-python` is `>=3.11,<3.13`)
- `pip install "sigantry>=3.0.0"`
- Access to a Fabric tenant (a free trial is fine -- see
  `docs/runbooks/demo-tenant-operator.md`).

## Steps

1. Clone this repo: `git clone https://github.com/sigantry/demo-sigantry.git`
2. Set environment variables (or copy `.env.example` to `.env.live`):
   - `SIGANTRY_DEMO_TENANT_ID`
   - `SIGANTRY_DEMO_WORKSPACE_ID`
   - `SIGANTRY_DEMO_CAPACITY_ID`
   - `SIGANTRY_DEMO_FABRIC_TOKEN`
3. Validate parameters: `sigantry config validate parameters.yml`
4. Sync demo items: `sigantry sync apply --manifest sync.yml --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID`
5. Confirm zero drift: `sigantry diff --workspace-id $SIGANTRY_DEMO_WORKSPACE_ID --manifest sync.yml`
6. Push a change to `main` and watch the demo CI workflow run.

## What's next

Open the demo workspace in the Fabric portal and inspect the four
deployed items. Watch the walkthrough mp4 (link in the README) for
the full WI -> deploy -> test -> approve -> audit -> rollback
round-trip narrative.
