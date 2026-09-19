# OPA Approval Gate Quickstart

The OSS-pure approval gate. If you are not using ADO Environments or
GitHub Environments, [Open Policy Agent](https://www.openpolicyagent.org/)
gives you a vendor-neutral way to express approval policies in Rego and
have Sigantry observe the decisions during `sigantry deploy`.

This runbook shows you how to:

1. Start an OPA daemon in dev mode
2. Wire `OpaApprovalGate` into a `sigantry deploy` invocation
3. Adapt the default Rego policy for your environments
4. Harden the deployment for production (mTLS, GitOps for Rego, audit-log
   integration)

## Prerequisites

- OPA binary `>= 0.60.0` -- `brew install opa`, or Docker:

  ```bash
  docker run --rm -p 8181:8181 openpolicyagent/opa run --server
  ```

- A directory for your Rego policies; we use `policies/sigantry/` below
- Sigantry installed in your active environment
  (`pip install sigantry-core` or your preferred method)

## Step 1 -- start the OPA daemon (dev mode)

Save the [default policy](#default-policy) as
`policies/sigantry/approval.rego`, then start OPA:

```bash
opa run --server policies/
```

This brings up the HTTP API on `http://localhost:8181`. Verify it is
healthy:

```bash
curl -s http://localhost:8181/health
# {} on success
```

## Step 2 -- test the policy with a sample request

```bash
curl -s -X POST http://localhost:8181/v1/data/sigantry/approval/allow \
  -H 'Content-Type: application/json' \
  -d '{"input":{"release_id":"r1","env":"prod","approvers":["alice","bob"]}}'
# {"result": true}
```

Two approvers on prod -> allow. Try with one approver:

```bash
curl -s -X POST http://localhost:8181/v1/data/sigantry/approval/allow \
  -d '{"input":{"release_id":"r1","env":"prod","approvers":["alice"]}}'
# {"result": false}
```

One approver on prod -> deny.

## Step 3 -- wire `OpaApprovalGate` into your deploy

In your deploy harness:

```python
from sigantry_core.approval_gates import OpaApprovalGate
from sigantry_core.protocols import ApprovalContext

gate = OpaApprovalGate(
    opa_url="http://localhost:8181",          # default; override for prod
    policy_path="sigantry/approval/allow",    # default; matches policy above
)

ctx = ApprovalContext(
    release_id="rel-2026-04-28-001",
    env="prod",
    approvers=["alice@corp.com", "bob@corp.com"],
)
request = gate.request(ctx)
outcome = gate.wait(request)
# outcome is "approved" / "rejected" / "timeout"
```

The `wait()` call writes an `ApprovalRecord` to
`~/.sigantry/audit/approvals.jsonl` on every decision. You can inspect
or ship that file to your central audit pipeline.

## Default policy

Save as `policies/sigantry/approval.rego`:

```rego
package sigantry.approval

import future.keywords.if
import future.keywords.in

default allow := false

allow if {
    input.env == "dev"  # Auto-approve dev deploys
}

allow if {
    input.env in {"preprod", "prod"}
    count(input.approvers) >= 2  # Require 2+ approvers for non-dev
}
```

Adapt the rules to your team's policy:

- Add named approvers: `input.approvers[_] in {"alice@corp.com", "bob@corp.com"}`
- Time-window constraints: `time.weekday(time.now_ns()) in {"Tuesday", "Wednesday"}`
- Workspace constraints: `input.workspace_id == "prod-workspace-id"`
- Pull from `data.*` for centrally-managed approver groups

The Rego language reference: <https://www.openpolicyagent.org/docs/latest/policy-language/>

## Production hardening

The dev quickstart above runs OPA on `localhost:8181` with no
authentication. For production:

### mTLS

Front OPA with TLS client-cert auth so only authorised callers can
request decisions. OPA supports mTLS natively:

```bash
opa run --server \
  --tls-cert-file=server.crt \
  --tls-private-key-file=server.key \
  --tls-ca-cert-file=ca.crt \
  --authentication=tls
```

Pass the certs to your `OpaApprovalGate`:

```python
import httpx
from sigantry_core.approval_gates import OpaApprovalGate

http_client = httpx.Client(
    cert=("client.crt", "client.key"),
    verify="ca.crt",
)
gate = OpaApprovalGate(
    opa_url="https://opa.internal.corp:8181",
    http_client=http_client,
)
```

### Authenticating reverse proxy

If your platform team prefers to terminate auth at a reverse proxy
(Envoy, nginx, Azure API Management) supply a `TokenProvider` to
`OpaApprovalGate` and let the proxy front the daemon:

```python
gate = OpaApprovalGate(
    opa_url="https://opa-proxy.internal.corp",
    token_provider=my_proxy_token_provider,
)
```

### Policy versioning + GitOps

Rego policies are configuration-as-code. Track them in a Git repo and
push to OPA via [bundle delivery](https://www.openpolicyagent.org/docs/latest/management-bundles/):

```yaml
# OPA config.yaml
services:
  control:
    url: https://policy-bundle-server.internal.corp
bundles:
  sigantry-policies:
    service: control
    resource: bundles/sigantry-policies.tar.gz
    polling:
      min_delay_seconds: 60
      max_delay_seconds: 120
```

The bundle server can be a simple S3/Azure Blob or a dedicated bundle
service. Versioning policies in Git also gives you the audit trail:
`who proposed what policy change, when, and through which PR`.

### Audit-log integration

`OpaApprovalGate.wait()` writes an `ApprovalRecord` to
`~/.sigantry/audit/approvals.jsonl` every time it gets a decision. The
record carries:

- `request_id` (uuid4 generated by `OpaApprovalGate.request()`)
- `release_id`, `env`, `approvers`
- `outcome` (`"approved"` / `"rejected"`)
- `decided_by = "opa-policy"` (OPA does not surface the human identity
  of the policy author -- enforce that linkage in your Git workflow)
- `audit_hash` (SHA-256 over canonical JSON of the metadata)

Forward `approvals.jsonl` to your SIEM the same way you forward
`deploys.jsonl` and `secret_changes.jsonl` -- they share the audit-plane
shape.

## Troubleshooting

- **`{"result": false}` on a request that should pass**: OPA's default
  for an undefined Rego expression is `false`. Check `opa eval` output
  with `--explain=full` to see which rule fired.
- **`{}` (empty result) on a request**: Sigantry's
  `OpaApprovalGate.wait()` defensively maps a missing `result` field to
  `rejected` (RESEARCH §Assumption A2). If you see this in
  `approvals.jsonl`, your policy did not return a value for the documented
  decision path -- likely a typo in `package sigantry.approval` or in the
  rule head.
- **`401 Unauthorized` from the gate**: only relevant in production with
  mTLS or a reverse proxy. Verify cert paths and proxy auth headers.
- **`Connection refused`**: OPA daemon not running. Start it with
  `opa run --server policies/`.

## See also

- [Sigantry protocol seams reference](../../reference/protocols.md) -- the
  `ApprovalGate` Protocol surface (v3.0 Phase 16) is part of the public
  plugin contract.
- `sigantry_core.approval_gates.opa_hook` -- the module docstring carries
  the full constructor signature and behaviour notes.
- OPA HTTP API: <https://www.openpolicyagent.org/docs/latest/rest-api/>
- Rego language: <https://www.openpolicyagent.org/docs/latest/policy-language/>
