# Sigantry Demo Walkthrough -- Source-of-truth Script

> Phase 15 / DEMO-03 / CONTEXT D-10. <=200 lines. Edit this file
> to update the walkthrough's spoken/displayed text -- the 5 scene
> components under `src/scenes/` reference the H2 headings here.
> Re-render with `npm run render` after editing.

Approximate runtime: 80 seconds (5 scenes; Scene 2 is 20s, the
others 15s each at 30fps = 2400 frames total).

The walkthrough demonstrates Sigantry's WI -> deploy -> tests ->
audit -> rollback round-trip on the demo Fabric tenant. Every
visible UI element is sourced from a real Sigantry feature (work
items, deploy ledger, drift detection) -- the demo IS Sigantry
dogfooded, no theatre.

## Scene 1: Work Item -- the audit chain begins (15s)

A developer opens an Azure DevOps work item AB-1234 "Add OrderDate
column to OrdersAnalytics". They commit the schema change to a
feature branch, referencing AB-1234 in the commit message. The
work-item link surfaces in the on-screen overlay as the first link
in Sigantry's audit chain: every later artefact (deploy record,
test result, rollback ledger) traces back to this work item ID.

Visible on-screen: a code editor showing the OrdersAnalytics TMDL
table with the new column, plus the ADO work-item card with the
matching `AB-1234` reference.

## Scene 2: Deploy -- 5-stage pipeline runs (20s)

The PR merges to main. The Sigantry-deploy-with-tests workflow
starts in GitHub Actions: deploy -> smoke -> integration ->
approval -> promote. The deploy stage runs `sigantry deploy run`
which calls `fabric-cicd` against the demo Fabric tenant. The
walkthrough zooms in on the workflow run page so the 5 stage names
are legible; the timing strip shows ~3 minutes for the deploy
stage, ~30 seconds for smoke + integration, and a manual approval
gate before promote.

Visible on-screen: the GHA workflow-run UI with all 5 stages
chronologically laid out; the deploy stage logs streaming
`fabric-cicd` API calls.

## Scene 3: Test gates -- smoke + integration tests (15s)

Smoke test runs first: `sigantry doctor` reports green across all
six checks (auth, tenant reachability, capacity health, workspace
state, role assignments, item inventory). Integration test runs
next: `sigantry_pipeline`-marked tests query the deployed semantic
model and validate `Total Sales` returns the expected value
(non-zero, finite). Both gates exit 0; the workflow advances to
the approval stage.

Visible on-screen: the smoke + integration test logs; the green
checkmark on each gate; the pipeline advancing to "Awaiting
approval".

## Scene 4: Audit -- DeployRecord written to ledger (15s)

After the operator approves and the promote stage completes,
`sigantry release record` fires. The JSONL deploy ledger gains an
immutable record: release-id (the GitHub commit SHA),
work-item-ref `AB-1234` (linked back to Scene 1), deploy-record
SHA-256 hash (covers the deployed item set), operator GitHub
identity, and a UTC timestamp. The ledger is append-only and
0o600 permissioned (per Phase 11 TRACE-04 mitigation).

Visible on-screen: the ledger file (formatted as a column-aligned
table) with the new entry highlighted; the audit chain that
traces from `AB-1234` -> commit SHA -> deploy record.

## Scene 5: Rollback -- one command, full recovery (15s)

A subtle bug surfaces in prod: a downstream report shows `Total
Sales` rounding incorrectly. Operator runs `sigantry deploy
--rollback --release-id <previous-sha>`. The demo Fabric workspace
returns to the prior committed state in ~30 seconds; a new
DeployRecord (forward-pointer style) chains the rollback to the
forward-deploy record from Scene 4. Total time-to-recovery: 90
seconds end-to-end (operator decision + rollback execution).

Visible on-screen: the operator's terminal running the rollback
command; the Fabric workspace UI showing the items reverting; the
ledger gaining a final "rollback" entry that closes the audit
chain.

## Editor's note

This script is implementation-grade, not marketing-grade. The
operator (with marketing context) may overwrite this file freely
before the public Test 3 mp4 capture per RESEARCH §Open Questions
#1. Coordinate any major rewrite with `PRODUCT-BRIEF.md`'s `## Demo`
section so the on-screen narrative + brief copy stay aligned.

## Round-trip step coverage

For the round-trip checklist from ROADMAP success criterion 3:

- work item link (Scene 1)
- deploy via `sigantry deploy` (Scene 2)
- test gates: smoke + integration (Scene 3)
- approval gate (end of Scene 2 / start of Scene 4)
- audit ledger via `sigantry release record` (Scene 4)
- rollback via `sigantry deploy --rollback` (Scene 5)
