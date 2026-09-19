# Sigantry starter (placeholder)

Wave 0 stamp -- the actual starter content lands in Plan 14-01:

- `parameters.yml` (3-env fabric-cicd substitution file)
- `_partials/pr-checklist.md`
- `.github/pull_request_template.md` (rendered from `_partials/pr-checklist.md`)
- `.azuredevops/pull_request_template.md` (rendered from `_partials/pr-checklist.md`)
- `docs/BRANCHING.md` (trunk-based; references ADR-0010 + ADR-0011)
- `docs/QUICKSTART.md` (15-minute adopter walkthrough)

Plan 14-06 lands the workflow pair:

- `.github/workflows/pr-bot.yml`
- `.azuredevops/jobs/pr-bot.yml`

See `.planning/phases/14-starter-repo-pr-review-bot/14-RESEARCH.md` for
the full source-of-truth design.
