# JToye Onboarding -- Operator Runbook (Phase 16 / SEAM-04..06)

**Audience:** JToye Digital engineering lead + JToye platform operators
provisioning the JToye plugin fork of `sigantry-jtoye`.

**Status:** v3.0 phase 16 closure runbook. Live JToye sign-off is operator-bound
per V3-RISK-2; this runbook is the procedure JToye runs against their own
infra. Sigantry CI never touches JToye's tenant or repo.

**Cross-references:**

- Commercial model: [ADR-0010 -- Apache-2.0 commercial model](../decisions/ADR-0010-commercial-model.md)
- Rename + namespace: [ADR-0011 -- rename to Sigantry](../decisions/ADR-0011-rename-to-sigantry.md)
- Seam reference: [Reference > Seams](../reference/seams.md)
- Live UAT script: `.planning/phases/16-seam-expansion-jtoye-trial/16-HUMAN-UAT.md`
  (operator records sign-off here)
- Env-var contract: `scripts/live-creds.template` Phase 16 block

---

## 1. Prerequisites

Before forking `sigantry-jtoye/`, JToye Digital must hold the following
infrastructure pieces. None of them are provisioned by this runbook -- they
are JToye-internal decisions.

### 1.1 JToye Fabric tenant

- A Microsoft Fabric tenant under JToye's Azure subscription (NOT shared with
  the upstream maintainer -- the multi-org claim requires JToye to run on
  JToye's own tenant).
- A dedicated Fabric workspace for the SEAM-06 live UAT test. Empty workspaces
  are simpler; pre-bound (Git-integrated) workspaces work too if drift is
  documented.
- An F-SKU capacity assigned to the workspace (F2 trial is sufficient for the
  UAT round-trip).

### 1.2 JToye SPN with deploy + release-record permissions

A service principal in JToye's Entra tenant with the following permissions:

| API | Permission | Why |
|-----|-----------|-----|
| Microsoft Fabric | `Workspace.ReadWrite.All` (delegated or app) | `sigantry deploy run` creates Notebooks / Lakehouses |
| Microsoft Fabric | `Capacity.Read.All` | `sigantry doctor` introspects capacity |
| Power BI Service | `Tenant.ReadWrite.All` (optional) | only if JToye uses tenant-settings export |

The SPN's client id + secret OR a workload-identity federation
configuration must be available to JToye's CI. Sigantry uses
`DefaultAzureCredential` so any of `AZURE_CLIENT_ID` /
`AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET` env vars OR
`az login` OR managed identity will work.

### 1.3 JToye-owned repository

JToye operator picks ONE of:

- **GitHub:** A new repo at `jtoye/sigantry-jtoye` (or any JToye-owned org).
  Public visibility is fine; private is supported via PAT or GitHub App auth.
- **ADO:** A new project at `https://dev.azure.com/<jtoye-org>/sigantry-jtoye/`
  with a single git repo named `sigantry-jtoye`.

The repo is the canonical home for JToye's plugin fork. `sigantry-core`
(the upstream) does NOT consume JToye-side commits; the dependency direction
is one-way: JToye's `pyproject.toml` depends on `sigantry-core>=3.0.*`.

### 1.4 Tooling on the operator's laptop

- Python 3.11+ (`pyproject.toml` pins `>=3.11,<3.13`)
- conda or venv (conda recommended -- matches sigantry-core's
  `fabric-dataops-toolkits` env name)
- `gh` CLI >= 2.40 (for the GitHub path) OR `az` CLI with
  `azure-devops` extension (for the ADO path)
- `git` >= 2.40

---

## 2. Fork Procedure

### 2.1 Clone the sigantry-core monorepo

```bash
git clone https://github.com/hs2labs/sigantry sigantry-core
cd sigantry-core
git checkout v3.0.0   # or the latest stable v3.x tag
```

### 2.2 Copy `sigantry-jtoye/` into a JToye-owned working tree

```bash
TMP=$(mktemp -d)
cp -r sigantry-jtoye/. "$TMP/sigantry-jtoye-fork/"
cd "$TMP/sigantry-jtoye-fork"
```

The copied tree contains:

```
.
+-- sigantry_jtoye/
|   +-- __init__.py
|   +-- _version.py
|   +-- notifications.py        <-- placeholder; JToye replaces
|   +-- workitems.py            <-- placeholder; JToye replaces
+-- tests/
|   +-- conftest.py
|   +-- test_notifications.py
|   +-- test_workitems.py
|   +-- test_doctor_lists_jtoye_plugins.py
|   +-- fixtures/
|       +-- jtoye_minimal_tree/  <-- live UAT fixture (Plan 16-04)
+-- .github/workflows/ci.yml     <-- self-contained per Open-Q-4
+-- azure-pipelines.yml          <-- self-contained per Open-Q-4
+-- pyproject.toml
+-- README.md
```

### 2.3 Adapt `pyproject.toml`

Replace the placeholder `[project] urls` + `[project] authors` blocks with
JToye-owned values:

```toml
[project]
authors = [
  { name = "JToye Digital Engineering", email = "engineering@jtoye.example" },
]

[project.urls]
homepage = "https://github.com/jtoye/sigantry-jtoye"
documentation = "https://docs.jtoye.example/sigantry-jtoye"
repository = "https://github.com/jtoye/sigantry-jtoye"
```

The entry-point declarations stay verbatim:

```toml
[project.entry-points."sigantry.notification_sinks"]
jtoye = "sigantry_jtoye.notifications:JtoyeNotificationSink"

[project.entry-points."sigantry.work_item_providers"]
jtoye = "sigantry_jtoye.workitems:JtoyeWorkItemProvider"
```

### 2.4 Replace the placeholder plugin impls

The placeholder impls are deliberately incomplete -- they prove the Protocol
contract holds at the type-checker + runtime-isinstance layers, but they
post to `https://jtoye.example.com/webhook` (a guaranteed-unreachable host)
so the placeholder cannot accidentally hit a real endpoint.

JToye operator replaces:

- `sigantry_jtoye/notifications.py` -- swap the placeholder webhook URL +
  HTTP body with JToye's real notification path. The Protocol surface
  (`name: str`, `send(event, *, channel=None) -> None`, `ping() -> None`)
  stays unchanged. See `docs/reference/seams.md` for the full method
  signature contract.
- `sigantry_jtoye/workitems.py` -- swap the placeholder synthetic-WorkItem
  body for JToye's real WorkItem service (ADO Work Items, Jira, internal
  ticketing system, etc.). The Protocol surface is the Phase 11
  `WorkItemProvider` seam unchanged.

### 2.5 Push to the JToye-owned repo

GitHub path:

```bash
gh repo create jtoye/sigantry-jtoye --public \
  --description "Sigantry plugin for JToye Digital (Phase 16)"
git init -b main
git add -A
git commit -m "Initial fork from sigantry-core/sigantry-jtoye (v3.0.0; Phase 16)"
git remote add origin git@github.com:jtoye/sigantry-jtoye.git
git push -u origin main
```

ADO path:

```bash
az devops project create --name sigantry-jtoye \
  --org "$JTOYE_ADO_ORG" --visibility private
git remote add ado "https://dev.azure.com/<jtoye-org>/sigantry-jtoye/_git/sigantry-jtoye"
git push -u ado main
```

---

## 3. CI Wiring

### 3.1 Self-contained CI -- no upstream `uses:` imports

Per RESEARCH §Open-Q-4 the workflows in `sigantry-jtoye/` are
**self-contained**: they install `sigantry-core` from PyPI directly,
do NOT `uses: hs2labs/sigantry/...` reusable workflows, and run their
own pytest + entry-point smoke. JToye operators do NOT need to also
fork the upstream monorepo's CI workflows.

### 3.2 Replace the runner pool

The placeholder workflows ship with `runs-on: ubuntu-latest` (GHA) and
`vmImage: ubuntu-latest` (ADO). If JToye uses self-hosted runners or
a private agent pool, replace these strings before pushing:

```yaml
# .github/workflows/ci.yml
jobs:
  test:
    runs-on: [self-hosted, jtoye-pool]   # was: ubuntu-latest

# azure-pipelines.yml
pool:
  name: jtoye-private-pool                # was: vmImage: ubuntu-latest
```

### 3.3 Wire the JToye CI secrets

The CI workflows need three secrets from JToye's secret store (KeyVault,
GitHub Actions secrets, ADO variable groups -- pick one; Phase 16
ships reference impls for all three):

- `SIGANTRY_JTOYE_FABRIC_TOKEN` -- a Fabric SPN access token (rotates per
  JToye's rotation cadence)
- `SIGANTRY_JTOYE_TENANT_ID` -- the JToye Entra tenant guid
- `SIGANTRY_JTOYE_WORKSPACE_ID` -- the target Fabric workspace guid

GitHub Actions:

```bash
gh secret set SIGANTRY_JTOYE_FABRIC_TOKEN --repo jtoye/sigantry-jtoye --body "$VALUE"
gh secret set SIGANTRY_JTOYE_TENANT_ID    --repo jtoye/sigantry-jtoye --body "$VALUE"
gh secret set SIGANTRY_JTOYE_WORKSPACE_ID --repo jtoye/sigantry-jtoye --body "$VALUE"
```

ADO variable groups:

```bash
az pipelines variable-group create --name sigantry-jtoye-secrets \
  --org "$JTOYE_ADO_ORG" --project sigantry-jtoye \
  --variables SIGANTRY_JTOYE_TENANT_ID=$VALUE \
              SIGANTRY_JTOYE_WORKSPACE_ID=$VALUE
# Then mark FABRIC_TOKEN secret via az pipelines variable-group variable update.
```

### 3.4 Verify `sigantry doctor` smoke

The CI workflows include a `sigantry doctor` smoke step that asserts the
JToye plugins register correctly:

```bash
pip install -e .
sigantry doctor                  # lists 17+ plugins, 2 prefixed `jtoye`
python -c "from importlib.metadata import entry_points; \
  print('jtoye' in {ep.name for ep in entry_points(group='sigantry.notification_sinks')})"
# => True
```

---

## 4. Live Verification (`tests/integration/jtoye`)

After the JToye CI is GREEN on the initial commit + the JToye plugin impls
are in place (Section 2.4), the JToye operator runs the live integration
tests against the JToye Fabric tenant.

### 4.1 Set the env vars

```bash
export PYTEST_RUN_INTEGRATION=1
export SIGANTRY_JTOYE_FABRIC_TOKEN=<fabric SPN access token>
export SIGANTRY_JTOYE_TENANT_ID=<jtoye tenant guid>
export SIGANTRY_JTOYE_WORKSPACE_ID=<jtoye workspace guid>
# All three GitHub coordinates are operator-supplied -- the toolkit
# carries NO defaults; the live test skips cleanly when any is unset.
export SIGANTRY_JTOYE_GITHUB_OWNER=<github owner / org name>
export SIGANTRY_JTOYE_GITHUB_REPO=<github repo name>
export SIGANTRY_JTOYE_GITHUB_WORK_ITEM_ID=<existing issue or PR number>
export GITHUB_TOKEN=<github pat with pull-requests:write or repo scope>
# (SIGANTRY_GITHUB_TEST_PAT is honoured as a secondary fallback so
#  operators can share one PAT across Phase 11 + Phase 14 + Phase 16.)
```

### 4.2 Run the live tests

From the **upstream** sigantry-core repo (the live tests live there, not in
the JToye fork):

```bash
cd <sigantry-core repo-root>
conda run -n fabric-dataops-toolkits \
  pytest -m sigantry_jtoye tests/integration/jtoye --tb=short -v
```

Expected output:

```
tests/integration/jtoye/test_live_e2e.py::test_live_jtoye_deploy_and_release_record_round_trip PASSED
tests/integration/jtoye/test_live_e2e.py::test_live_jtoye_audit_log_line_appears_after_release PASSED

2 passed in <NN>s
```

### 4.3 Skip semantics (`SIGANTRY_JTOYE_*` unset)

When any of `SIGANTRY_JTOYE_FABRIC_TOKEN` / `_TENANT_ID` /
`_WORKSPACE_ID` is unset OR empty, both tests SKIP with the message
`Phase 16 live integration test skipped -- missing env vars: <list>`.
This is the V3-RISK-3 skip-when-absent pattern -- CI runs without
JToye creds, tests skip cleanly, exit 0.

---

## 5. Audit-Log Export (sign-off evidence)

### 5.1 Locate the audit log

Sigantry writes the deploy ledger to `~/.sigantry/audit/deploys.jsonl` by
default. Each line is a frozen `DeployRecord` pydantic v2 model:

```json
{
  "workspace": "<jtoye workspace guid>",
  "release_id": "jtoye-uat-<8-char-prefix>-<ISO-timestamp>Z",
  "work_items": ["jtoye-uat-1"],
  "fabric_items_changed": [],
  "test_evidence": {},
  "approver": "jtoye-uat@sigantry",
  "audit_hash": "<sha256 hex digest>",
  "created_at": "2026-04-NNTHH:MM:SS.SSSZ"
}
```

### 5.2 Verify the audit hash

```bash
python -c "
import json
from sigantry_core.release.record import DeployRecord
last = open('$HOME/.sigantry/audit/deploys.jsonl').read().splitlines()[-1]
r = DeployRecord(**json.loads(last))
print('audit_hash matches:', r.verify_hash())
"
```

The `verify_hash()` method recomputes the SHA-256 over the canonical JSON
of every other field and compares against the stored value. A `True`
result is the cryptographic guarantee that the audit-log line has not
been tampered with after-the-fact.

### 5.3 Export the audit log to JToye's evidence archive

```bash
EVIDENCE_DIR=<jtoye-evidence-archive>/phase-16-seam-06
mkdir -p "$EVIDENCE_DIR"
cp ~/.sigantry/audit/deploys.jsonl "$EVIDENCE_DIR/jtoye-deploys.jsonl"
```

### 5.4 Record the result block in `16-HUMAN-UAT.md` Test 3

JToye operator opens
`.planning/phases/16-seam-expansion-jtoye-trial/16-HUMAN-UAT.md`,
locates the `### 3. Live JToye Deploy + Release Record + Audit-Log Export +
Sign-off` test, and records:

- Date (UTC)
- JToye operator name
- Workspace ID
- Release ID (from the audit-log line)
- pytest output (last 10 lines)
- Audit log path (the evidence-archive copy)
- Audit hash verification result
- JToye operator signature

---

## 6. Sign-off Cadence + Escalation (V3-RISK-2)

### 6.1 Cadence

JToye sign-off SHALL happen within **30 days** of the Phase 16 commit
(`feat(16-05): ...` on master). The cadence is the V3-RISK-2 budget --
JToye Digital engineering availability is the gating constraint.

### 6.2 Escalation contact

If the 30-day budget slips:

1. The Sigantry upstream maintainer notes the slip in the Phase 16 SUMMARY +
   updates the v3.0 milestone status.
2. The JToye engineering lead is the escalation contact.
3. Sign-off is appended to `16-HUMAN-UAT.md` when JToye is ready;
   no Phase 16 commit unwind is required (the scaffolding is complete +
   tag-ready).

### 6.3 Phase 16 closure transition

Sign-off triggers the status transition:

| Before sign-off | After sign-off |
|-----------------|----------------|
| `Complete (live-UAT pending V3-RISK-2 + V3-RISK-3)` | `Complete (operator-signed)` |

The Phase 16 entry in `.planning/ROADMAP.md` updates accordingly. The
v3.0 milestone closes when this sign-off lands.

---

*Runbook authored: 2026-04-28*
*Phase 16 / SEAM-06 closure runbook -- live JToye sign-off operator-bound per V3-RISK-2*
