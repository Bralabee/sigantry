# Thread-safety contract

`sigantry` has **per-component** thread-safety. This doc
spells out which surfaces are safe to share across threads, which are
not, and how plugin authors should reason about it.

## Base primitives

| Surface | Thread-safe? | Notes |
|---------|--------------|-------|
| `Registry` | **Yes** (discovery + register/resolve) | `threading.Lock` around discovery state; `register` / `resolve` read from dicts after the fact, which is GIL-safe for reads. |
| `default_registry()` singleton | **Yes** | `_default_lock: threading.Lock` guards first-time construction + the `discover()` call that follows. |
| `TokenProvider` / token cache | **Yes** | Documented as thread-safe in `sigantry_core/auth/token_provider.py`; `threading.Lock` around cache state. |
| `RateLimit` buckets | **Yes** | `threading.RLock` in `client/rate_limit.py`. |
| `FabricRestClient` and subclasses (ARM, PowerBI, Purview) | **Per-request safe** | `httpx.Client` is thread-safe for concurrent requests. Instances may be shared. |

## `FabricDataOps`

`FabricDataOps` itself is **not** internally synchronised. Sharing a
single instance across threads is safe **if and only if** every seam
plugin the instance holds is thread-safe.

### When it is safe to share one `FabricDataOps` across threads

- Every injected seam plugin is thread-safe on its own terms.
- No thread calls `.close()` while another is mid-operation (calling
  `.close()` is exclusive).
- The consumer does not mutate `fdo.auth`, `fdo.telemetry`, etc.
  after construction.

### When it is NOT safe

- A plugin holds mutable per-call state without locking.
- A plugin is built on a single non-thread-safe SDK (e.g. certain
  `msgraph-core` clients predating v1.x).
- The consumer mutates seam attributes on the fly.

### Recommended pattern

For concurrent work, build a `FabricDataOps` per worker:

```python
def worker(job):
    with FabricDataOps.from_config(".sigantry.toml") as fdo:
        fdo.deploy(job.ctx)
```

`default_registry()` is thread-safe, so parallel `from_config` calls
don't race.

## Plugin author checklist

If your plugin will be shared across threads, document it and enforce:

- All mutable state is behind a `threading.Lock` (or the plugin is
  stateless).
- `close()` is idempotent and safe to call concurrently with in-flight
  `emit` / `run` / `plan` calls *or* the consumer promises to quiesce
  the plugin before `close`.
- Any Azure SDK you wrap either documents thread-safety or you
  construct one client per call.

If your plugin is **not** thread-safe, state so in its module docstring
and the entry-point description.

## Built-in plugin status

| Plugin | Thread-safe? | Evidence |
|--------|--------------|----------|
| `InMemoryTelemetrySink` (double) | No (append to list) | Test-only; rebuild per test. |
| `NoopGate` (double) | Yes | Stateless. |
| `FakeAuth` (double) | Yes | Returns an immutable `Secret`. |
| `StaticRunbookRegistry` (double) | Yes | Read-only dict after init. |
| `NoopCapacityPolicy` (double) | Yes | Stateless. |

The base package registers no deploy profile, DQ gate, telemetry sink,
auth provider, runbook registry or capacity policy. A plugin that
supplies one should document its own thread-safety (see the checklist above).

## Related

- [observation-planes.md](observation-planes.md)
