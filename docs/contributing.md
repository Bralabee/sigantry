# Contributing to sigantry-core

Thanks for your interest in the vendor-agnostic Sigantry platform
(formerly Fabric DataOps Toolkits; renamed in v3.0 per ADR-0011). This
document is the contract for contributors to the base package. Plugin
packages follow their own contribution guide.

The canonical copy lives at the repo root (`CONTRIBUTING.md`). This page
mirrors that content.

## Ground rules

- **Feature branches + PR for every change.** No direct commits to
  `master`. No force pushes under any circumstances.
- **Python 3.11 + PowerShell 7.4.** Never require 3.12+ or PS 5.1.
- **No emojis in code, docs, runbooks, or PR descriptions.** Plain-ASCII
  markdown only.
- **One HTTP client:** `sigantry_core.client` is the only
  module allowed to import `httpx`. Ruff `TID251` enforces this
  repo-wide.
- **Read-only by default.** Any function that mutates remote state must
  take `force=True` and write an audit entry.
- **The base package must stay vendor-agnostic.** No tenant-specific
  string, environment variable name, stream name, resource prefix, or
  runbook URL may land under `sigantry_core/` or in base
  docs. Those belong in a plugin package.

## Local development setup

```bash
git clone <repo-url> && cd sigantry-core
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pwsh -c "Install-Module Pester -RequiredVersion 5.7.1 -Force -SkipPublisherCheck"
pwsh -c "Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Force"
```

Verify:

```bash
python -c "import sigantry_core; print(sigantry_core.__version__)"
ruff check sigantry_core/ tests/
pytest -q
pwsh -c "Invoke-Pester -Configuration ./tests/Pester.config.ps1"
```

## Commit + PR workflow

1. Open an issue (or pick one from the backlog).
2. Cut a feature branch: `feature/<short-slug>`.
3. Make atomic commits. One logical change per commit, written in
   imperative mood. Reference the requirement id if relevant.
4. Open a PR against `master`. Fill in the template, link the issue,
   and call out any user-facing changes.
5. CI must be green before merge (lint, tests, banned-API grep gate,
   PSScriptAnalyzer, pre-commit hooks).
6. Squash-merge when two reviewers approve.

## Testing expectations

- **Always run pytest with `TERM=dumb`:**

  ```bash
  NO_COLOR=1 TERM=dumb python -m pytest --color=no
  ```

  The CLI tests assert on plain strings from `CliRunner(...).output`. Typer
  renders help and error output through Rich, so with a real `TERM` value
  those strings arrive full of ANSI escapes and box-drawing and the
  assertions fail for no reason. Measured 2026-08-13: 1 failure with
  `TERM=dumb`, 36 with `TERM=xterm-256color`. `NO_COLOR=1` on its own is
  not enough — it removes colour but not the panel layout.
- **Unit tests:** pytest with HTTP calls mocked. Target 85%+ coverage
  on any file you touch.
- **Banned-API invariants:** `tests/prereqs/test_phase8_banned_apis.py`
  asserts that no tenant-specific branding re-enters the base. Do not
  attempt to bypass the guard; fix your change instead.
- **Pester:** PowerShell parity for any PS cmdlet under `Sigantry/`
  (renamed from `Fabric/` in v3.0 per ADR-0011) or plugin PS modules.

## Release

See [release-process.md](release-process.md) for the SemVer contract
and release checklist. Tagging is maintainer-only.
