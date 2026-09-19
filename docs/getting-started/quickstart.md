# Quickstart

End-to-end walkthrough from `pip install` to a first successful
`FabricDataOps.emit()` through the in-memory telemetry double. Takes
under five minutes in a clean Python 3.11 venv.

## 1. Install the base

```bash
python -m venv .venv && source .venv/bin/activate
pip install sigantry-core
```

(`pip install sigantry-core` assumes the PyPI release, which is currently
held pending operator UAT closure — substitute the distributed wheel path
per [install.md](install.md) until then.)

## 2. Wire a toy deploy profile

The base ships `sigantry_core.testing.doubles` with in-memory
implementations of each seam so you can exercise the composition root
without any cloud auth.

```python
from sigantry_core import FabricDataOps
from sigantry_core.testing.doubles import (
    InMemoryTelemetrySink,
    FakeDeployProfile,
    NoopGate,
)
from sigantry_core.protocols import DeployContext

fdo = FabricDataOps(
    telemetry=InMemoryTelemetrySink(),
    deploy_profile=FakeDeployProfile(),
    dq_gate=NoopGate(),
)

fdo.emit("hello", {"note": "quickstart"})
assert fdo.telemetry.events[0].name == "hello"

result = fdo.deploy(
    DeployContext(workspace_id="ws-demo", environment="dev")
)
print(result.items_published, result.items_failed)
```

## 3. Switch to a real plugin

Install a plugin package, then wire via `.fabric-dataops.toml`:

```bash
pip install <your-plugin-package>
```

```toml
[core]
tenant_id = "<your-tenant-id>"

[deploy]
profile = "<plugin-profile-name>"
```

```python
from sigantry_core import FabricDataOps
fdo = FabricDataOps.from_config(".fabric-dataops.toml")
```

`from_config` discovers installed plugins via their entry points and
resolves the named profile / gate / sink / auth / runbooks / capacity
implementations for you.

## Next steps

- Read the [protocol seams reference](../reference/protocols.md) to
  understand the contracts each plugin class must satisfy.
- Browse the [API reference](../api/index.md) for the base module
  surface.
