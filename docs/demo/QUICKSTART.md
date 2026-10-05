# Sigantry demo -- try it in 15 minutes

> Phase 15 / DEMO-04. This walkthrough is for outside reviewers who
> did not build Sigantry. It does not run end to end on the shipped
> demo tree yet: Step 4's `sync apply` and the demo CI's deploy step in
> Step 6 both exit with code 1 (see those steps). If any step takes
> more than 3 minutes, jump to the
> [Troubleshooting](#troubleshooting) section at the bottom.

## What you will do

1. Clone the demo-sigantry repo (~1 min)
2. Set 4 environment variables (~3 min -- operator pre-provisions the demo tenant)
3. Validate `parameters.yml` (~1 min)
4. Sync the 4 sample Fabric items into the demo workspace (~5 min)
5. Confirm zero drift via `sigantry diff` (~1 min)
6. Push a small change to `main` and watch the demo CI run (~4 min)

## Prerequisites

- **Python 3.11 or 3.12** (`requires-python` is `>=3.11` with no upper bound; CI tests 3.11 and 3.12)
- **gh CLI** for the clone (`gh --version`)
- **A demo Fabric tenant** -- operator-provisioned per
  [docs/runbooks/demo-tenant-operator.md](../runbooks/demo-tenant-operator.md).
  Out-of-band setup gives you four values you will set as environment
  variables in Step 2: tenant ID, workspace ID, capacity ID, Fabric
  token.

Install Sigantry once into a fresh virtualenv or conda env:

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install sigantry
```

Verify the install:

```bash
sigantry --help
```

You should see at least 17 top-level subcommands listed (`workspace`,
`deploy`, `release`, `sync`, `diff`, `config`, `pr-bot`, ...).

## Step 1 -- Clone the demo repo

```bash
gh repo clone sigantry/demo-sigantry
cd demo-sigantry
```

Expected: ~10 files at the root: `parameters.yml`, `sync.yml`,
`fabric_items/` (4 subdirs: `Sales.Lakehouse/`, `LoadOrders.Notebook/`,
`RefreshOrdersDaily.DataPipeline/`, `OrdersAnalytics.SemanticModel/`),
`.github/workflows/`, `.azuredevops/`, `docs/`, `_partials/`,
`README.md`.

## Step 2 -- Set environment variables

Set these 4 variables. The operator who provisioned the demo tenant
hands you the values out-of-band:

- `SIGANTRY_DEMO_TENANT_ID`
- `SIGANTRY_DEMO_WORKSPACE_ID`
- `SIGANTRY_DEMO_CAPACITY_ID`
- `SIGANTRY_DEMO_FABRIC_TOKEN`

The simplest path is inline export:

```bash
export SIGANTRY_DEMO_TENANT_ID="..."
export SIGANTRY_DEMO_WORKSPACE_ID="..."
export SIGANTRY_DEMO_CAPACITY_ID="..."
export SIGANTRY_DEMO_FABRIC_TOKEN="..."
```

If you prefer a dotenv-style file, copy `scripts/live-creds.template`
from the [sigantry monorepo](https://github.com/Bralabee/sigantry)
into `.env.live` (gitignored), populate the four `SIGANTRY_DEMO_*`
keys, and `source` it.

## Step 3 -- Validate parameters.yml

```bash
sigantry config validate parameters.yml
```

Expected output:

```
OK -- 3 environment(s) parsed: DEV, PREPROD, PROD
```

The validator confirms every `$ENV:` reference resolves to a set
environment variable. If you see "missing env var" errors, jump back
to Step 2.

## Step 4 -- Sync the demo items into the demo workspace

```bash
sigantry sync apply \
  --manifest sync.yml \
  --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID"
```

On the shipped demo tree this command exits with code 1 before it
changes the workspace: no `sync apply` packager handles the `Lakehouse`
item, and the notebook packager reads only `.ipynb` files. Without
`--with-publish`, `sync apply` publishes no items in any case; it
creates and moves folders and places items the workspace already holds.

Re-running this command is idempotent -- if the workspace already
matches the manifest, the second run is a no-op.

## Step 5 -- Confirm zero drift

```bash
sigantry diff \
  --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID" \
  --manifest sync.yml
```

Expected output: `no drift detected` (exit 0).

`sigantry diff` compares item names, types and folders only, so
Lakehouse tables, which live in OneLake rather than Git, never show as
drift (see [Troubleshooting](#troubleshooting)).

## Step 6 -- Push a change and watch CI

Edit `fabric_items/LoadOrders.Notebook/notebook-content.py` (e.g.
add a comment), then:

```bash
git checkout -b feature/demo-quickstart-touch
git add fabric_items/LoadOrders.Notebook/notebook-content.py
git commit -m "demo: add comment to LoadOrders notebook"
git push -u origin feature/demo-quickstart-touch
```

Open a PR against `main` on the demo-sigantry repo (GitHub or ADO).
After merge, the `sigantry-demo-ci` workflow runs on the push to
`main`: deploy -> record -> diff. It does not complete yet. The deploy
step exits with code 1, because `parameters.yml` references PREPROD and
PROD variables that the step does not set, so the record and diff steps
do not run. The record step would exit with code 2 in any case (see
"Release record" below).

## What the walkthrough covers

By hand: `sigantry config validate`, `sigantry sync apply` and
`sigantry diff`. In the demo CI, which ships for both GitHub Actions and
Azure DevOps: `sigantry deploy run`, `sigantry release record` and
`sigantry diff`. It does not complete yet (see the note at the top).

**Release record** -- the demo CI's `sigantry release record` step
writes no record yet. It passes `--workspace-id`, which `release record`
does not accept (it takes `--workspace`), and it omits options the
command requires (`--provider`, `--work-items` and `--approver`), so the
command exits with code 2 before writing anything. Run
`sigantry release record --help` to record a release by hand.

## Troubleshooting

- **`sigantry config validate` fails on env names** -- ensure
  `parameters.yml`'s env keys are exactly `DEV`, `PREPROD`, `PROD`.
  A `DEMO` env name fails the whitelist; this is intentional. The
  demo workspace lives behind the `DEV` slot whose `$ENV:` references
  point at the four `SIGANTRY_DEMO_*` env vars.
- **Lakehouse tables** -- table data lives in OneLake, not in Git, and
  `sigantry diff` does not see it: a table or column added in the
  Fabric portal is not reported as drift. Use the demo notebook
  (`LoadOrders.Notebook`) to create tables, so the table-creation logic
  is in Git. The runbook
  [docs/runbooks/demo-tenant-operator.md](../runbooks/demo-tenant-operator.md)
  has the full explanation under "Lakehouse Git limitation".
- **`SIGANTRY_DEMO_FABRIC_TOKEN` rejected** -- tokens rotate every 90
  days. Ask the demo-tenant operator for a fresh value (procedure in
  the operator runbook, section 2).
- **`gh repo clone` fails** -- the public demo repo lives at
  `sigantry/demo-sigantry`. If the URL has not been published yet,
  the demo is still in pre-mirror state; check the latest
  `<DEMO-URL>` in the
  [PRODUCT-BRIEF Demo section](../PRODUCT-BRIEF.md#demo).

## Next steps

- Read the [PRODUCT-BRIEF](../PRODUCT-BRIEF.md) for the full Sigantry
  adoption story.
- Read [docs/reference/parameters-yml.md](../reference/parameters-yml.md)
  for the full `find_replace` / `key_value_replace` / `spark_pool`
  shape reference.
