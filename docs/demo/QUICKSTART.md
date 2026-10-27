# Sigantry demo walkthrough

> **What the demo needs.** It runs end to end on a Fabric capacity: an F
> or P SKU, or a Fabric trial. A Power BI Premium Per User capacity hosts
> only its semantic model; `sigantry preflight --manifest sync.yml
> --params parameters.yml --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID"`
> reports that before anything is deployed. The end-to-end run used the
> GitHub Actions demo CI; the Azure DevOps pipeline has not been run.
>
> Phase 15 / DEMO-04. This walkthrough is for outside reviewers who
> did not build Sigantry.

## What you will do

1. Copy the demo tree
2. Set 4 environment variables (the operator pre-provisions the demo tenant)
3. Validate `parameters.yml`
4. Run `sigantry sync apply` with the demo manifest
5. Compare the workspace with the manifest via `sigantry diff`
6. Push a small change to `main` for the demo CI

## Prerequisites

- **Python 3.11 or 3.12** (`requires-python` is `>=3.11` with no upper bound; CI tests 3.11 and 3.12)
- **A clone of this repository** (`Bralabee/sigantry`), for the demo tree
- **A demo Fabric tenant** -- operator-provisioned per
  [docs/runbooks/demo-tenant-operator.md](../runbooks/demo-tenant-operator.md).
  Out-of-band setup gives you four values you will set as environment
  variables in Step 2: tenant ID, workspace ID, capacity ID, and the
  demo service principal's client secret (`SIGANTRY_DEMO_FABRIC_TOKEN`).

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

## Step 1 -- Copy the demo tree

The demo is `templates/demo/` in the sigantry repository; it is not
provisioned as its own repository. Copy it into a working directory
of your own:

```bash
cp -r sigantry/templates/demo ./sigantry-demo
cd sigantry-demo
```

The tree holds `parameters.yml`, `sync.yml`, `fabric_items/` (3 subdirs:
`LoadOrders.Notebook/`,
`RefreshOrdersDaily.DataPipeline/`, `OrdersAnalytics.SemanticModel/`),
`.github/`, `.azuredevops/`, `docs/`, `_partials/` and `README.md`.

## Step 2 -- Set environment variables

Set these 4 variables. The operator who provisioned the demo tenant
hands you the values out-of-band:

- `SIGANTRY_DEMO_TENANT_ID`
- `SIGANTRY_DEMO_WORKSPACE_ID`
- `SIGANTRY_DEMO_CAPACITY_ID`
- `SIGANTRY_DEMO_FABRIC_TOKEN`

Export them in your shell:

```bash
export SIGANTRY_DEMO_TENANT_ID="..."
export SIGANTRY_DEMO_WORKSPACE_ID="..."
export SIGANTRY_DEMO_CAPACITY_ID="..."
export SIGANTRY_DEMO_FABRIC_TOKEN="..."
```

## Step 3 -- Validate parameters.yml

Validate the `DEV` environment slot in `parameters.yml` (the slot the
demo uses):

```bash
sigantry config validate parameters.yml --environment DEV
```

This step checks `parameters.yml`, the file the demo CI's deploy step
reads, and resolves the `$ENV:` references for `DEV` (and `_ALL_`).

```text
OK -- 3 environment(s) parsed: DEV, PREPROD, PROD
```

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

On a newly provisioned workspace the diff lists the three demo items as
removed: step 4 places the items the workspace already holds but does
not publish new ones. The demo CI in step 6 deploys them, after which
`sigantry diff` reports no drift.

## Step 6 -- Push a change for the demo CI

Step 6 needs your copy to be a Git repository with a GitHub or ADO
remote of your own. Edit
`fabric_items/LoadOrders.Notebook/notebook-content.ipynb` (e.g. add a
comment line to the code cell), then:

```bash
git checkout -b feature/demo-quickstart-touch
git add fabric_items/LoadOrders.Notebook/notebook-content.ipynb
git commit -m "demo: add comment to LoadOrders notebook"
git push -u origin feature/demo-quickstart-touch
```

Open a PR against `main` on that repository (GitHub or ADO) and
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
