# Demo tenant -- operator runbook

> The demo runs end to end on a Fabric capacity (an F SKU or a Fabric trial); a Power BI Premium Per User
> capacity hosts only its semantic model.
>
> Phase 15 / DEMO-02 closure (DEMO-03, the walkthrough video build, is
> retired). This runbook is for the
> operator who provisions the demo Fabric tenant, rotates demo
> secrets, and wires those secrets into the repository that runs the demo
> CI (a copy of `templates/demo/`). Outside reviewers do
> NOT need this runbook -- send them to
> [docs/demo/QUICKSTART.md](../demo/QUICKSTART.md).

## Audience

- Sigantry maintainer with admin rights on the dedicated demo Fabric
  tenant
- Admin on the GitHub repo or ADO project that holds the copy of
  `templates/demo/` running the demo CI

## 1. Tenant provisioning (one-time)

Provision a dedicated Fabric tenant for the demo. Do NOT reuse a
production adopter tenant -- see Section 6 (dedicated SPN scope)
for why.

1. Create a Microsoft 365 dev tenant (or sub-tenant) with Fabric
   enabled.
2. Get a Fabric capacity: start a Fabric trial (account manager ->
   Start trial, which creates an FTL64 trial capacity) or create an F SKU
   capacity in Azure. A Premium Per User capacity is not enough: it
   cannot host the demo's Notebook or DataPipeline. Note the capacity ID
   -- this becomes `SIGANTRY_DEMO_CAPACITY_ID`.
3. Create a workspace named `demo-sigantry` and assign it to the
   capacity. Note the workspace ID -- this becomes
   `SIGANTRY_DEMO_WORKSPACE_ID`. Note the tenant ID -- this becomes
   `SIGANTRY_DEMO_TENANT_ID`.
4. Register a service principal named `sigantry-demo-spn` in Entra
   ID. Grant it Workspace Admin on the demo workspace ONLY (see
   Section 6). Generate a client secret -- this becomes
   `SIGANTRY_DEMO_FABRIC_TOKEN`. Note the app registration's
   application (client) ID as `SIGANTRY_DEMO_CLIENT_ID` (used as
   `DEMO_SPN_APP_ID` in the check below). Also create a seed work item
   or issue (for example `#1`) in the demo repository/project and note
   its ID as `SIGANTRY_DEMO_WORK_ITEM_ID` for `sigantry release record`.

Check that the SPN can call Fabric REST. The Fabric API takes a
Microsoft Entra access token, not the client secret, so sign in as the
SPN and request a token for the Fabric API first:

```bash
az login --service-principal \
  --username "$DEMO_SPN_APP_ID" \
  --password="$SIGANTRY_DEMO_FABRIC_TOKEN" \
  --tenant "$SIGANTRY_DEMO_TENANT_ID" \
  --allow-no-subscriptions
FABRIC_ACCESS_TOKEN=$(az account get-access-token \
  --resource https://api.fabric.microsoft.com \
  --query accessToken -o tsv)
curl -H "Authorization: Bearer $FABRIC_ACCESS_TOKEN" \
  "https://api.fabric.microsoft.com/v1/workspaces/$SIGANTRY_DEMO_WORKSPACE_ID/items"
```

These commands have not been run against a tenant.

## 2. Secret rotation cadence

Rotate `SIGANTRY_DEMO_FABRIC_TOKEN` every 90 days, or immediately on
suspicion of leakage. Procedure:

1. Generate a new SPN client secret in Entra ID.
2. Update the GitHub Actions secret on the repo that runs the demo
   CI. Set `DEMO_REPO` to it, as `owner/name`; the command refuses to
   run while it is unset:
   ```bash
   gh secret set SIGANTRY_DEMO_FABRIC_TOKEN \
     --repo "${DEMO_REPO:?set DEMO_REPO to owner/name}" \
     --body "<new-secret>"
   ```
3. Update the `sigantry-demo-secrets` ADO variable group (set
   `ADO_ORG` and `DEMO_ADO_PROJECT` first):
   ```bash
   az pipelines variable-group variable update \
     --org "${ADO_ORG:?set ADO_ORG}" \
     --project "${DEMO_ADO_PROJECT:?set DEMO_ADO_PROJECT}" \
     --group-id <id> --name SIGANTRY_DEMO_FABRIC_TOKEN \
     --value "<new-secret>" --secret true
   ```
4. Check the new secret with the commands in Section 1, with
   `SIGANTRY_DEMO_FABRIC_TOKEN` set to the new secret.
5. Revoke the old secret in Entra ID once that check succeeds.

The other demo secrets/variables
(`SIGANTRY_DEMO_TENANT_ID`, `SIGANTRY_DEMO_CLIENT_ID`, `_WORKSPACE_ID`,
`_CAPACITY_ID`, and `SIGANTRY_DEMO_WORK_ITEM_ID`) are
durable across the demo tenant's lifetime; rotate only on tenant
re-provisioning.

## 3. Lakehouse Git limitation (RESEARCH §Pitfall 1)

Microsoft Fabric does NOT track Lakehouse table data + column types
in Git. Only the container shell (`<Name>.Lakehouse/.platform` +
`lakehouse.metadata.json` + `shortcuts.metadata.json`) is
version-controlled, and `sigantry sync apply` does not register a
`Lakehouse` packager (so `templates/demo/fabric_items/` omits a
`.Lakehouse` directory). Tables, files, columns, and OneLake data live
OUTSIDE the Git source-of-truth.

Implications for the demo:

- Table data is outside what `sigantry diff` compares (item names,
  types and folders).
- Create tables from the demo notebook (`LoadOrders.Notebook`), so
  the table-creation logic is in Git.
- Document this in any external comms / FAQ. The QUICKSTART's
  Troubleshooting section already references this runbook.

Reference: [Microsoft Learn -- Lakehouse Git deployment limitations](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines).

## 4. Where the demo lives

The demo is `templates/demo/` in this repository. It is not
provisioned as its own repository, and sigantry CI never creates one.
To run the demo CI, copy the tree into a GitHub repo or ADO project you
administer. Run this from the root of the sigantry checkout, with
`DEMO_DIR` set to a directory that does not exist yet (outside the
checkout) and `DEMO_REMOTE_URL` to the empty repository you created.
It is one chain, so it stops at the first step that fails, including
an unset variable or an existing `DEMO_DIR`:

```bash
python scripts/export-demo.py --dry-run \
  && mkdir "${DEMO_DIR:?set DEMO_DIR to a new directory}" \
  && cp -r templates/demo/. "$DEMO_DIR"/ \
  && cd "$DEMO_DIR" && git init -b main && git add . \
  && git commit -m "Initial demo content" \
  && git remote add origin "${DEMO_REMOTE_URL:?set DEMO_REMOTE_URL}" \
  && git push -u origin main
```

Wire the `SIGANTRY_DEMO_*` GitHub Actions secrets
(`SIGANTRY_DEMO_TENANT_ID`, `SIGANTRY_DEMO_CLIENT_ID`,
`SIGANTRY_DEMO_FABRIC_TOKEN`, `SIGANTRY_DEMO_WORKSPACE_ID`,
`SIGANTRY_DEMO_CAPACITY_ID`, and optional `SIGANTRY_DEMO_WORK_ITEM_ID`)
and the `sigantry-demo-secrets` ADO variable group per Section 2.

## 5. Demo CI notes

This runbook has no recovery procedure for a failed demo CI run; the
checks below cover the usual causes.

- **Credentials:** check the SPN with the commands in Section 1;
  rotate per Section 2.
- **Tenant status:** check `https://admin.fabric.microsoft.com` and
  `https://status.azure.com`.
- **Drift check:** the diff step's `--fail-on-drift` flag is there to
  fail the run when the workspace and `sync.yml` disagree -- the safety
  net per RESEARCH §Pitfall 2 ("stale demo").
- **Pins:** a version pin for `sigantry` or `fabric-cicd` goes into the
  `pip install` step of
  `templates/demo/.github/workflows/sigantry-demo-ci.yml` and
  `templates/demo/.azuredevops/sigantry-demo-ci.yml` together
  (dual-CI parity rule).

## 6. Dedicated SPN scope (RESEARCH §Pitfall 6)

The demo SPN MUST hold permissions ONLY on the demo tenant. NEVER
grant role assignments outside the demo tenant.

Why this matters: a `sigantry` command run with another tenant's
credentials loaded acts on that tenant, so an operator who mixes up
the demo and a production adopter tenant can change the wrong one. The
demo SPN being narrowly-scoped is the structural safeguard;
least-privilege variable groups are the procedural one.

Verification:

```bash
az ad sp show --id "$DEMO_SPN_OBJECT_ID" \
  --query "appRoleAssignments[?resourceDisplayName!='sigantry-demo-spn']" \
  -o table
# Confirm zero entries outside the demo tenant.
```

Run this verification at SPN creation, after every secret rotation,
and quarterly.

## 7. Trademark / clearance -- V3-RISK-1

Per the phase-15 reviewed-todos record (maintainer-side, not in the
open-source tree): trademark + domain clearance for `Sigantry` was
DEFERRED in v3.0. Operator runs the clearance check at Test 0 of that
same checklist.

## See also

- [docs/demo/QUICKSTART.md](../demo/QUICKSTART.md) -- adopter-facing
  walkthrough.
- The maintainer's phase-15 operator checklist -- its operator gates
  (trademark, tenant and fresh-laptop reviewer). Held
  outside this repository; ask the maintainer.
