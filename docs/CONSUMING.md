# Consuming Sigantry

> **Operator-facing entry point.** This file is for people who want to **use** Sigantry, not develop it. If you're contributing to the toolkit itself, read [CONTRIBUTING.md](../CONTRIBUTING.md) instead.

## How Sigantry is distributed

1. **Python distribution** -- `pip install sigantry` from PyPI. The import package is `sigantry_core`.
2. **CI templates and scaffolding** -- the Azure DevOps templates under [`templates/`](../templates/), the reusable GitHub Actions workflows under `.github/workflows/`, and the starter and demo scaffolds under `templates/{starter,demo}/`, all in this repository.

You **do not** need to clone this repository (`Bralabee/sigantry`) to run Sigantry. It holds the product source, its tests and CI gates, and the templates your pipelines reference.

## Status today

| Surface | State |
|---|---|
| `sigantry` on PyPI | published; 1.0.0 reads the pre-rename config names (see the note in [README.md](../README.md)) |
| Public `sigantry/sigantry-starter` and `sigantry/demo-sigantry` repositories | not provisioned; use [`templates/starter/`](../templates/starter/) and [`templates/demo/`](../templates/demo/) |

## Install

```bash
pip install sigantry
```

The distribution is `sigantry`, not `sigantry-core` ([ADR-0017](decisions/ADR-0017-distribution-name-sigantry.md)). Code and configuration written for the pre-rename names (`fabric_dataops_toolkits`, `.fabric-dataops.toml`, `FDT_`) are covered in [docs/migration/2.x-to-3.0.md](migration/2.x-to-3.0.md).

The base package registers its own notification sinks, secret stores and approval gates. A deploy profile, DQ gate, telemetry sink or other organisation-specific behaviour comes from a plugin wheel that the organisation builds and installs itself; see [Seams](reference/seams.md) and Part V of the [Operator Manual](USER-GUIDE.md).

## Starter and demo scaffolding

| Scaffold in this repository | Use it when |
|---|---|
| [`templates/starter/`](../templates/starter/) | greenfield adoption -- a dev/preprod/prod `parameters.yml`, the PR-review bot as a GitHub Actions / Azure DevOps pair, PR templates, branching strategy |
| [`templates/demo/`](../templates/demo/) | 15-minute end-to-end walkthrough against a demo Fabric tenant |

Copy the scaffold into your own repository. Public mirror repositories for both are planned but not provisioned. `scripts/export-starter.py` and `scripts/export-demo.py` are parity checks for those trees, not publishing tools: their live `--target-*` flags raise `NotImplementedError` deliberately.

## Quick start (15 minutes against a demo Fabric tenant)

From a copy of [`templates/demo/`](../templates/demo/):

```bash
pip install sigantry
# set 4 env vars: SIGANTRY_DEMO_{TENANT_ID,WORKSPACE_ID,CAPACITY_ID,FABRIC_TOKEN}
sigantry config validate parameters.yml
sigantry sync apply --manifest sync.yml --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID"
sigantry diff --workspace-id "$SIGANTRY_DEMO_WORKSPACE_ID" --manifest sync.yml
```

Full walkthrough with troubleshooting: [docs/demo/QUICKSTART.md](demo/QUICKSTART.md).

## Why this matters -- the consumer/product seam

This repository and the consumer experience are deliberately separate:

- **Product source** -- `sigantry_core/`. Operators don't read it. They get the wheel.
- **Internal CI building blocks** -- `templates/{stages,jobs,steps,extends,schedules,pr-review,environments}/`. Used by the product's own dual-CI parity gates AND composed by consumer pipelines via `template:` (ADO) or `uses:` (GHA) reference. Operators reference individual entries, never instantiate the directory wholesale.
- **Consumer scaffolds** -- `templates/{starter,demo}/`. Operators copy these into their own repositories.
- **Operator runbooks** -- [`docs/runbooks/`](runbooks/). The "how to operate Sigantry against my Fabric tenant" reference. Versioned alongside the product.
- **Tutorials** -- [`docs/tutorials/`](tutorials/index.md). Hand-holding worked examples (setup, sync, drift, audit trail, brownfield adoption, bootstrap, rollback, scheduled alerts, PR bot, governance, environments, config-driven auto-update).

If you find yourself reading `sigantry_core/*.py` to figure out how to use Sigantry, start with [`docs/tutorials/`](tutorials/index.md), then [`docs/demo/QUICKSTART.md`](demo/QUICKSTART.md) and [`docs/runbooks/`](runbooks/) -- the operator surface is documented there, not in code.

See [`templates/README.md`](../templates/README.md) for the per-subdir partition between consumer-deployable and internal-CI templates.

## Sigantry vs the Terraform provider for Microsoft Fabric

Evaluators routinely ask when to use Microsoft's [Terraform provider](https://registry.terraform.io/providers/microsoft/fabric/latest) (GA, monthly releases) versus `sigantry workspace bootstrap` + the sync engine. They solve different operating models and can co-exist:

| Use Terraform when... | Use Sigantry when... |
|---|---|
| You run fleet-scale, state-managed IaC and already operate Terraform (state backends, plan/apply pipelines, modules). | You want an operator-driven, single-verb, stateless flow -- `workspace bootstrap workspace.yml` probes live state and converges, no state file to manage or drift against. |
| Provisioning-level resources are the concern: workspaces, RBAC, domains, gateways, tenant settings as code. | Item-level lifecycle is the concern: manifest-driven sync, first-time publish, folder preservation, deploy rollback to a prior release. |
| `terraform plan` drift coverage of provisioned resources is sufficient. | You need drift detection against a manifest that you can run on a schedule, plus hash-chained audit records of each release you record (`sigantry release record`; `sync apply` writes one too, a forward `deploy run` none), each bootstrap, and each secret change and approval made through Sigantry's secret stores and approval gates. |
| Your change-control process is PR-reviewed HCL. | Your change-control process needs work-item traceability (release records written back to ADO / GitHub items) and destructive-op gating (`force=True` on every gated operation, plus a runbook id for capacity pause and resume). |

Notable gap on the Terraform side (as of 2026-06-11): no Variable Library resource ([provider issue #515](https://github.com/microsoft/terraform-provider-fabric/issues/515)) -- `sigantry variable-library` is one of the few non-portal paths. Full ecosystem comparison: [docs/LANDSCAPE-2026-06.md](LANDSCAPE-2026-06.md).

## Bug reports + features

File issues at <https://github.com/Bralabee/sigantry/issues> and pull requests at <https://github.com/Bralabee/sigantry/pulls>. Triage discipline lives in [CONTRIBUTING.md](../CONTRIBUTING.md).

## See also

- [README.md](../README.md) -- one-paragraph project pitch
- [CONTRIBUTING.md](../CONTRIBUTING.md) -- how to contribute to the toolkit (different audience)
- [docs/PRODUCT-BRIEF.md](PRODUCT-BRIEF.md) -- product positioning, ICP, why-we-win
- [docs/migration/2.x-to-3.0.md](migration/2.x-to-3.0.md) -- legacy (pre-rename) names and what replaces them
