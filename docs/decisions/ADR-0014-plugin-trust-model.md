# ADR-0014 -- plugin trust model + ``SIGANTRY_TRUSTED_PLUGIN_DISTS``

- **Status:** Accepted
- **Date:** 2026-05-08
- **Milestone:** v3.0.x (Audit-2026-05-07 Wave 3 / W3.4)
- **Deciders:** platform team (audit synthesis: security dimension finding S-04, "Plugin trust model is implicit -- any installed dist that declares an entry-point participates in resolution")
- **Context:** The v3.0 plugin model uses Python entry points
  (``[project.entry-points."sigantry.<seam>"]`` tables in plugin
  ``pyproject.toml`` files) to surface third-party implementations of
  the toolkit's eleven Protocol seams. ``Registry.discover()`` walks
  ``importlib.metadata.entry_points()`` and any plugin in the active
  Python environment is eligible to handle a seam invocation. That is
  the design intent (the v3.0 productisation explicitly targets a
  multi-tenant plugin universe -- ``sigantry-hs2`` lands alongside
  ``sigantry-jtoye`` etc.) but it also means that an operator who
  inadvertently installs a malicious wheel matching one of the
  registered group names gives that wheel runtime privileges
  matching the legitimate plugin's. The audit synthesis flagged
  this as a documented blast radius without an explicit acceptance
  artifact.

## Decision

The trust model is **opt-in tightening**, not built-in restriction:

> The registry stays permissive. The doctor surfaces every plugin's
> trust status. CI gates on the trust list when the operator
> explicitly enables ``--strict-trust``.

### Mechanism

1. **Env-var allowlist.** ``SIGANTRY_TRUSTED_PLUGIN_DISTS`` carries a
   comma-separated list of distribution names operators consider
   trusted. Whitespace is stripped per entry; empty entries are
   ignored. Examples:

   ```
   export SIGANTRY_TRUSTED_PLUGIN_DISTS="sigantry-hs2,sigantry-jtoye"
   ```

2. **Doctor classification.** ``sigantry doctor`` adds a ``Trust``
   column to its plugin table. Each row reports one of three states:

   | State        | Condition                                                                            |
   |--------------|--------------------------------------------------------------------------------------|
   | ``trusted``  | Allowlist is non-empty AND the plugin's distribution name is in the allowlist.       |
   | ``untrusted``| Allowlist is non-empty AND the plugin's distribution name is NOT in the allowlist.   |
   | ``unknown``  | Allowlist is empty (no list configured) OR the plugin has no resolvable dist name.   |

3. **Strict-mode CI gate.** ``sigantry doctor --strict-trust`` exits
   non-zero when any plugin reports ``untrusted``. The flag is
   independent of ``--strict`` (which gates on import errors); CI
   jobs that pin a known plugin universe should pass both flags.

4. **No automatic rejection at runtime.** The registry does NOT refuse
   to resolve an untrusted plugin. The trust list is an **operator
   visibility** mechanism, not a runtime sandbox. Pin the universe
   via a wheel-hash-pinned ``requirements-lock.txt`` (W3.3) or by
   running ``--strict-trust`` in CI; the trust list is the single
   point of acceptance for what counts as "expected".

### Operator workflow

1. Set ``SIGANTRY_TRUSTED_PLUGIN_DISTS`` to your known-good plugin
   set (e.g. ``"sigantry-hs2,sigantry-jtoye"``).
2. Add ``sigantry doctor --strict-trust`` to your CI smoke step.
3. When a new plugin is intentionally onboarded, add its dist name
   to the env var (review-gated change to your CI config).
4. When an unexpected ``untrusted`` row appears, investigate before
   widening the list -- that is the failure mode the trust list is
   designed to surface.

## Alternatives considered

| Option                                                                                  | Why rejected |
|-----------------------------------------------------------------------------------------|--------------|
| **A. Reject at registry resolution** -- the registry refuses to dispatch to untrusted plugins. | Conflicts with the "permissive by design" v3.0 plugin model. A fresh-laptop install with no env var would refuse to resolve a legitimate plugin -- worse UX than the current state. |
| **B. Hash-pin plugin wheels and lock the universe by checksum.** | Already in W3.3 territory for the toolkit's own dev dependencies. Plugin wheels live outside ``requirements-lock.txt`` (consumers install them ad-hoc). Hash-pinning plugins requires either a per-consumer lockfile (operator burden) or a curated index (infrastructure burden). Out of scope for v3.0. |
| **C. Code-signing on plugin wheels.** | The Python package ecosystem does not have a uniformly-supported wheel-signature standard yet. Sigstore is promising but not universal. Premature. |
| **D. Trust list (chosen).** | Single env var, single doctor flag, zero impact on the existing plugin model. The blast radius doesn't shrink, but the operator now has a visibility surface. Net cost: one env var to set + one CLI flag to add to CI. Net benefit: surprise plugin installs surface immediately. |

## Rationale

1. **The blast radius is documented and accepted.** The v3.0 plugin
   model explicitly targets multi-tenant plugin universes. Restricting
   resolution to a static allowlist would defeat the design.
2. **Visibility is the cheapest defense.** Most "unexpected plugin"
   failure modes are accidents (a developer pip-installed something on
   their laptop and forgot). Surfacing the deviation in a CLI command
   that operators run regularly catches them early.
3. **CI gating is opt-in.** Operators who don't run
   ``--strict-trust`` see the same UX they had pre-W3.4. Operators
   who do run it gate on a single env-var-driven policy that travels
   with their CI config.
4. **Distribution name, not module name.** Distributions can
   contribute multiple modules. The allowlist matches the unit
   operators install (``pip install sigantry-hs2``), not the unit
   the registry imports (``sigantry_hs2.deploy.aims_profile``). This
   keeps the env-var list short and human-readable.

## Consequences

- ``sigantry_core/doctor.py`` adds a ``Trust`` column + ``--strict-trust``
  flag. The default ``sigantry doctor`` invocation continues to exit
  zero so existing scripts are unaffected.
- ``CLAUDE.md`` documents the env var alongside the other operator-
  facing knobs. Operator-facing docs (``docs/USER-GUIDE.md``,
  ``CONSUMING.md``) reference this ADR for the full model.
- Falsifiability tests in
  ``tests/sigantry_core/test_doctor_trust.py`` lock the four
  classification rules + the ``--strict-trust`` exit code + the env-
  var parse semantics.
- This ADR does not bump the api_version of any plugin Protocol seam
  (no Protocol method added or changed).
