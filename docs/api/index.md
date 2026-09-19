# API Reference

Auto-generated API reference for the `sigantry_core` Python
package. Each subpackage below is rendered via
[mkdocstrings](https://mkdocstrings.github.io/python/) directly from
source docstrings - the single source of truth for the public Python
surface.

For the CLI surface, every subapp mirrors the Python API one-to-one. Run
`sigantry-core <subapp> --help` for inline usage.

## Subpackages

| Subpackage | Purpose | Page |
|---|---|---|
| `sigantry_core.auth` | DefaultAzureCredential chain, token provider, diagnose-auth | [auth](auth.md) |
| `sigantry_core.client` | Single shared HTTP client (the only module that may import httpx) | [client](client.md) |
| `sigantry_core.workspace` | Workspace CRUD + items + capacity assignment | [workspace](workspace.md) |
| `sigantry_core.capacity` | Capacity inspection + pause/resume lifecycle (ARM) | [capacity](capacity.md) |
| `sigantry_core.governance` | RBAC audit, label sync, tenant-settings export | [governance](governance.md) |
| `sigantry_core.deploy` | fabric-cicd wrapper + deploy orchestrator (plugin profiles register separately) | [deploy](deploy.md) |
| `sigantry_core.monitor` | Telemetry dispatcher + event envelope | [monitor](monitor.md) |
| `sigantry_core.dq` | DataQualityGate dispatcher (plugin gates register separately) | [dq](dq.md) |

## Conventions

- **Docstring style:** Google (Args: / Returns: / Raises:).
- **Stability:** Every symbol exported from a subpackage `__init__.py`
  is part of the public surface and is semver-protected.
  Implementation-detail symbols are documented but not guaranteed
  stable across minor releases.
- **HTTP:** No module outside `sigantry_core.client` imports
  `httpx` directly. Ruff `TID251` enforces the invariant project-wide.
- **Destructive operations:** any function that mutates remote state
  requires `force=True` and writes an audit entry.

See `CONTRIBUTING.md` at the repo root for docstring requirements.
