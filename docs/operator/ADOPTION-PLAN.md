# ADOPTION-PLAN — AIMS + DQ Adoption of Sigantry

**Created:** 2026-06-11. **Owner:** operator (Sanmi) + maintainer agents.
**Status:** ACTIVE — W1.1 already complete (de facto adoption began before this plan).

This plan operationalises the longest-standing open item on the books
("AIMS + DQ adoption — neither sibling repo currently consumes this toolkit",
first recorded 2026-04-21, re-verified 2026-06-11). It also resolves, empirically,
the question "is the company-specific wheel (`sigantry-hs2`) still needed?" —
not by argument, but by giving each of its six components a dated keep/trim
verdict based on real use.

## Principles (inherited from the project contract)

1. **Adoption is optional, not forced.** Each workstream ends at a verdict
   point; "do not adopt" is an acceptable verdict if the evidence says so.
2. **One-way dependency.** Sibling repos depend on `sigantry-core` (and
   optionally `sigantry-hs2`); never the reverse. No sigantry code may import
   AIMS/DQ code at module scope (the banned-API gate enforces this).
3. **Config in consumer repos, code in plugins.** Workspace IDs, manifests and
   `parameters.yml` live in the consuming repo; behaviour lives in the plugin.
4. **Every step has a falsifiable gate** — a command with an expected output,
   in the style of the tutorials and `*-HUMAN-UAT.md` files. No step is "done"
   on intention.
5. **Distribution without PyPI** (PyPI publish is held until UAT closure):
   ADO Artifacts feed preferred for HS2-internal consumers; GitHub Release
   wheel assets as fallback; wheel-by-email per USER-GUIDE section 5.2 for
   one-off testers.

## Current state (all verified 2026-06-11)

| Fact | Evidence |
|---|---|
| 13 AIMS notebooks are ALREADY manifest-governed in `COE_F_ManagedData` | `sync-v2.yml` in `1_AIMS_LOCAL_2026/notebooks/`; releases `sync-publish-2026-06-11T10-50-{30,48}Z` in the deploy ledger; drift `=13 -0 ~0` |
| Workspace content matches AIMS local sources cell-for-cell | pull-and-compare audit, 3 notebooks confirmed identical (Tutorial 05 recipe) |
| Neither sibling repo imports the toolkit | grep of both repos, re-verified 2026-06-11 |
| `sigantry-hs2`'s six components have zero production consumption | consumption audit: contract/integration tests only |
| SPN has no role on `COE_F_ManagedData` | V3-RISK-3 / OPERATOR-PUNCHLIST A4; all ManagedData ops run as operator `az login` |
| No Teams webhook secret provisioned | PUNCHLIST C1/C2 open |
| Only the `sigantry-core` wheel is attached to GitHub Releases | release `v3.0.0-rc.1` assets |
| ADO Artifacts feed availability | **UNVERIFIED** — W0.2 confirms |

## Workstream W0 — Platform prerequisites (unblocks everything else)

| # | Step | Owner | Gate (falsifiable) |
|---|---|---|---|
| W0.1 | Tenant admin grants the SPN `Member` on `COE_F_ManagedData` (+ `vso.work_write` on the ADO project if work-item linking is wanted) | operator | `sigantry workspace get 2f4d899a-…` succeeds under SPN credentials (3 `AZURE_*` env vars or WIF); closes V3-RISK-3 / A4 |
| W0.2 | Confirm Azure Artifacts is enabled on `dev.azure.com/HS2-DataAndAnalytics`; create feed `sigantry`; `twine upload` the core + hs2 rc1 wheels | operator | `pip install sigantry-core --index-url https://pkgs.dev.azure.com/HS2-DataAndAnalytics/_packaging/sigantry/pypi/simple/` succeeds in a CLEAN conda env |
| W0.3 | Attach `sigantry-hs2` (and `-jtoye`) wheels to GitHub Releases as the fallback channel | maintainer | release assets list shows all three wheels on the next tag |
| W0.4 | Provision `SIGANTRY_TEAMS_WEBHOOK` repo secret (Teams incoming webhook) | operator | local sink smoke test posts a card (Tutorial 08 step 3: `python -m sigantry_core.sync._notify_main`) |

## Workstream W1 — AIMS adoption (deepen the existing beachhead)

| # | Step | Owner | Gate |
|---|---|---|---|
| W1.1 | Manifest governance of the 13 notebooks | — | **DONE 2026-06-11** (see Current state) |
| W1.2 | Commit the governance artefacts properly in the AIMS repo: `sync-v2.yml` + `parameters.yml` + a README section explaining the manifest contract; add `sigantry-core` to AIMS `environment.yml` (from the W0.2 feed) | maintainer | AIMS repo PR merged; `conda run -n aims_data_platform sigantry --help` works. CAUTION: AIMS pins `numpy<2.0` — verify no dependency conflict before merging |
| W1.3 | Scheduled drift check in AIMS CI against ManagedData, consuming the `drift-check.yml` `workflow_call` half (inputs are camelCase: `workspaceId`/`manifestPath`) | maintainer | manual run green; injected portal rename produces a red run + Teams card; reconcile returns green. NOTE decision #4: ManagedData is shared (`+154` benign) — alert on `removed`/`modified` from the JSON, NOT `--fail-on-drift`, until the manifest-scoped drift flag ships (V3.X candidate) |
| W1.4 | Content-update path: when AIMS edits notebook content, republish via `sigantry deploy run` scoped with `--items-to-include` (staging tree packed from the manifest — the pack driver from the 2026-06-11 session is the template) | maintainer | one real content change shipped; ledger shows the release; pull-and-compare confirms parity |
| W1.5 | **AimsDeployProfile verdict point**: exercise `sigantry-hs2`'s AIMS deploy profile against the real AIMS wheel-publication flow once W1.2-W1.4 are live | operator + maintainer | either (a) profile used in a real AIMS deploy with ledger evidence -> KEEP, or (b) base verbs proved sufficient -> record TRIM verdict in W4 |

## Workstream W2 — DQ adoption

| # | Step | Owner | Gate |
|---|---|---|---|
| W2.1 | `DqFrameworkGate` livecheck against a real `dq_framework` install (`pip install -e` the DQ repo next to sigantry; run `sigantry-hs2-livecheck` + `tests/integration/dq/test_live_gate.py`) | maintainer | livecheck exits 0; live gate test passes against a real Lakehouse dataset |
| W2.2 | Wire the DQ gate into one deploy pipeline as a promotion gate (`sigantry dq` stage in the 5-stage template pair) | maintainer | negative test: a deliberately failing DQ suite BLOCKS promotion; positive test: passing suite promotes. Both runs in the ledger |
| W2.3 | DQ repo's own notebooks under manifest governance (mirror of W1.1-W1.3 for the DQ workspace) | maintainer | drift baseline `=N -0 ~0` + idempotent re-run clean |
| W2.4 | **DqFrameworkGate verdict point** | operator | KEEP (gating a real pipeline) or TRIM, recorded in W4 |

## Workstream W3 — Governance baseline (independent, cheap, start anytime)

| # | Step | Owner | Gate |
|---|---|---|---|
| W3.1 | Monthly `rbac-audit --output csv` + `tenant-settings export` committed to a governance evidence area (Tutorial 10 ritual) | operator | two consecutive monthly baselines committed; digest-compare answers "what changed" |
| W3.2 | Review every `Admin` row from the first export | operator | sign-off note attached to the evidence commit |

## Workstream W4 — The wheel verdict (closes the "is sigantry-hs2 still needed?" question)

After W1.5 and W2.4, each `sigantry-hs2` component gets a dated verdict:

| Component | Verdict driver |
|---|---|
| `AimsDeployProfile` | W1.5 |
| `DqFrameworkGate` | W2.4 |
| `LogAnalyticsSink` | does any adopted pipeline emit telemetry to Log Analytics? If not exercised by end of W1/W2: TRIM candidate |
| `Hs2EntraGroupAuth` | superseded by base `TokenProvider` + WIF? If unused: TRIM candidate |
| `Hs2TeamsRunbookRegistry` | does the drift-alert path use runbook URLs? Evaluate with W1.3 |
| `Hs2CapacityPolicy` | any capacity pause/resume automation requested? If not: TRIM candidate |

**Rules:** TRIM = delete the component (not the package) with a CHANGELOG entry
and contract-test cleanup; the package itself stays as long as ONE component
earns its keep (the plugin architecture is load-bearing regardless — banned-API
gate, entry-point registry, JToye pattern). If ALL six trim, fold the remaining
HS2 config into consumer repos and retire the wheel with a migration note.

## Sequencing

```
W0.1 ──> W1.3 (SPN needed for unattended CI)
W0.2 ──> W1.2, W2.1 (consumers install from the feed)
W0.4 ──> W1.3 (alerts need the webhook)
W1.2 -> W1.3 -> W1.4 -> W1.5 (in order)
W2.1 -> W2.2 -> W2.4;  W2.3 independent after W0.2
W3 anytime; W4 after W1.5 + W2.4
```

Nothing here is calendar-bound; each gate is evidence-bound. The single
critical-path item is **W0.1 (SPN grant)** — it is operator-bound, blocks all
unattended automation, and has been open since 2026-05-12.

## Risks

- **AIMS env conflicts**: AIMS pins `numpy<2.0`; sigantry-core's dependency
  tree must be checked against it before W1.2 merges (gate includes this).
- **Shared-workspace drift noise**: ManagedData carries ~154 ungoverned items;
  W1.3 must alert on `removed`/`modified` only until the manifest-scoped
  drift flag ships (tracked in V3.X-ROADMAP candidates).
- **Adoption fatigue**: every workstream has an explicit verdict point so a
  "no" is cheap and recorded, not silently stalled.

## Success criteria (plan-level)

1. AIMS: scheduled drift check running unattended for 2 consecutive weeks with
   correct alerting (W1.3) + one content update shipped through the audited
   deploy path (W1.4).
2. DQ: one promotion blocked by a failing DQ suite and one promoted by a
   passing suite, both ledger-evidenced (W2.2).
3. Distribution: `pip install sigantry-core` from the ADO feed in a clean env
   (W0.2).
4. The wheel question answered with six dated verdicts (W4).
