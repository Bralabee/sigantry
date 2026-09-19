# ADR-0004 — Protocol API version policy (plugin contract evolution)

- **Status:** Accepted
- **Date:** 2026-04-21
- **Milestone:** v2.0 (decision made now; implementation binds at v2.1)
- **Deciders:** platform team
- **Context:** 08.1 gap-closure review flagged that `api_version` was
  deferred as YAGNI during Phase 8, and retrofitting protocol versioning
  once third-party plugins ship is painful.

## Decision

Each of the six seam protocols will gain an optional `api_version: str`
class variable starting at **v2.1.0**. Plugins may declare a SemVer
compatibility range; the base dispatcher treats a missing or empty value
as the legacy-v2.0 contract and will continue to accept it for the life
of v2.x.

```python
@runtime_checkable
class TelemetrySink(Protocol):
    name: str
    api_version: str = ""  # default = v2.0 contract (Phase 8)
    def emit(self, event: TelemetryEvent) -> None: ...
    def flush(self, timeout_s: float = 5.0) -> None: ...
```

At resolve time, the registry will call a lightweight
`_check_api_compat(plugin)` helper that:

1. Reads `plugin.api_version` (default `""` = v2.0).
2. Compares against the seam's declared **current** version
   (stored on the Protocol class or in a sidecar constant).
3. **Logs a warning** when a plugin targets an older-but-supported
   version (soft deprecation path).
4. **Raises `IncompatiblePluginError`** when a plugin targets a
   version that is NOT in the base's supported range.

## Rationale

- v2.0 shipped without version tags. Any policy must accept them as
  legacy; otherwise every Phase-8-era plugin breaks at v2.1 upgrade.
- Deferring past v2.1 means any third-party plugin that ships
  post-v2.0 needs to be retrofitted later, which is worse than adding
  the (optional) field now.
- `str` values are SemVer. Range compatibility follows PEP 440.

## Alternatives considered

1. **Keep deferring to v3.** Rejected: if the first third-party plugin
   publishes during v2.x, the base can't detect contract drift.
2. **Hard-fail on missing `api_version`**. Rejected: breaks every
   plugin written against v2.0's Protocol shape.
3. **Structural (typing-only) versioning.** Rejected: `runtime_checkable`
   `Protocol` does not distinguish between v2.0 shape and a
   v2.5 shape that adds an optional kwarg, so pure structural
   checks under-detect drift.

## Implementation plan (for v2.1)

1. Add optional `api_version: str` class var to each of the six
   Protocols. Default `""`.
2. Ship a `sigantry_core.compatibility` module with:
   - Current supported-version map per seam.
   - `check_plugin_compat(impl: type, group: str) -> None`.
   - Registered pytest fixture `fdt_api_compat_check(plugin_cls)` for
     plugin authors to assert their declared version matches the seam
     they target.
3. Call the compatibility check in `Registry.register` and in
   `_resolve_optional` after instantiation.
4. Contract-test suite is extended to run compatibility checks
   against doubles and HS2 plugins for each version tier.
5. `doctor` CLI surfaces `api_version` in a new column; plugins
   without an explicit version are shown as `v2.0 (legacy)`.

## Consequences

- **Pro:** Drift between base and plugin is detectable at install time,
  not at call time. A `doctor` run flags incompatibility before a
  deploy attempt.
- **Pro:** Consumers can pin plugin versions in `.fabric-dataops.toml`
  if they want stricter policy.
- **Pro:** Defers hard-break decisions: v2.1 can carry old and new
  plugins simultaneously.
- **Con:** Extra optional field on every Protocol. Minor cognitive
  load for plugin authors.
- **Con:** Maintaining the supported-version matrix is an ongoing
  cost — base maintainers must bump it every time a Protocol changes.
  This is the right place to pay that cost.

## Not covered by this ADR

- Plugin signing / trust boundary policy (`PRODUCTIZATION.md §10`
  decision deferred until a third party publishes).
- Semantic diffing of plugin shapes (considered overkill).

## References

- `PRODUCTIZATION.md §10` — YAGNI cut that this ADR reopens.
- `sigantry_core/protocols.py` — current Protocol definitions.
- `sigantry_core/registry.py` — `register` + `resolve` hooks
  where the compatibility check will live.
