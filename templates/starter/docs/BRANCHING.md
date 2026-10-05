# Branching strategy

Sigantry-starter uses **trunk-based development** with short-lived
feature branches and three deploy environments: `DEV`, `PREPROD`,
`PROD`. This doc is the source of truth for any contributor or deploy
operator.

See also:

- [`docs/decisions/ADR-0010-commercial-model.md`](../../docs/decisions/ADR-0010-commercial-model.md)
  -- the Apache-2.0 + pure-OSS stance the deploy strategy assumes.
- [`docs/decisions/ADR-0011-rename-to-sigantry.md`](../../docs/decisions/ADR-0011-rename-to-sigantry.md)
  -- the rename to Sigantry.
- ADR-0017 (`docs/decisions/ADR-0017-distribution-name-sigantry.md` in
  the Sigantry repository) -- the PyPI distribution is `sigantry`, so pin
  `sigantry` in your `pyproject.toml`.

## Trunk-based development

All work merges into `main` via a pull request. Feature branches are
short-lived (≤2 days; aim for hours): create one per work item, push
early, request review, merge.

Branch-name convention: `feature/<wi-id>-<short-slug>` (e.g.
`feature/1234-add-bronze-layer`). Keep the work-item id in the name so
it is at hand for `sigantry release record --work-items <ids>` when you
record the release.

Long-lived branches are explicitly out of scope -- if a piece of work
cannot land in `main` within a sprint, split it into smaller PRs behind
a feature flag.

## Environment-to-branch mapping

Set up your CI to follow this mapping:

| Environment | Trigger                              | Approval     |
|-------------|--------------------------------------|--------------|
| `DEV`       | every merge into `main`              | none (auto)  |
| `PREPROD`   | git tag matching `v*` pushed         | manual       |
| `PROD`      | git tag matching `v*` re-promoted    | manual       |

`DEV` is the loosest: deploy every merged PR to it. The Fabric
workspace IDs for each env live in `parameters.yml` under the
`replace_value` blocks (`DEV` / `PREPROD` / `PROD`).

Put an environment approver on `PREPROD` (GitHub Environments or ADO
Environments, depending on your CI). To go back to an earlier recorded
release, check out that release's source and run:

```bash
sigantry deploy run --rollback --to-release <release-id> --rollback-force \
  --source <that checkout> --environment PREPROD \
  --workspace-id <preprod-workspace-id>
```

`PROD` is the tightest gate: promote the same tagged commit that
PREPROD validated, never a fresh build.

## Promotion gates

1. PR merges into `main` -> deploy to `DEV`.
2. Tag `v<x.y.z>` pushed -> queued for `PREPROD`. The approver reviews
   the PR-bot comments and the test results before approving.
3. Same tag re-queued for `PROD` -> the approver re-reviews the
   PREPROD smoke evidence before approving.

In the Sigantry pipeline templates the smoke tests, integration tests
and approval run after the deploy, so they gate the release record,
not the deployment. Releases recorded with `sigantry release record`
are listed by `sigantry release list` and shown by
`sigantry release show <release-id>`; a forward `sigantry deploy run`
writes no record.

## Pull-request checklist

Every PR uses the in-repo template
(`.github/pull_request_template.md` on GitHub;
`.azuredevops/pull_request_template.md` on ADO). The Sigantry PR-bot
workflow is there to post diff comments when relevant Fabric files
change -- see `docs/runbooks/pr-bot-operator.md`.

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
