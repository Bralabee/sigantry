<!-- pr-checklist:start -->
## Sigantry PR review checklist

- [ ] **Semantic-model changes (TMDL)** -- if this PR touches `*.tmdl`
      files, the Sigantry PR-bot has posted a TMDL diff comment and a
      reviewer has confirmed table / measure / column / relationship
      changes are intentional.
- [ ] **Schema changes (Lakehouse / `.platform`)** -- if this PR touches
      `*.Lakehouse/**` or `**/.platform`, the PR-bot's Lakehouse-metadata
      diff comment has been reviewed for shortcut, role, and identity
      changes. Note: column-level schema lives in OneLake, not Git --
      the PR-bot does not surface column-type changes.
- [ ] **Variable-library changes** -- if this PR adds, removes, or
      modifies a Variable Library reference, the corresponding
      `parameters.yml` find/replace key was updated for every target env
      (DEV / PREPROD / PROD).
- [ ] **Test evidence** -- the most recent CI run on this branch is
      green; integration / smoke tests covering the changed item type
      have been run (or skipped intentionally with a noted reason).
- [ ] **Deploy-record link** -- once this PR merges and triggers a
      deploy, the resulting `sigantry release record` audit-record
      identifier is linked back to this PR (auto-posted by the deploy
      pipeline; reviewer confirms presence).

For more detail see: `docs/runbooks/pr-bot-operator.md` (Sigantry docs).
<!-- pr-checklist:end -->
