# PR-Bot Operator Runbook

> Operator-facing guide for enabling the Sigantry PR-review bot in a
> consumer repo, configuring auth, and triaging the most common failure
> modes. Phase 14 / STARTER-05/06/07.

The Sigantry PR-bot is a single CLI binary (`sigantry pr-bot run`)
invoked from a CI workflow on PR open / synchronize. There is no daemon,
no GitHub App webhook listener, no ADO Service Hook subscription. The
adopter wires a workflow YAML, sets one or two env vars, and the bot
posts a TMDL + Lakehouse-metadata diff comment when the PR touches the
relevant file globs.

## What the PR-bot does

On every PR that touches `**/*.tmdl`, `**/*.Lakehouse/**`, or
`**/.platform`, the bot:

1. Diffs the base-branch tree against the head-branch tree for the
   semantic model (TMDL line-based parser, indentation-aware) and for
   the Lakehouse metadata files (`.platform`, `lakehouse.metadata.json`,
   `shortcuts.metadata.json`, `data-access-roles.json`).
2. Renders a single-source Markdown body with provider-agnostic GFM so
   the byte-identical body posts cleanly on either GitHub or Azure
   DevOps. The body always carries an `audit_hash:` line (SHA-256 of
   the canonical diff JSON) so reviewers can confirm a re-run would
   produce identical output.
3. Posts the body as one comment on GitHub (issue-comment endpoint) or
   one thread on ADO (pull-request-threads endpoint). When neither diff
   has content the body still posts a fixed
   `> No semantic-model or schema changes detected in this PR.` line --
   never silent.

Exit codes (D-07):

- `0` -- comment posted (or no-changes comment posted).
- `1` -- validation / auth misconfiguration.
- `2` -- REST / auth runtime error.
- `3` -- unknown failure (catch-all).

## Enabling the bot in your repo (GitHub)

1. **Copy the workflow into your repo**:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/sigantry/sigantry-starter/main/.github/workflows/pr-bot.yml \
     > .github/workflows/pr-bot.yml
   ```
   Or, if you bootstrapped from the `sigantry-starter` template, the
   workflow is already in place.

2. **Verify the workflow's trigger matches your repo's default branch**.
   The starter ships with `branches: [main]`; rename if your default
   branch is `master`.

3. **Confirm the auth surface is correct**. The workflow uses
   `secrets.GITHUB_TOKEN` (the auto-provisioned per-job token); no PAT
   or App auth is required for first-party repos. The token has the
   `pull-requests: write` permission set in the workflow's `permissions:`
   block.

4. **Push a test PR** that touches a `*.tmdl` file. Within ~60 seconds
   the bot's comment should appear on the PR.

## Enabling the bot in your repo (Azure DevOps)

1. **Reference the job template in your `azure-pipelines.yml`**:
   ```yaml
   resources:
     repositories:
       - repository: sigantry-starter
         type: github
         name: sigantry/sigantry-starter
         ref: refs/heads/main

   pr:
     branches:
       include:
         - main
     paths:
       include:
         - "**/*.tmdl"
         - "**/*.Lakehouse/**"
         - "**/.platform"

   jobs:
     - template: .azuredevops/jobs/pr-bot.yml@sigantry-starter
   ```

2. **Allow the pipeline to use `System.AccessToken`**. Under Project
   Settings -> Pipelines -> Settings, ensure
   "Limit job authorization scope to current project for non-release
   pipelines" is enabled (default), and that the YAML pipeline has
   permission to post PR comments. The job template wires
   `System.AccessToken` into a shell-level `$SYSTEM_ACCESSTOKEN`
   variable -- no additional ADO PAT is required for first-party
   repos.

3. **Push a test PR** that touches a `*.tmdl` file. The bot will post a
   PR thread when the pipeline run finishes.

## Required env vars

The workflow YAMLs auto-provision the auth surface from CI-system
predefined variables; you do NOT export anything yourself in the
default flow.

| Provider | Auth env var | Provisioned by |
|---|---|---|
| GitHub | `GITHUB_TOKEN` | `secrets.GITHUB_TOKEN` (per-job token) |
| ADO    | `SYSTEM_ACCESSTOKEN` | `$(System.AccessToken)` (predefined) |

For local debugging or for running the bot from your laptop:

```bash
# GitHub: PAT with 'pull-requests: write'.
export GITHUB_TOKEN=<your-github-pat>  # token format: ghp_<36-alphanumerics>
export GITHUB_ACTIONS=true        # so --provider auto resolves correctly
export GITHUB_REPOSITORY=owner/repo
sigantry pr-bot run --provider auto --pr-id 123 --token "$GITHUB_TOKEN" \
  --base-dir base/ --head-dir head/

# ADO: DefaultAzureCredential (az login or WIF).
export SYSTEM_TEAMFOUNDATIONCOLLECTIONURI=https://dev.azure.com/<org>/
export SYSTEM_TEAMPROJECT=<project>
export BUILD_REPOSITORY_ID=<repository-guid>
export TF_BUILD=True              # so --provider auto resolves correctly
sigantry pr-bot run --provider auto --pr-id 123 --token unused-for-ado \
  --base-dir base/ --head-dir head/
```

## Troubleshooting

### `--provider auto could not detect CI`

Both `GITHUB_ACTIONS` and `TF_BUILD` are unset. Pass `--provider github`
or `--provider ado` explicitly when running locally.

### `403 Forbidden` posting a PR comment (GitHub)

The token's `pull-requests: write` permission is missing.

- For workflow runs: confirm `permissions: pull-requests: write` is set
  at the job or workflow level.
- For PAT-based local runs: the PAT must include the `repo` scope (for
  classic PATs) or `pull-requests: write` (for fine-grained PATs).

### `403 Forbidden` posting a PR thread (Azure DevOps)

The pipeline's `System.AccessToken` lacks PR-comment write permission.

- Project Settings -> Repos -> Security: grant the build identity
  "Contribute to pull requests".

### `429 Too Many Requests` / `Retry-After`

The bot's `BaseRestClient` retries automatically (exponential backoff +
respects `Retry-After`). On exhaustion the bot exits 2 and the workflow
fails -- re-run the workflow once the rate limit window clears.

For GitHub specifically: secondary rate limits on issue-comment POST
fire if multiple bot runs race for the same PR. The starter workflow
ships with a `concurrency:` block keyed on PR id (`group:
sigantry-pr-bot-${{ github.event.pull_request.number }}`,
`cancel-in-progress: true`) so only one bot run executes per PR at a
time. Keep this block in place when customising the workflow.

### Bot did not post a comment but the workflow shows green

The bot may have exited 0 with a "no semantic-model or schema changes
detected" comment (rendered when both diff sections are empty). Search
the PR comments for the `audit_hash:` substring -- the bot ALWAYS
posts when a workflow runs.

### `unexpected error during diff: ...`

The bot raised an unhandled exception during diff computation. Check
the workflow log for the full traceback. Most often this is:

- A malformed `*.tmdl` file (the parser is line-based, indentation-
  aware; tabs vs. spaces mixed within a single block trips it).
- A symlink loop inside `**/*.Lakehouse/**`.

Open an issue at <https://github.com/sigantry/sigantry-core/issues>
with the offending file path + the workflow log.

## Known limitations

### Lakehouse column types are not tracked in Git metadata

Microsoft Fabric does not track Lakehouse column types in the per-item
Git metadata (`.platform`, `lakehouse.metadata.json`,
`shortcuts.metadata.json`, `data-access-roles.json`). The bot's
Lakehouse diff section therefore surfaces:

- Identity changes (`displayName`, `description`, `defaultSchema`)
- Schema toggle (between schema-enabled and schema-disabled state)
- Tracked tables added / removed
- Shortcuts added / removed / modified
- Role changes

But it does NOT surface column-type changes. The renderer emits a
fixed warning line in every Lakehouse section reminding reviewers of
this limitation. Do not interpret a silent diff as "no schema change"
without separately inspecting OneLake.

### Provider auto-detect is env-only

`--provider auto` reads only `GITHUB_ACTIONS` and `TF_BUILD`; no HTTP
or filesystem probes. If you want to run the bot under a non-standard
CI runner that does not set those env vars, pass `--provider` explicitly.

### TMDL parser semantics are shallow

The TMDL parser is intentionally simple: line-based, indentation-aware,
recognising `table`, `measure`, `column`, `relationship`, `partition`,
`model` blocks. Heavy semantics (DAX expression equivalence, partition
source identity) are out of scope -- the bot surfaces "modified" when
text changes; downstream reviewer adjudicates significance.

### No live REST queries during PR review

The diff is computed against repo file content only, never against
live workspace state. This is a deliberate constraint (Council D's "no
live state during PR review" framing) -- it keeps the bot fast,
deterministic, and auth-light.

## See also

- `docs/migration/3.x-pr-bot.md` -- migration notes (no breaking change
  in v3.0; the bot is a new feature).
- `docs/reference/parameters-yml.md` -- the starter's `parameters.yml`
  reference doc (DEPLOY-03 + STARTER-02).
- `templates/starter/docs/QUICKSTART.md` -- 15-minute adopter
  walkthrough.
- `.planning/phases/14-starter-repo-pr-review-bot/14-HUMAN-UAT.md` --
  operator UAT plan + sign-off log.
