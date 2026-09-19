# Release Process

Canonical release checklist for `sigantry-core` (the base
platform). Releases are tag-triggered: a `v*` tag on `master` triggers
the publish stage that pushes the wheel to the configured package feed.
**Tagging is manual and performed by the maintainer only**; contributors
and agents MUST NOT push tags.

## Release cadence

- **SemVer:** `MAJOR.MINOR.PATCH`.
- **v0.x:** pre-v1 snapshots. Breaking changes may ship in minor bumps.
- **v1.0.0 onwards:** any break to the public Python surface
  (module-level exports), the CLI surface (subapps, flags), or the
  telemetry schema is a MAJOR bump.
- **Release window:** on-demand after a milestone completes and CI is
  green. No fixed cadence.

## Versioning

Version lives in three places that MUST stay aligned (enforced by
`tests/prereqs/test_version_alignment.py`):

1. `sigantry_core/_version.py` - `__version__ = "X.Y.Z"`
   (Hatchling single source of truth via `[tool.hatch.version]`).
2. `CHANGELOG.md` - the most recent dated release heading.
3. `pyproject.toml` - carries `dynamic = ["version"]`; Hatchling
   resolves from `_version.py` at build time. There is no literal
   version string in `pyproject.toml`.

Additional references the grep audit catches (fix manually as part of
the bump):

- `docs/index.md` version banner.
- `docs/getting-started/install.md` pip-install examples.
- `docs/getting-started/quickstart.md` version assertions.
- Any other docs that pin a version (grep for the old version across
  `docs/` + `README.md`, then fix stragglers).

## Pre-release checklist

Run before opening the release PR:

1. `ruff check sigantry_core/ tests/` - clean.
2. `pytest -q` - all green.
3. `pytest tests/prereqs/ -q` - banned-API invariants green.
4. `pwsh -c "Invoke-Pester -Configuration ./tests/Pester.config.ps1"`.
5. Update `CHANGELOG.md`: move `[Unreleased]` to a dated heading.
6. Bump `sigantry_core/_version.py`.
7. Grep for the old version across docs + README; fix stragglers.

## Release PR

- Title: `Release vX.Y.Z`.
- Body: CHANGELOG excerpt + migration notes for breaking changes.
- Merge once two approvals land and CI is green.

## Tag + publish

Maintainer only:

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

The publish pipeline builds the wheel + sdist and uploads to the
configured feed. Verify the artefact appears + `pip install` resolves
the new version.

## Plugin releases

Plugin packages follow their own SemVer clock. Plugin majors may or
may not align with base majors; pinning is the consumer's
responsibility. See each plugin's own `CHANGELOG.md` for its release
notes.
