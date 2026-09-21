# Contributing to Sigantry

Thanks for your interest in the vendor-agnostic Sigantry platform
(formerly Fabric DataOps Toolkits; renamed in v3.0 per ADR-0011). This
document is the contract for contributors to the base package. Plugin
packages follow their own contribution guide.

The canonical copy lives at the repo root
([`CONTRIBUTING.md`](https://github.com/Bralabee/sigantry/blob/main/CONTRIBUTING.md)).
This page adds the PowerShell-side detail that the root file does not
carry; where the two ever disagree, **the root file wins**.

## Ground rules

- **Feature branches + PR for every change.** No direct commits to
  `main` (the default branch; there is no `master`). No force pushes
  under any circumstances.
- **Python 3.11+ and PowerShell 7.4.** Support 3.11, 3.12 and 3.13;
  never require PS 5.1.
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
git clone https://github.com/Bralabee/sigantry.git && cd sigantry
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,test]"   # both extras: `dev` alone cannot collect the suite
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
4. Open a PR against `main`. Fill in the template, link the issue,
   and call out any user-facing changes.
5. CI must be green before merge (lint, tests, banned-API grep gate,
   PSScriptAnalyzer, pre-commit hooks).
6. Merge once review is complete and every automated CI check is green.

## Testing expectations

- **Always run pytest with `TERM=dumb`:**

  ```bash
  NO_COLOR=1 TERM=dumb python -m pytest --color=no
  ```

  The CLI tests assert on plain strings from `CliRunner(...).output`. Typer
  renders help and error output through Rich, so with a real `TERM` value
  those strings arrive full of ANSI escapes and box-drawing and the
  assertions fail for no reason. `NO_COLOR=1` on its own is not enough --
  it removes colour but not the panel layout. (The repo-root `conftest.py`
  now sets `NO_COLOR=1` and `TERM=dumb` for every run, so a bare
  `pytest` is already safe; the explicit form is kept for runners that
  bypass conftest.)
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
