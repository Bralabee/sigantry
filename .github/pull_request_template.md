# Pull Request

## Summary

<!-- One or two sentences: what does this PR change and why. No emojis. -->

## Requirement / Work Item

<!-- REQ-ID, phase-plan id, or ADO work item number. Example: INTEG-01, 07-03, AB#1234. -->

## Type of change

- [ ] feat (new feature)
- [ ] fix (bug fix)
- [ ] refactor (no behavior change)
- [ ] test (tests only)
- [ ] docs (documentation only)
- [ ] chore (tooling / config)
- [ ] perf (performance)

## Checklist

- [ ] Branch is `feature/<short-name>` off `main` (not direct to `main`).
- [ ] Commits follow conventional-commit format (`<type>(<scope>): <summary>`).
- [ ] No emojis in code, commits, or docs.
- [ ] No `Co-Authored-By:` trailers.
- [ ] `python -m pytest` passes locally with zero failures. (Not `pytest -q`: `addopts` already carries `-q`, and `-qq` prints no counts at all, so a run that collected nothing looks identical to a passing one.) There is no longer a carve-out for `test_wiki_links.py` - it runs in CI now. Compare your count against the `Test (...)` legs on this PR rather than repeating a number here.
- [ ] `ruff check sigantry_core/ tests/ scripts/` clean. This repo's CI (GitHub
      Actions) enforces all three roots, and the ADO job template
      `templates/jobs/lint-python.yml` defaults `sourcePaths` to the same three.
- [ ] `mypy sigantry_core/ scripts/` clean. Enforced by the GitHub Actions
      `Type Check (mypy)` job.
- [ ] `pwsh -c "Invoke-Pester -Configuration (& ./tests/Pester.config.ps1)"` passes (if PowerShell surface touched).
- [ ] CHANGELOG.md updated under `## [Unreleased]` with a user-facing entry (if user-facing change).
- [ ] Docs updated (runbook, api/, quickstart, or install) for any user-visible change.
- [ ] New public functions carry Google-style docstrings (Args: / Returns: / Raises:).
- [ ] New CLI commands have `--help` that resolves via `CliRunner` in a test.
- [ ] `httpx` is imported only where Ruff `TID251` allows it (the `banned-api` and `per-file-ignores` tables in `pyproject.toml`).
- [ ] Destructive operations require `force=True` and log an audit entry.
- [ ] No tag pushed (tags are maintainer-only; see the release process).

## Testing

<!-- How did you verify this change? Paste key pytest / ruff / manual output. -->

## Screenshots / Output

<!-- Optional: CLI output, Rich table, wiki render. -->

## Related links

<!-- Related PRs, ADO work items, runbooks, ADRs, research notes. -->
