# Branching strategy

Sigantry-starter uses **trunk-based development** with short-lived
feature branches and three deploy environments: `DEV`, `PREPROD`,
`PROD`. This doc is the source of truth for any contributor or deploy
operator.

See also:

- [`docs/decisions/ADR-0010-commercial-model.md`](../../docs/decisions/ADR-0010-commercial-model.md)
  -- the Apache-2.0 + pure-OSS stance the deploy strategy assumes.
- [`docs/decisions/ADR-0011-rename-to-sigantry.md`](../../docs/decisions/ADR-0011-rename-to-sigantry.md)
  -- the `fabric-dataops-toolkits -> sigantry` rename. The legacy
  name's shim window closed in v3.1, so pin `sigantry` in your
  `pyproject.toml`.

## Trunk-based development

All work merges into `main` via a pull request. Feature branches are
short-lived (≤2 days; aim for hours): create one per work item, push
early, request review, merge.

Branch-name convention: `feature/<wi-id>-<short-slug>` (e.g.
`feature/1234-add-bronze-layer`). The work-item id makes the deploy
record's `sigantry release record --work-items <ids>` output naturally
populated when the PR merges.

Long-lived branches are explicitly out of scope -- if a piece of work
cannot land in `main` within a sprint, split it into smaller PRs behind
a feature flag.

## Environment-to-branch mapping

| Environment | Trigger                              | Approval     |
|-------------|--------------------------------------|--------------|
| `DEV`       | every merge into `main`              | none (auto)  |
| `PREPROD`   | git tag matching `v*` pushed         | manual       |
| `PROD`      | git tag matching `v*` re-promoted    | manual       |

`DEV` is the loosest -- every merged PR is deployed automatically by
the Sigantry deploy template. The Fabric workspace IDs for each env
live in `parameters.yml` under the `replace_value` blocks
(`DEV` / `PREPROD` / `PROD`).

`PREPROD` is gated by an environment approver (configured in either
GitHub Environments or ADO Environments depending on your CI). The
`sigantry deploy --rollback --to-release <id>` command is your
fallback if smoke tests fail post-promotion.

`PROD` is the tightest gate -- a deploy here promotes the same
artefact that PREPROD validated, never a fresh build. The deploy
template will refuse to publish to `PROD` if the corresponding
PREPROD release is not in the deploy ledger.

## Promotion gates

1. PR merges into `main` -> auto-deploy to `DEV`.
   `sigantry release record --release-id auto-<sha>` runs as part of
   the deploy job.
2. Tag `v<x.y.z>` pushed -> queued for `PREPROD`. Approver reviews
   the deploy ledger entry + PR-bot comments before clicking approve.
3. Same tag re-queued for `PROD` -> approver re-reviews PREPROD smoke
   evidence (linked from the deploy ledger). On approve, deploy
   publishes to `PROD` with the same release id as PREPROD.

Each gate is auditable via `sigantry release list` and
`sigantry release show <release-id>`.

## Pull-request checklist

Every PR uses the in-repo template
(`.github/pull_request_template.md` on GitHub;
`.azuredevops/pull_request_template.md` on ADO). The Sigantry PR-bot
will append diff comments when relevant Fabric files change -- see
`docs/runbooks/pr-bot-operator.md`.

## Draft PR caveat

GitHub Actions' `pull_request` event fires on draft PRs by default,
but some org settings disable that. The Sigantry PR-bot workflow uses
`types: [opened, synchronize, reopened, ready_for_review]` so the bot
will run when a draft is converted to ready-for-review. If your org
blocks draft-PR workflow runs entirely, mark drafts as
ready-for-review before expecting bot comments.

GitHub Actions also honours the workflow's `paths:` filter -- a PR
that only touches files outside `**/*.tmdl`, `**/*.Lakehouse/**`, and
`**/.platform` will be skipped silently. This is intentional (no spam
on docs-only PRs) but can confuse first-time contributors expecting a
bot comment on every PR.

## Hot-fix path

Production hot-fixes start from a tagged release: branch off the tag
(`git checkout -b feature/HF-1234-fix-bronze-loop v1.2.3`), make the
minimal change, open a PR into `main`, and tag the merged commit
`v1.2.4` to start a new PREPROD -> PROD round.
