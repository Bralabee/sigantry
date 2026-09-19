# Sigantry walkthrough -- Remotion subproject

Phase 15 / DEMO-03. Produces `out/walkthrough.mp4` -- the recorded
walkthrough of Sigantry's WI -> deploy -> tests -> audit -> rollback
round-trip on the demo tenant.

## Local render

```bash
cd scripts/remotion
nvm use            # picks up .nvmrc -> Node 22.22.2
npm ci             # installs deterministic deps from package-lock.json
npm run render     # outputs out/walkthrough.mp4
```

The `out/` directory is gitignored (RESEARCH Pitfall 4 -- mp4 bloat).
The canonical mp4 location is GitHub Releases on the public
`demo-sigantry` repo (15-HUMAN-UAT.md Test 3, operator-bound).

## Asset swap (placeholder -> demo)

`src/assetSelector.ts` auto-prefers `assets/demo/<name>.png` over
`assets/placeholder/<name>.png` at bundle time, via Vite's
`import.meta.glob` pattern (RESEARCH Pattern 3 + Code Example 2).
To use real demo screenshots:

1. Capture 5 PNGs from the live demo workspace UI:
   - `scene1-workitem.png`
   - `scene2-deploy.png`
   - `scene3-tests.png`
   - `scene4-audit.png`
   - `scene5-rollback.png`
2. Drop them into `scripts/remotion/assets/demo/`.
3. Re-run `npm run render`. The composition picks up the new
   assets automatically -- no code change required.

The `assets/demo/` directory is empty in tree (only `.gitkeep`);
operator captures land out-of-band per `15-HUMAN-UAT.md` Test 3.

## Sibling project

There is an external Remotion 4.0.451 reference toolchain (the
working tutorial template that pre-dates Sigantry). It is **not
vendored into this repo** -- it lives outside the working tree to
keep the repo lean (`remotion-tutorial/` is also listed in
`.gitignore` as a defensive directive in case a contributor clones
it adjacent to this repo). Both projects pin to Remotion 4.0.451;
**coordinate any version bump across BOTH `package.json` files
together** (RESEARCH Pitfall 7 -- lockfile drift between siblings).

Note that this subproject (`scripts/remotion/`) does NOT enable
Tailwind, while the external reference template does. The dep set is
otherwise intentionally aligned for the version bump coordination
above.

## CI workflow

`.github/workflows/sigantry-demo-mp4.yml` re-renders the mp4 on
every PR touching `scripts/remotion/**` and uploads the result as a
workflow artefact (retention 30 days). On `master` / `main` push the
same workflow runs and produces the canonical artefact; the operator
promotes a chosen run to a public GitHub Release per the demo-tenant
operator runbook (Plan 15-04).

The workflow carries a `# sigantry-dual-ci-exception: ci-mechanics`
annotation so the dual-CI parity registry
(`scripts/ci/check-dual-ci-parity.py`) skips it -- the mp4-build
workflow has no ADO equivalent because it builds the demo asset
itself, not a user-facing CI template adopters consume.

## Source-of-truth script

`script.md` is the markdown source-of-truth for the walkthrough's
spoken/displayed text -- edit this file (<=200 lines per CONTEXT
D-10) and re-render. The 5 H2 headings (`## Scene 1: ...` through
`## Scene 5: ...`) correspond to the 5 scene components under
`src/scenes/`.

## Determinism

`scripts/build-walkthrough.sh` (one level up) wraps `npm ci` +
`npm run render` with explicit `--codec h264 --jpeg-quality 80`
flags so re-runs produce a reproducible mp4 (modulo container
timestamp metadata).

## Pin versions (Pitfall 7)

All deps are pinned **without caret** (no `^4.0.451`). The lockfile
(`package-lock.json`) is committed so CI's `npm ci` resolves an
identical dep tree on every run.

| Package | Pin | Source |
| --- | --- | --- |
| `remotion` | `4.0.451` | matches `/remotion-tutorial/` |
| `@remotion/cli` | `4.0.451` | matches `/remotion-tutorial/` |
| `react` | `19.2.3` | matches `/remotion-tutorial/` |
| `react-dom` | `19.2.3` | matches `/remotion-tutorial/` |
| `typescript` | `5.9.3` | matches `/remotion-tutorial/` |
| `@types/react` | `19.2.7` | matches `/remotion-tutorial/` |
| `eslint` | `9.19.0` | matches `/remotion-tutorial/` |
| `@remotion/eslint-config-flat` | `4.0.451` | matches `/remotion-tutorial/` |

Bumping any of the above requires updating BOTH `scripts/remotion/`
and `/remotion-tutorial/` together (Pitfall 7).
