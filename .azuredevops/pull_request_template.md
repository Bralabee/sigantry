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

- [ ] Branch is `feature/<short-name>` off `master` (not direct to `master`).
- [ ] Commits follow conventional-commit format (`<type>(<scope>): <summary>`).
- [ ] No emojis in code, commits, or docs.
- [ ] No `Co-Authored-By:` trailers.
- [ ] `pytest -q` passes locally with zero failures. There is no longer a carve-out for `test_wiki_links.py` - it runs in CI now. See CLAUDE.md for the current measured baseline rather than repeating a count here.
- [ ] `ruff check .` clean.
- [ ] `mypy sigantry_core/` clean.
- [ ] `pwsh -c "Invoke-Pester -Configuration (& ./tests/Pester.config.ps1)"` passes (if PowerShell surface touched).
- [ ] `make docs-doctest` passes (if any docstring examples were added or edited).
- [ ] CHANGELOG.md updated under `## [Unreleased]` with a user-facing entry (if user-facing change).
- [ ] Docs updated (runbook, api/, quickstart, or install) for any user-visible change.
- [ ] New public functions carry Google-style docstrings (Args: / Returns: / Raises:).
- [ ] New CLI commands have `--help` that resolves via `CliRunner` in a test.
- [ ] Any new alert in `bicep/modules/alerts.bicep` points to a `docs/runbooks/*.md` anchor that exists (MONITOR-04).
- [ ] `sigantry_core` does not import `aims_data_platform` or `dq_framework` at module scope (dependency-direction invariant).
- [ ] `httpx` is imported only inside `sigantry_core/client/` or `sigantry_core/auth/diagnose.py` (Ruff `TID251`).
- [ ] Destructive operations require `force=True` and log an audit entry.
- [ ] No tag pushed (tags are maintainer-only; see the release process).

## Testing

<!-- How did you verify this change? Paste key pytest / ruff / manual output. -->

## Screenshots / Output

<!-- Optional: CLI output, Rich table, wiki render. -->

## Related links

<!-- Related PRs, ADO work items, runbooks, ADRs, research notes. -->
