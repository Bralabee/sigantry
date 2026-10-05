# Demo tenant -- operator runbook

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
   Section 6). Generate a secret -- this becomes
   `SIGANTRY_DEMO_FABRIC_TOKEN` (or wire workload-identity-federation
   if you prefer no long-lived secrets; the demo CI workflows accept
   either).

Verify the SPN can call Fabric REST:

```bash
curl -H "Authorization: Bearer $SIGANTRY_DEMO_FABRIC_TOKEN" \
  "https://api.fabric.microsoft.com/v1/workspaces/$SIGANTRY_DEMO_WORKSPACE_ID/items"
```

Expected: HTTP 200 with the list of items in the demo workspace
(empty on a fresh provision; populated after the first
`sigantry sync apply`).

## 2. Secret rotation cadence

Rotate `SIGANTRY_DEMO_FABRIC_TOKEN` every 90 days, or immediately on
suspicion of leakage. Procedure:

1. Generate a new SPN secret (or rotate the WIF federated credential)
   in Entra ID.
2. Update the GitHub Actions secret on the public `demo-sigantry`
   repo:
   ```bash
   gh secret set SIGANTRY_DEMO_FABRIC_TOKEN \
     --repo sigantry/demo-sigantry \
     --body "<new-token>"
   ```
3. Update the `sigantry-demo-secrets` ADO variable group:
   ```bash
   az pipelines variable-group variable update \
     --org "$ADO_ORG" --project demo-sigantry \
     --group-id <id> --name SIGANTRY_DEMO_FABRIC_TOKEN \
     --value "<new-token>" --secret true
   ```
4. Push a no-op commit to `main` to verify the demo CI still runs
   green:
   ```bash
   git commit --allow-empty -m "ops: token rotation smoke" && git push
   ```
5. Revoke the old secret in Entra ID once the demo CI run goes green.

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

- A table added in the Fabric portal does not show as drift:
  `sigantry diff` compares item names, types and folders only.
- Use the demo notebook (`LoadOrders.Notebook`) to create tables for
  round-trip-stable demos; it commits the table-creation logic to
  Git so re-deploys reproduce the same end state.
- Document this in any external comms / FAQ. The QUICKSTART's
  Troubleshooting section already references this runbook.

Reference: [Microsoft Learn -- Lakehouse Git deployment limitations](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines).

## 4. Public-repo mirror procedure (Test 1)

The full procedure lives in the maintainer's phase-15 operator checklist
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

## 5. Recovery -- when the demo CI goes red

Common failure modes:

- **Drift detected (`sigantry diff` exit 1):** investigate which file
  under `fabric_items/` or `parameters.yml` changed; re-sync via
  `sigantry sync apply` if the source-of-truth is correct, or revert
  the workspace edit if a portal click made it. The
  `--fail-on-drift` flag is intentional -- it is the safety net per
  RESEARCH §Pitfall 2 ("stale demo").
- **Token expired:** rotate per Section 2.
- **Tenant outage:** check `https://admin.fabric.microsoft.com` and
  `https://status.azure.com`.
- **`fabric-cicd` upgrade broke the deploy:** pin to last-known-good
  version in the demo CI YAML's `pip install` step, file an issue
  against `fabric-cicd`, and PR the pin into
  `templates/demo/.github/workflows/sigantry-demo-ci.yml` +
  `templates/demo/.azuredevops/sigantry-demo-ci.yml` together
  (dual-CI parity rule).

## 6. Dedicated SPN scope (RESEARCH §Pitfall 6)

The demo SPN MUST hold permissions ONLY on the demo tenant. NEVER
grant role assignments outside the demo tenant.

Why this matters: a confused-deputy attack pattern -- an operator
running `sigantry sync apply` against the wrong env-var-loaded token
deploys demo content to a production adopter tenant. The demo SPN
being narrowly-scoped is the structural defence; least-privilege
variable groups and per-step
`if: ${{ env.SIGANTRY_DEMO_HAS_TOKEN == 'true' }}` guards in the
demo CI are the procedural defences.

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
  15-min walkthrough.
- The maintainer's phase-15 operator checklist -- its operator gates
  (trademark, mirror, tenant and fresh-laptop reviewer). Held
  outside this repository; ask the maintainer.
