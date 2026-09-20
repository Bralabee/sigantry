# Demo tenant -- operator runbook

> Phase 15 / DEMO-02 + DEMO-03 closure. This runbook is for the
> operator who provisions the demo Fabric tenant, rotates demo
> secrets, captures screenshots for the walkthrough mp4, and
> maintains the public `demo-sigantry` repo. Outside reviewers do
> NOT need this runbook -- send them to
> [docs/demo/QUICKSTART.md](../demo/QUICKSTART.md).

## Audience

- Sigantry maintainer with admin rights on the dedicated demo Fabric
  tenant
- Sigantry maintainer with GitHub org admin + ADO Server admin on the
  `sigantry` org

## 1. Tenant provisioning (one-time)

Provision a dedicated Fabric tenant for the demo. Do NOT reuse a
production adopter tenant -- see Section 7 (dedicated SPN scope)
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
   Section 7). Generate a secret -- this becomes
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

- Adding a table in the Fabric portal surfaces as `drift` in
  `sigantry diff` -- this is correct Microsoft Fabric behaviour, NOT
  a Sigantry bug.
- Use the demo notebook (`LoadOrders.Notebook`) to create tables for
  round-trip-stable demos; it commits the table-creation logic to
  Git so re-deploys reproduce the same end state.
- Document this in any external comms / FAQ -- outside reviewers
  will encounter it on Step 5 of the QUICKSTART. The QUICKSTART's
  Troubleshooting section already references this runbook.

Reference: [Microsoft Learn -- Lakehouse Git deployment limitations](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-git-deployment-pipelines).

## 4. mp4 re-recording cadence (RESEARCH §Pitfall 5)

The walkthrough mp4 captures specific Sigantry CLI surface (e.g.
`sigantry release record --release-id ...`). When the CLI surface
changes, re-render the mp4. Cadence:

- **Quarterly review:** check `sigantry --help` against
  `scripts/remotion/script.md`. If any command/flag has changed,
  re-render.
- **On any breaking-change CLI bump:** re-render BEFORE the
  breaking-change release ships, so the demo never lags the docs.

Re-render procedure:

1. Capture 5 fresh PNG screenshots from the demo tenant UI:
   `scene1-workitem.png` through `scene5-rollback.png`. Recommended
   size: 1920x1080 (matches the Remotion composition).
2. Drop them into `scripts/remotion/assets/demo/` (overwriting any
   prior demo-flavoured PNGs). The `assetSelector.ts` auto-prefers
   `assets/demo/` over `assets/placeholder/`.
3. Run `scripts/build-walkthrough.sh` (the determinism wrapper around
   `npm ci && npm run render`).
4. Inspect `scripts/remotion/out/walkthrough.mp4` locally to confirm
   the 5 scenes render with the new screenshots.
5. Push the asset commit to a feature branch. The
   `.github/workflows/sigantry-demo-mp4.yml` GHA workflow re-renders
   the mp4 in CI and uploads the artefact.
6. Open a GitHub Release on the public `demo-sigantry` repo and
   attach the mp4:
   ```bash
   gh release create v0.X.0 --repo sigantry/demo-sigantry \
     --title "Demo walkthrough vX.0" \
     --notes "Refreshed walkthrough; sigantry $(python -c 'import sigantry_core; print(sigantry_core.__version__)')" \
     scripts/remotion/out/walkthrough.mp4
   ```
7. Update the PRODUCT-BRIEF `## Demo` section's `<DEMO-URL>`
   placeholder if the URL has shifted (e.g. on first publish or
   on a domain switch).

## 5. Public-repo mirror procedure (Test 1)

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

## 6. Recovery -- when the demo CI goes red

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

## 7. Dedicated SPN scope (RESEARCH §Pitfall 6)

The demo SPN MUST hold permissions ONLY on the demo tenant. NEVER
grant role assignments outside the demo tenant.

Why this matters: a confused-deputy attack pattern -- an operator
running `sigantry sync apply` against the wrong env-var-loaded token
deploys demo content to a production adopter tenant. The demo SPN
being narrowly-scoped is the structural defence; least-privilege
variable groups and per-step
`if: ${{ secrets.SIGANTRY_DEMO_FABRIC_TOKEN != '' }}` guards in the
demo CI are the procedural defences.

Verification:

```bash
az ad sp show --id "$DEMO_SPN_OBJECT_ID" \
  --query "appRoleAssignments[?resourceDisplayName!='sigantry-demo-spn']" \
  -o table
# Confirm zero entries outside the demo tenant.
```

Run this verification at SPN creation, after every secret rotation,
and quarterly during the mp4-cadence review (Section 4).

## 8. Trademark / clearance -- V3-RISK-1

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
- [docs/demo/walkthrough-script.md](../demo/walkthrough-script.md) --
  mp4 narrative source.
- The maintainer's phase-15 operator checklist -- the 5 operator gates
  (trademark + mirror + tenant + mp4 + fresh-laptop reviewer). Held
  outside this repository; ask the maintainer.
