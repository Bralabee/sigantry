# Release Process

Canonical release checklist for `sigantry` (the base platform; see
[ADR-0017](decisions/ADR-0017-distribution-name-sigantry.md) for the
distribution name). Releases are **GitHub-Release-triggered**:
`.github/workflows/publish-pypi.yml` fires on `release: types:
[published]` and on nothing else. There is **no** manual trigger and **no**
`push: tags:` trigger, so pushing a tag on its own publishes nothing. The
default branch is `main`; there is no `master`.
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

`sigantry_core/_version.py` (`__version__ = "X.Y.Z"`) is the single
source: `pyproject.toml` declares `dynamic = ["version"]` and Hatchling
reads `_version.py` through `[tool.hatch.version]`, so there is no literal
version in `pyproject.toml`. Two other places must name the same version
(enforced by `tests/prereqs/test_version_alignment.py` and
`tests/prereqs/test_changelog.py`):

1. `CHANGELOG.md` - the most recent dated heading, `## [X.Y.Z] - YYYY-MM-DD`,
   below `## [Unreleased]`.
2. `docs/index.md` - the first `vX.Y.Z` on the page, which opens the
   "Current release" section: put the new release's paragraph above the
   previous one.

The other version tests read the version from `_version.py`, so a bump
needs no test edit. These state the current version and no test checks
them; update them in the bump:

- `docs/USER-GUIDE.md`: the `<!-- VERSION: X.Y.Z -->` marker on line 1
  (the rendered PDF's cover reads it) and the "Versioning" paragraph.
- `docs/handbook.md`: the "Last updated" and "Toolkit version" lines.

Then search `README.md` and `docs/` for the previous version and fix any
sentence that states the current one. Leave `schema_version: "1.0.0"`
alone: it is the version of the manifest and report schemas, not of the
package, and a `sync.yml` accepts only `"1.0"` or `"1.0.0"` there.

## Pre-release checklist

Run before opening the release PR:

1. `ruff check sigantry_core/ tests/ scripts/` and
   `ruff format --check sigantry_core/ tests/ scripts/` - clean.
2. `mypy sigantry_core/ scripts/` - clean. (`Type Check (mypy)` is a
   required status check on `main`.)
3. `python -m pytest` - all green. Do not add `-q`: `addopts` already
   carries one and `-qq` prints no counts at all, so a run that collected
   nothing looks identical to a passing one.
4. `python -m pytest tests/prereqs/` - green. Names are not checked here:
   the `Name gate` check covers each pull request, and the release
   workflow runs the same gate over the released tree and the wheel and
   sdist it uploads (`--dist`). It uses the same exception register as the
   pull-request check, so a release of a ref whose registered lines have
   moved fails until the register fits that ref. A version bump needs no
   register edit: a distribution member identical to the tree file at its
   path is judged by that file's entry, and a hit in the core metadata is
   keyed by its field (`#Author:1`), or in a field that can repeat, such
   as `Classifier`, by its entry, not by its line. Before tagging, the
   maintainer runs the release scan on the release commit locally, in one
   shell from the repository root: `out=$(mktemp -d)`, then
   `python -m build --outdir "$out"`, then
   `python scripts/ci/check-name-gate.py --root . --dist "$out" --list-file <list>`.
   The build goes to a new, empty directory because `python -m build` adds
   to its output directory rather than emptying it, and `--dist` refuses a
   directory that holds anything but one wheel and one sdist: a `dist/`
   that still holds an earlier version's files fails the scan with exit 2
   before it reads anything.
5. `pwsh -c "Invoke-Pester -Configuration ./tests/Pester.config.ps1"`.
6. Update `CHANGELOG.md`: add the dated heading `## [X.Y.Z] - YYYY-MM-DD`
   directly below `## [Unreleased]`, so the unreleased entries become that
   release's and `## [Unreleased]` stays on top (the alignment test
   requires it above the newest dated heading).
7. Bump `sigantry_core/_version.py`, then reinstall the package in your
   environment (`pip install -e .`):
   `tests/sigantry_core/test_version_single_source.py` compares the
   imported version with the installed metadata, which an editable install
   records when it is installed.
8. Grep for the old version across docs + README; fix stragglers.
9. Run steps 1-5 again on the bumped tree.

## Release PR

- Title: `Release vX.Y.Z`.
- Body: CHANGELOG excerpt + migration notes for breaking changes.
- Merge once every required check is green: CI, the `Name gate` and the
  `review-record` status. Branch protection does not require an approving
  review count; the review record is the review gate.

## Tag + publish

Maintainer only. The tag is necessary but **not sufficient** -- the
publish fires on the GitHub Release, not on the tag push.

Tag the release PR's merge commit. Update `main` first and check that the
commit you tag declares the version you are releasing. Nothing in the
release workflow compares the tag with `_version.py`, and with
`skip-existing` on, a tag on a commit that declares an already-uploaded
version builds files PyPI already holds and can finish without publishing
anything new:

```bash
git switch main && git pull --ff-only
git show HEAD:sigantry_core/_version.py   # must declare X.Y.Z
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file <changelog-excerpt>
```

Publishing the Release runs `publish-pypi.yml`, in two halves:

1. `quality` runs `ci.yml`: lint, type check and the test matrix, then its
   `build` job builds the sdist and wheel, runs `twine check --strict`,
   prints and records their SHA-256, and uploads them as the `dist`
   artifact. It is passed no secret and cannot request an OIDC token, and
   the `build` job checks out without persisting its read-only GitHub
   token, so the build tools it installs from PyPI cannot read that token
   from the git config.
2. `publish` waits for the `pypi` environment's reviewer (the environment
   admits only `v*` tags). It downloads that `dist` artifact, verifies it
   against the recorded SHA-256, scans the tree and both distributions with
   the name gate, and uploads exactly those files to PyPI through OIDC
   trusted publishing (no API token). It builds nothing and installs
   nothing from PyPI.

If the run fails, use **Re-run all jobs**: the build, its checks and the
scan run again on the same commit, with that commit's workflow files. A
re-run therefore recovers from a cause outside the repository -- a
rejected or expired environment approval, the PyPI trusted-publisher
record, the `NAME_GATE_TOKENS` secret, a transient runner or network
failure -- but not from one in the tagged commit. For that, fix it on
`main` and release again: if nothing reached PyPI, delete the Release and
the tag and create them on the fixed commit; once a file of X.Y.Z is on
PyPI, release the next patch version instead. There is no manual trigger
to fall back on. `skip-existing` stays on so that a re-run can finish an
upload that stopped after one file. Verify that the files appear on PyPI
with the SHA-256 the `build` job printed (a file a re-run skipped keeps
the digest of the attempt that uploaded it), and that
`pip install sigantry==X.Y.Z` resolves in a clean environment.

## Known gaps in the published record

- **1.0.0 was not published by the Release trigger.** Of the three 1.0.0
  publish runs, two fired on the Release trigger and failed -- the first on
  an unresolvable action pin (fixed in `3d5eda7`), the second on PyPI
  `invalid-publisher` -- and the run that succeeded was a
  `workflow_dispatch` twenty-three minutes later, on the same tag, the same
  workflow file and the same `pypi` environment. PyPI matches a trusted
  publisher on owner, repository, workflow filename and environment, none
  of which differed, so the publisher record appears to have been corrected
  server-side in between. That is an inference, not an observation: PyPI's
  publisher configuration cannot be read back. The manual trigger has since
  been removed, so every release now goes through the Release trigger.

## Plugin releases

Plugin packages follow their own SemVer clock. Plugin majors may or
may not align with base majors; pinning is the consumer's
responsibility. See each plugin's own `CHANGELOG.md` for its release
notes.
