# Sigantry demo -- try it in 15 minutes

> Phase 15 / DEMO-04. This walkthrough is for outside reviewers who
> did not build Sigantry. Total time: ~15 minutes from a fresh laptop
> to a green deploy + audit-record + diff against the demo Fabric
> tenant. If any step takes more than 3 minutes, jump to the
> [Troubleshooting](#troubleshooting) section at the bottom.

## What you will do

1. Clone the demo-sigantry repo (~1 min)
2. Set 4 environment variables (~3 min -- operator pre-provisions the demo tenant)
3. Validate `parameters.yml` (~1 min)
4. Sync the 4 sample Fabric items into the demo workspace (~5 min)
5. Confirm zero drift via `sigantry diff` (~1 min)
6. Push a small change to `main` and watch the demo CI run (~4 min)

## Prerequisites

- **Python 3.11 or 3.12** (`requires-python` is `>=3.11,<3.13`; 3.13 is refused at install)
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
pip install "sigantry-core>=3.0.0"
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
from the [sigantry-core monorepo](https://github.com/sigantry/sigantry-core)
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

Expected output: 4 items deployed
(`Sales.Lakehouse`, `LoadOrders.Notebook`,
`RefreshOrdersDaily.DataPipeline`, `OrdersAnalytics.SemanticModel`).

Re-running this command is idempotent -- if the workspace already
matches the manifest, the second run is a no-op.

## Step 5 -- Confirm zero drift

```bash
sigantry diff \
  --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID" \
  --manifest sync.yml
```

Expected output: `no drift detected` (exit 0).

If you see drift on `Sales.Lakehouse` table data, jump to the
[Troubleshooting](#troubleshooting) section -- Lakehouse table data
lives in OneLake (not Git), so this is correct Microsoft Fabric
behaviour, not a Sigantry bug.

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
After merge, watch the `sigantry-demo-ci` workflow run on the push to
`main`: deploy -> record -> diff. Expect zero drift on the diff stage.

## What you proved

In about 15 minutes, you exercised four Sigantry feature surfaces
end-to-end against a real Fabric tenant:

- **Phase 4 deploy** -- `sigantry deploy` (and its `sync apply` peer)
  pushes 4 Fabric item types (Lakehouse + Notebook + DataPipeline +
  SemanticModel) via `fabric-cicd`.
- **Phase 11 audit** -- the demo CI's `sigantry release record` step
  writes an immutable JSONL DeployRecord traceable back to the GitHub
  commit SHA.
- **Phase 13 drift** -- `sigantry diff` (and the demo CI's
  `--fail-on-drift` flag) catches divergence between the Git
  source-of-truth and tenant state.
- **Dual-CI parity** -- the same three-command demo loop ships in
  GitHub Actions and Azure DevOps; CI in either system tells the
  same audit story.

## Troubleshooting

- **`sigantry config validate` fails on env names** -- ensure
  `parameters.yml`'s env keys are exactly `DEV`, `PREPROD`, `PROD`.
  A `DEMO` env name fails the whitelist; this is intentional. The
  demo workspace lives behind the `DEV` slot whose `$ENV:` references
  point at the four `SIGANTRY_DEMO_*` env vars.
- **Lakehouse drift on first run** -- Lakehouse table data lives in
  OneLake, NOT in Git. Adding a table or column in the Fabric portal
  surfaces as drift in `sigantry diff`. Use the demo notebook
  (`LoadOrders.Notebook`) to create tables, so the round-trip stays
  stable. The runbook
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

- Read the [walkthrough script](walkthrough-script.md) for the
  narrative behind the recorded mp4.
- Watch the [demo walkthrough mp4](https://github.com/sigantry/demo-sigantry/releases/latest)
  (~90 seconds; produced by `scripts/remotion/`).
- Read the [PRODUCT-BRIEF](../PRODUCT-BRIEF.md) for the full Sigantry
  adoption story.
- Read [docs/reference/parameters-yml.md](../reference/parameters-yml.md)
  for the full `find_replace` / `key_value_replace` / `spark_pool`
  shape reference.
