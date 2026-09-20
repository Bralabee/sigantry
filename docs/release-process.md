# Release Process

Canonical release checklist for `sigantry` (the base platform; see
[ADR-0017](decisions/ADR-0017-distribution-name-sigantry.md) for the
distribution name). Releases are **GitHub-Release-triggered**:
`.github/workflows/publish-pypi.yml` fires on `release: types:
[published]` and on `workflow_dispatch`. There is **no** `push: tags:`
trigger, so pushing a tag on its own publishes nothing. The default
branch is `main`; there is no `master`.
**Tagging and releasing are manual and performed by the maintainer
only**; contributors and agents MUST NOT push tags.

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

Maintainer only. The tag is necessary but **not sufficient** -- the
publish fires on the GitHub Release, not on the tag push:

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <changelog-excerpt>
```

Publishing the Release runs `publish-pypi.yml`, which builds the wheel +
sdist and uploads to PyPI via OIDC trusted publishing (no API token).
A `workflow_dispatch` run against the tag is the manual fallback.
Verify the artefact appears on PyPI and that `pip install sigantry==X.Y.Z`
resolves in a clean environment.

## Known gaps in the published record

- **The 1.0.0 PyPI page does not carry the CLI-troubleshooting section.** The
  `sigantry: command not found` guidance was committed ten minutes *after* the
  1.0.0 upload, and PyPI forbids re-uploading a released version. The wheel is
  otherwise byte-identical to what this tree builds. The next patch release is
  what puts that section on the project page; nothing can change 1.0.0 itself.
- **The Release trigger has never been observed publishing successfully.** Of
  the three 1.0.0 publish runs, two fired on the Release trigger and failed --
  the first on an unresolvable action pin (fixed in `a3b54bc`), the second on
  PyPI `invalid-publisher` -- and the run that succeeded was a
  `workflow_dispatch` twenty-three minutes later, on the same tag, the same
  workflow file and the same `pypi` environment. PyPI matches a trusted
  publisher on owner, repository, workflow filename and environment, none of
  which differed, so the publisher record appears to have been corrected
  server-side in between. That is an inference, not an observation: PyPI's
  publisher configuration cannot be read back. **Treat the next
  Release-triggered run as the confirmation, and keep `workflow_dispatch` as
  the documented fallback if it fails again.**

## Plugin releases

Plugin packages follow their own SemVer clock. Plugin majors may or
may not align with base majors; pinning is the consumer's
responsibility. See each plugin's own `CHANGELOG.md` for its release
notes.
