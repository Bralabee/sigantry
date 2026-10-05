# Demo tenant -- operator runbook

> The demo does not run end to end on the shipped demo tree yet.
>
> Phase 15 / DEMO-02 closure (DEMO-03, the walkthrough video build, is
> retired). This runbook is for the
> operator who provisions the demo Fabric tenant, rotates demo
> secrets, and maintains the public `demo-sigantry` repo. Outside reviewers do
> NOT need this runbook -- send them to
> [docs/demo/QUICKSTART.md](../demo/QUICKSTART.md).

## Audience

- Sigantry maintainer with admin rights on the dedicated demo Fabric
  tenant
- Sigantry maintainer with GitHub org admin + ADO Server admin on the
  `sigantry` org

## 1. Tenant provisioning (one-time)

Provision a dedicated Fabric tenant for the demo. Do NOT reuse a
production adopter tenant -- see Section 6 (dedicated SPN scope)
for why.

1. Create a Microsoft 365 dev tenant (or sub-tenant) with Fabric
   enabled.
2. In the Fabric admin portal, create an F2 (trial) capacity. Note
   the capacity ID -- this becomes `SIGANTRY_DEMO_CAPACITY_ID`.
3. Create a workspace named `demo-sigantry` and assign it to the
   capacity. Note the workspace ID -- this becomes
   `SIGANTRY_DEMO_WORKSPACE_ID`. Note the tenant ID -- this becomes
   `SIGANTRY_DEMO_TENANT_ID`.
4. Register a service principal named `sigantry-demo-spn` in Entra
   ID. Grant it Workspace Admin on the demo workspace ONLY (see
   Section 6). Generate a client secret -- this becomes
   `SIGANTRY_DEMO_FABRIC_TOKEN`. Note the app registration's
   application (client) ID as `DEMO_SPN_APP_ID` for the check below.

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
2. Update the GitHub Actions secret on the public `demo-sigantry`
   repo:
   ```bash
   gh secret set SIGANTRY_DEMO_FABRIC_TOKEN \
     --repo sigantry/demo-sigantry \
     --body "<new-secret>"
   ```
3. Update the `sigantry-demo-secrets` ADO variable group:
   ```bash
   az pipelines variable-group variable update \
     --org "$ADO_ORG" --project demo-sigantry \
     --group-id <id> --name SIGANTRY_DEMO_FABRIC_TOKEN \
     --value "<new-secret>" --secret true
   ```
4. Check the new secret with the commands in Section 1, with
   `SIGANTRY_DEMO_FABRIC_TOKEN` set to the new secret.
5. Revoke the old secret in Entra ID once that check succeeds.

The other three env vars
(`SIGANTRY_DEMO_TENANT_ID`, `_WORKSPACE_ID`, `_CAPACITY_ID`) are
durable across the demo tenant's lifetime; rotate only on tenant
re-provisioning.

## 3. Lakehouse Git limitation (RESEARCH §Pitfall 1)

Microsoft Fabric does NOT track Lakehouse table data + column types
in Git. Only the container shell (`Sales.Lakehouse/.platform` +
`lakehouse.metadata.json` + `shortcuts.metadata.json`) is
version-controlled. Tables, files, columns, and OneLake data live
OUTSIDE the Git source-of-truth.

Implications for the demo:

- Table data is outside what `sigantry diff` compares (item names,
  types and folders).
- Create tables from the demo notebook (`LoadOrders.Notebook`), so
  the table-creation logic is in Git.
- Document this in any external comms / FAQ. The QUICKSTART's
  Troubleshooting section already references this runbook.

Reference: [Microsoft Learn -- Lakehouse Git deployment limitations](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines).

## 4. Public-repo mirror procedure (Test 1)

The public mirror is planned but not provisioned yet. The full
procedure lives in the maintainer's phase-15 operator checklist
(Test 1), which is not part of the open-source tree. Summary:

```bash
python scripts/export-demo.py --dry-run                     # parity gate
gh repo create sigantry/demo-sigantry --public \
  --description "Sigantry demo (Phase 15 / DEMO-01)"
mkdir -p /tmp/demo-sigantry && cp -r templates/demo/. /tmp/demo-sigantry/
cd /tmp/demo-sigantry && git init -b main && git add . \
  && git commit -m "Initial demo content (Phase 15 / DEMO-01)"
git remote add origin git@github.com:sigantry/demo-sigantry.git
git push -u origin main
az devops project create --name demo-sigantry --org "$ADO_ORG" --visibility public
```

Wire the four `SIGANTRY_DEMO_*` GitHub Actions secrets and the
`sigantry-demo-secrets` ADO variable group per Section 2.

## 5. Demo CI checks

The demo does not run end to end on the shipped demo tree yet, so this
runbook has no recovery procedure for a failed demo CI run. Checks that
do not depend on the demo tree:

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
open-source tree): trademark + domain + PyPI clearance for `Sigantry` / `demo-sigantry`
was DEFERRED in v3.0. Operator runs the clearance check at Test 0 of that same checklist;
if conflicts surface, defer Test 1 mirror until a v3.1 rename
completes.

The PRODUCT-BRIEF `## Demo` section's `<DEMO-URL>` placeholder
remains until clearance closes AND Test 1 closure publishes the real
URL. Treat the placeholder as a feature, not a bug, until then.

## See also

- [docs/demo/QUICKSTART.md](../demo/QUICKSTART.md) -- adopter-facing
  walkthrough.
- The maintainer's phase-15 operator checklist -- its operator gates
  (trademark, mirror, tenant and fresh-laptop reviewer). Held
  outside this repository; ask the maintainer.
