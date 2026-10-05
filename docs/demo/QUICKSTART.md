# Sigantry demo walkthrough

> The demo does not run end to end on the shipped demo tree yet.
>
> Phase 15 / DEMO-04. This walkthrough is for outside reviewers who
> did not build Sigantry. It gives the demo's commands and says what
> each step is for.

## What you will do

1. Get the demo repo
2. Set 4 environment variables (the operator pre-provisions the demo tenant)
3. Validate `parameters.yml`
4. Run `sigantry sync apply` with the demo manifest
5. Compare the workspace with the manifest via `sigantry diff`
6. Push a small change to `main` for the demo CI

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

Check the install with:

```bash
sigantry --help
```

## Step 1 -- Get the demo repo

```bash
gh repo clone sigantry/demo-sigantry
cd demo-sigantry
```

`templates/demo/` in the sigantry repository holds the demo repo's
content: `parameters.yml`, `sync.yml`, `fabric_items/` (4 subdirs:
`Sales.Lakehouse/`, `LoadOrders.Notebook/`,
`RefreshOrdersDaily.DataPipeline/`, `OrdersAnalytics.SemanticModel/`),
`.github/`, `.azuredevops/`, `docs/`, `_partials/` and `README.md`.
The public mirror, `sigantry/demo-sigantry`, is planned but not
provisioned yet; until it is, work in a copy of `templates/demo/`.

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
into `.env.live` (do not commit it), populate the four `SIGANTRY_DEMO_*`
keys, and `source` it.

## Step 3 -- Validate parameters.yml

`parameters.yml` also references four PREPROD and PROD variables. The
demo uses only the `DEV` slot, but the validator needs every `$ENV:`
reference set, so point them at the demo values:

```bash
export SIGANTRY_FABRIC_WORKSPACE_ID_PREPROD="$SIGANTRY_DEMO_WORKSPACE_ID"
export SIGANTRY_FABRIC_WORKSPACE_ID_PROD="$SIGANTRY_DEMO_WORKSPACE_ID"
export SIGANTRY_FABRIC_CAPACITY_ID_PREPROD="$SIGANTRY_DEMO_CAPACITY_ID"
export SIGANTRY_FABRIC_CAPACITY_ID_PROD="$SIGANTRY_DEMO_CAPACITY_ID"
sigantry config validate parameters.yml
```

This step checks `parameters.yml`, the file the demo CI's deploy step
reads, and every `$ENV:` reference in it.

## Step 4 -- Run `sync apply` on the demo items

```bash
sigantry sync apply \
  --manifest sync.yml \
  --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID"
```

`sync apply` is for bringing the workspace's folders, and the placement
of the items the workspace already holds, in line with `sync.yml`. The
[sync apply runbook](../runbooks/sync/apply.md) says what it changes and
what it does not.

## Step 5 -- Compare the workspace with the manifest

```bash
sigantry diff \
  --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID" \
  --manifest sync.yml
```

`sigantry diff` is for checking the workspace against `sync.yml`. It
compares item names, types and folders, so Lakehouse tables, which live
in OneLake rather than Git, are outside it (see
[Troubleshooting](#troubleshooting)).

## Step 6 -- Push a change for the demo CI

Edit `fabric_items/LoadOrders.Notebook/notebook-content.py` (e.g.
add a comment), then:

```bash
git checkout -b feature/demo-quickstart-touch
git add fabric_items/LoadOrders.Notebook/notebook-content.py
git commit -m "demo: add comment to LoadOrders notebook"
git push -u origin feature/demo-quickstart-touch
```

Open a PR against `main` on the demo-sigantry repo (GitHub or ADO) and
merge it. The demo CI (`.github/workflows/sigantry-demo-ci.yml` or
`.azuredevops/sigantry-demo-ci.yml`) has a push trigger on `main` for
changes under `fabric_items/` or to `parameters.yml`. Its steps are for
deploying the demo items (`sigantry deploy run`), recording the release
(`sigantry release record`) and checking the workspace against
`sync.yml` (`sigantry diff --fail-on-drift`).

## What the walkthrough covers

By hand: `sigantry config validate`, `sigantry sync apply` and
`sigantry diff`. In the demo CI, which ships for both GitHub Actions and
Azure DevOps: `sigantry deploy run`, `sigantry release record` and
`sigantry diff`.

## Troubleshooting

- **Lakehouse tables** -- table data lives in OneLake, not in Git, and
  is outside what `sigantry diff` compares (item names, types and
  folders). Create tables from the demo notebook
  (`LoadOrders.Notebook`), so the table-creation logic is in Git. The
  runbook
  [docs/runbooks/demo-tenant-operator.md](../runbooks/demo-tenant-operator.md)
  has the full explanation under "Lakehouse Git limitation".

## Next steps

- Read the [PRODUCT-BRIEF](../PRODUCT-BRIEF.md) for the full Sigantry
  adoption story.
- Read [docs/reference/parameters-yml.md](../reference/parameters-yml.md)
  for the full `find_replace` / `key_value_replace` / `spark_pool`
  shape reference.
