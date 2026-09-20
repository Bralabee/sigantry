# Consuming Sigantry

> **Operator-facing entry point.** This file is for people who want to **use** Sigantry, not develop it. If you're contributing to the toolkit itself, read [CONTRIBUTING.md](../CONTRIBUTING.md) instead.

## Two distribution channels

Sigantry ships through two distinct surfaces. You almost certainly want both:

1. **Python distribution** -- `pip install sigantry-core` (+ optional plugins).
2. **Public scaffolding repos** -- fork `sigantry/sigantry-starter` for a greenfield Sigantry project, or `sigantry/demo-sigantry` for the 15-minute end-to-end walkthrough.

You **do not** clone this monorepo (`Bralabee/fabric_dataops`) to run Sigantry. This repo is the development surface; it holds the product source, dual-CI parity gates, banned-API contract tests, and the source-of-truth for the public scaffolding.

## Status today (2026-06-14, v3.4.0)

| Surface | State | Gating |
|---|---|---|
| `sigantry-core` on PyPI | **not yet published** | gated on v3.0 operator UAT closure (V3-RISK-1/2/3) |
| `sigantry-hs2` on PyPI | not yet published | same gate |
| `sigantry-jtoye` on PyPI | not yet published | same gate |
| `Sigantry` on PowerShell Gallery | not yet published | same gate |
| Public `sigantry/sigantry-starter` GitHub repo | **not yet provisioned** | Phase 14 Test 1 in `14-HUMAN-UAT.md` (operator-bound: needs `gh repo create` + ADO project create) |
| Public `sigantry/demo-sigantry` GitHub repo | not yet provisioned | Phase 15 Test 1 in `15-HUMAN-UAT.md` (operator-bound: same) |

**Until UAT closes**, the consumer path is to clone this repo and `pip install -e .` from a checkout. The CI-side closure of every Phase 14/15 mirror invariant is already green (`scripts/export-starter.py --dry-run` and `scripts/export-demo.py --dry-run` both report `exit 0` plus per-invariant OK lines), so the mirror push itself is a single operator command away.

## Channel 1 -- Python distribution (forthcoming)

Once `sigantry-core` is on PyPI:

```bash
pip install "sigantry-core>=3.0"
```

The legacy `fabric-dataops-toolkits` name no longer resolves: the v3.0 shim
window closed with the v3.1.0 SHIM-DROP and the `shim/` tree was deleted.
Install `sigantry-core` directly.

See [docs/migration/2.x-to-3.0.md](migration/2.x-to-3.0.md) for the full upgrade recipe.

Optional plugins:

```bash
pip install sigantry-hs2       # HS2 reference impls of all 11 seams
pip install sigantry-jtoye     # JToye second-customer reference plugin
```

PowerShell module install: import from a clone (`Import-Module ./Sigantry/Sigantry.psd1`) -- module sources at `Sigantry/`, rename guidance in [docs/migration/2.x-to-3.0.md](migration/2.x-to-3.0.md). (The legacy `Fabric` module-name shim was removed in v3.1.0 SHIM-DROP.)

## Channel 2 -- Public scaffolding repos (forthcoming)

Two operator-facing template repos are mirrored from this monorepo's `templates/{starter,demo}/` source-of-truth. You fork or template-clone the public mirror:

| Public mirror (planned) | Source-of-truth in this repo | Use it when |
|---|---|---|
| `sigantry/sigantry-starter` | [`templates/starter/`](../templates/starter/) | greenfield adoption -- `parameters.yml`, dev/preprod/prod pipeline pair, PR templates, branching strategy |
| `sigantry/demo-sigantry` | [`templates/demo/`](../templates/demo/) | 15-minute end-to-end walkthrough against a demo Fabric tenant |

Mirror propagation is operator-driven and out-of-band per CONTEXT D-01..D-03. The export scripts ship as **parity gates only**, not propagation tools -- live `--target-*` flags raise `NotImplementedError` deliberately. The documented mirror procedure lives in `14-HUMAN-UAT.md` Test 1 (starter) and `15-HUMAN-UAT.md` Test 1 (demo).

## Quick start (15 minutes against a demo Fabric tenant)

Once `sigantry/demo-sigantry` is provisioned:

```bash
gh repo clone sigantry/demo-sigantry
cd demo-sigantry
pip install "sigantry-core>=3.0"
# set 4 env vars: SIGANTRY_DEMO_{TENANT_ID,WORKSPACE_ID,CAPACITY_ID,FABRIC_TOKEN}
sigantry config validate parameters.yml
sigantry sync apply --manifest sync.yml --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID"
sigantry diff --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID" --manifest sync.yml
```

Full walkthrough with screenshots and troubleshooting: [docs/demo/QUICKSTART.md](demo/QUICKSTART.md).

The same loop works today against the in-repo source-of-truth at [`templates/demo/`](../templates/demo/) if you want to dry-run before the public mirror lands.

## Why this matters -- the consumer/product seam

The development monorepo (this repo) and the consumer experience are deliberately separate:

- **Product source** -- `sigantry_core/`, `sigantry-hs2/`, `sigantry-jtoye/`, `Sigantry/`, `SigantryHs2/`. Operators don't read these. They get the compiled wheel.
- **Internal CI building blocks** -- `templates/{stages,jobs,steps,extends,schedules,pr-review,environments}/`. Used by the product's own dual-CI parity gates AND composed by consumer pipelines via `template:` (ADO) or `uses:` (GHA) reference. Operators reference individual entries, never instantiate the directory wholesale.
- **Consumer source-of-truth** -- `templates/{starter,demo}/`. Mirrored verbatim to the public scaffolding repos. Operators interact with the **public mirror**, not this directory.
- **Operator runbooks** -- [`docs/runbooks/`](runbooks/). The "how to operate Sigantry against my Fabric tenant" reference. Versioned alongside the product.
- **Tutorials** -- [`docs/tutorials/`](tutorials/index.md). Ten verified, hand-holding worked examples (setup, sync, drift, audit trail, brownfield adoption, bootstrap, rollback, scheduled alerts, PR bot, governance). Every step executed against a live tenant before publication.

If you find yourself reading `sigantry_core/*.py` to figure out how to use Sigantry, start with [`docs/tutorials/`](tutorials/index.md), then [`docs/demo/QUICKSTART.md`](demo/QUICKSTART.md) and [`docs/runbooks/`](runbooks/) -- the operator surface is documented there, not in code.

See [`templates/README.md`](../templates/README.md) for the per-subdir partition between consumer-deployable and internal-CI templates.

## Sigantry vs the Terraform provider for Microsoft Fabric

Evaluators routinely ask when to use Microsoft's [Terraform provider](https://registry.terraform.io/providers/microsoft/fabric/latest) (GA, monthly releases) versus `sigantry workspace bootstrap` + the sync engine. They solve different operating models and can co-exist:

| Use Terraform when... | Use Sigantry when... |
|---|---|
| You run fleet-scale, state-managed IaC and already operate Terraform (state backends, plan/apply pipelines, modules). | You want an operator-driven, single-verb, stateless flow -- `workspace bootstrap workspace.yml` probes live state and converges, no state file to manage or drift against. |
| Provisioning-level resources are the concern: workspaces, RBAC, domains, gateways, tenant settings as code. | Item-level lifecycle is the concern: manifest-driven sync, first-time publish, folder preservation, deploy rollback to a prior release. |
| `terraform plan` drift coverage of provisioned resources is sufficient. | You need scheduled drift detection against a manifest plus an integrity-checked audit ledger of every deploy, bootstrap, secret change, and approval. |
| Your change-control process is PR-reviewed HCL. | Your change-control process needs work-item traceability (release records written back to ADO / GitHub items) and destructive-op gating with `force=True` + runbook ids. |

Notable gap on the Terraform side (as of 2026-06-11): no Variable Library resource ([provider issue #515](https://github.com/microsoft/terraform-provider-fabric/issues/515)) -- `sigantry variable-library` is one of the few non-portal paths. Full ecosystem comparison: [docs/LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md).

## Bug reports + features

File issues + PRs against `Bralabee/fabric_dataops` (this repo). Operators don't open product issues against their consumer fork -- they file upstream. Triage discipline lives in [CONTRIBUTING.md](../CONTRIBUTING.md).

## See also

- [README.md](../README.md) -- one-paragraph project pitch
- [CONTRIBUTING.md](../CONTRIBUTING.md) -- how to contribute to the toolkit (different audience)
- [docs/PRODUCT-BRIEF.md](PRODUCT-BRIEF.md) -- product positioning, ICP, why-we-win
- [docs/migration/2.x-to-3.0.md](migration/2.x-to-3.0.md) -- v2 → v3 rename migration recipe
