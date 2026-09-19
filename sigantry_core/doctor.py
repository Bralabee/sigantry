"""``sigantry-core doctor`` CLI subcommand (PROD-18).

Minimum-viable doctor per PRODUCTIZATION.md Section 10.2 row 1. Lists
every plugin discovered under the v3.0 ``sigantry_core.*`` entry-point
groups (``Registry.known_new_groups()``), with one row per plugin
carrying:

- ``Group``   - short name for the seam group (last dotted segment).
- ``Name``    - the entry-point name the plugin registered under.
- ``Module``  - module path the entry point loaded from.
- ``Version`` - distribution version (``importlib.metadata.version``)
  or blank.
- ``Trust``   - ``trusted`` / ``untrusted`` / ``unknown`` (Audit-2026-05-07
  W3.4) per the ``SIGANTRY_TRUSTED_PLUGIN_DISTS`` env-var allowlist.
- ``Status``  - ``ok`` on successful import, else a short error string.

Exit-code semantics:

- Default: 0 always.
- ``--strict``: 1 when any plugin failed to import.
- ``--strict-trust`` (W3.4): 1 when any plugin is not in the trusted
  set. Combine with ``--strict`` to gate on both conditions; CI jobs
  that pin a known plugin universe should set both.

Trust-model background (ADR-0014, Audit-2026-05-07 W3.4):

The plugin registry is permissive by design -- any installed
distribution that declares a ``[project.entry-points."sigantry.<seam>"]``
table participates in resolution. That blast radius is documented and
deliberate (the v3.0 plugin model expects multi-tenant deployments
where multiple plugin distributions land in the same Python env).
The trust list is the OPT-IN tightening: an operator who wants to
lock down their
deploys to a known-good set sets the env var and adds ``--strict-trust``
to their CI gate.

Deferred to v2.1 (PRODUCTIZATION.md Section 10.2): JSON / machine-
readable output, plugin capability querying, per-seam filtering flags.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Literal

import typer
from rich.console import Console
from rich.table import Table

from sigantry_core.registry import PluginInfo, Registry, default_registry

doctor_app = typer.Typer(
    help="List discovered plugins and diagnose entry-point import failures.",
    no_args_is_help=False,
    add_completion=False,
)

# Test hook: setting this to a Registry instance redirects ``doctor_cmd`` away
# from :func:`default_registry` so unit tests can inject fixtures without
# mutating the module-level singleton. Production callers never touch this.
_REGISTRY_OVERRIDE: Registry | None = None

# Audit-2026-05-07 W3.4: env var carrying the comma-separated allowlist
# of trusted distribution names. Operators set this in their CI env so
# the doctor's Trust column + ``--strict-trust`` flag report against
# their known-good set. Empty / unset = "no trust list configured"
# (every plugin reports as ``unknown`` in that case so the absence of
# a list is visible to the operator).
_TRUSTED_DISTS_ENV: str = "SIGANTRY_TRUSTED_PLUGIN_DISTS"

TrustStatus = Literal["trusted", "untrusted", "unknown"]


def _set_registry_override(registry: Registry | None) -> None:
    """Test-only: install or clear the registry override used by ``doctor_cmd``."""
    global _REGISTRY_OVERRIDE
    _REGISTRY_OVERRIDE = registry


def _short_group(group: str) -> str:
    """Return the last segment of a ``sigantry_core.<seam>`` group."""
    return group.rsplit(".", 1)[-1]


def _distribution_for_module(module: str | None) -> str | None:
    """Best-effort distribution name -> version lookup for a module path.

    ``PluginInfo.version`` is populated during registry discovery when an
    entry-point carries ``ep.dist.version``; this helper covers the fallback
    case where we only know the top-level module name and need to look up
    the owning distribution.
    """
    if not module:
        return None
    top = module.split(".", 1)[0]
    # Try the module path itself first, then the top-level package name.
    for candidate in (module, top):
        try:
            return _pkg_version(candidate)
        except PackageNotFoundError:
            continue
        except Exception:  # pragma: no cover - defensive
            continue
    return None


def _canonicalise_dist_name(name: str) -> str:
    """PyPI canonical form: lowercase, ``_`` -> ``-``.

    Audit-2026-05-08 review follow-up (WR-04): PyPI distribution names
    are case-insensitive AND tolerate ``-`` <-> ``_`` swaps (PEP 503).
    Operators who declare ``my_plugin`` in
    ``SIGANTRY_TRUSTED_PLUGIN_DISTS`` would previously see the doctor
    report ``untrusted`` when ``_dist_name_for_plugin`` resolved
    ``my-plugin`` first (or vice-versa) -- the trust comparison was
    string-equality on the un-canonicalised form. Canonicalising
    BOTH the trusted set AND the resolved dist name before the ``in``
    check closes the gap; documented as the canonical form in
    ADR-0014.
    """
    return name.strip().lower().replace("_", "-")


def _read_trusted_dists(env: dict[str, str] | None = None) -> frozenset[str]:
    """Return the set of trusted distribution names from the env var.

    The env var is comma-separated; whitespace + empty entries are
    stripped. Returns an empty frozenset when the env var is unset or
    empty -- the caller distinguishes "no trust list configured" from
    "trust list explicitly empty".

    Audit-2026-05-08 review follow-up (WR-04): values are
    canonicalised to PyPI form (lowercase, ``_`` -> ``-``) so an
    operator who declares ``my_plugin`` matches a dist published as
    ``my-plugin`` (and vice-versa).

    Tests pass ``env={...}`` to override; production reads ``os.environ``.
    """
    source = env if env is not None else os.environ
    raw = source.get(_TRUSTED_DISTS_ENV, "")
    return frozenset(_canonicalise_dist_name(part) for part in raw.split(",") if part.strip())


def _resolve_trust_status(
    dist_name: str | None,
    trusted_dists: frozenset[str],
) -> TrustStatus:
    """Classify a plugin's distribution as trusted / untrusted / unknown.

    Rules:

    - ``trusted_dists`` is empty (no allowlist configured) -> ``unknown``.
    - ``dist_name`` is None (could not resolve a distribution for this
      plugin -- defensive case for in-tree fixtures) -> ``unknown``.
    - ``dist_name`` (PyPI-canonicalised) is in ``trusted_dists`` -> ``trusted``.
    - Otherwise -> ``untrusted``.

    Audit-2026-05-08 review follow-up (WR-04): both sides of the ``in``
    comparison are now in PyPI canonical form (lowercase, ``_`` ->
    ``-``); the trust list and the resolved dist name no longer drift
    on hyphen/underscore variants.
    """
    if not trusted_dists:
        return "unknown"
    if dist_name is None:
        return "unknown"
    return "trusted" if _canonicalise_dist_name(dist_name) in trusted_dists else "untrusted"


def _dist_name_for_plugin(p: PluginInfo) -> str | None:
    """Return the distribution name a plugin came from, or ``None``.

    ``PluginInfo`` records the module path (e.g.
    ``my_plugin.deploy.profile``); the distribution name is the
    underscore->hyphen-tolerant top-level package (e.g. ``my-plugin``).
    Falls back to ``None`` when the module is in-tree / not packaged.
    """
    if not p.module:
        return None
    top = p.module.split(".", 1)[0]
    # Try both the dotted module path and the top-level segment, plus stripped _core for sigantry.
    candidates = [p.module, top, top.replace("_", "-")]
    if top.endswith("_core"):
        candidates.append(top[:-5])
        candidates.append(top[:-5].replace("_", "-"))
    for candidate in candidates:
        try:
            _pkg_version(candidate)
            return candidate
        except PackageNotFoundError:
            continue
    return None


def _build_table(
    plugins: list[PluginInfo],
    trusted_dists: frozenset[str],
) -> tuple[Table, bool, bool]:
    """Render ``plugins`` into a rich ``Table``.

    Returns ``(table, had_import_errors, had_untrusted)``. The two
    boolean flags drive ``--strict`` and ``--strict-trust`` exit codes
    respectively.
    """
    table = Table(title="sigantry-core plugins", title_style="bold")
    table.add_column("Group", style="cyan", no_wrap=True)
    table.add_column("Name", style="magenta", no_wrap=True)
    table.add_column("Module", style="white")
    table.add_column("Version", style="green")
    table.add_column("Trust")
    table.add_column("Status")

    had_errors = False
    had_untrusted = False
    # Deterministic ordering so the doctor output is stable across invocations.
    ordered = sorted(plugins, key=lambda p: (p.group, p.name))
    for p in ordered:
        if p.import_error:
            had_errors = True
            msg = p.import_error
            if len(msg) > 80:
                msg = msg[:77] + "..."
            status = f"[red]error: {msg}[/red]"
        else:
            status = "[green]ok[/green]"
        version = p.version or _distribution_for_module(p.module) or ""
        dist = _dist_name_for_plugin(p)
        trust_status = _resolve_trust_status(dist, trusted_dists)
        if trust_status == "trusted":
            trust_cell = "[green]trusted[/green]"
        elif trust_status == "untrusted":
            had_untrusted = True
            trust_cell = "[red]untrusted[/red]"
        else:
            trust_cell = "[yellow]unknown[/yellow]"
        table.add_row(
            _short_group(p.group),
            p.name,
            p.module or "",
            version,
            trust_cell,
            status,
        )
    return table, had_errors, had_untrusted


@doctor_app.callback(invoke_without_command=True)
def doctor_cmd(
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit non-zero if any plugin failed to import.",
    ),
    strict_trust: bool = typer.Option(
        False,
        "--strict-trust",
        help=(
            "Exit non-zero if any discovered plugin is not in "
            "$SIGANTRY_TRUSTED_PLUGIN_DISTS (Audit-2026-05-07 W3.4 / "
            "ADR-0014). Combine with --strict to gate on both."
        ),
    ),
) -> None:
    """Report discovered plugins across the v3.0 ``sigantry_core.*`` seams.

    The seam-group count comes from :meth:`Registry.known_new_groups` (the
    Phase 11+ entry-point surface); legacy v2 group names are still
    discovered under the hood via ``_LEGACY_GROUPS`` but do not contribute
    to the operator-facing summary line.

    Tests redirect the registry via :func:`_set_registry_override`; production
    callers leave the override unset so :func:`default_registry` is used.

    Audit-2026-05-07 W3.4 trust-list integration: the ``Trust`` column
    classifies each plugin against the
    ``SIGANTRY_TRUSTED_PLUGIN_DISTS`` env var. ``--strict-trust``
    surfaces an untrusted plugin as a non-zero exit code; CI jobs
    that pin a plugin universe should set both ``--strict`` and
    ``--strict-trust``.
    """
    registry: Registry = (
        _REGISTRY_OVERRIDE if _REGISTRY_OVERRIDE is not None else default_registry()
    )
    registry.discover()
    plugins = registry.list_plugins()
    trusted_dists = _read_trusted_dists()

    console = Console()
    table, had_errors, had_untrusted = _build_table(plugins, trusted_dists)
    console.print(table)
    console.print(
        f"\n{len(plugins)} plugin(s) discovered across "
        f"{len(Registry.known_new_groups())} seam group(s)."
    )
    if not trusted_dists:
        console.print(
            "[yellow]Trust list:[/yellow] not configured -- set "
            f"${_TRUSTED_DISTS_ENV} (comma-separated) to enable the "
            "trust column. ADR-0014 documents the model."
        )
    else:
        console.print(
            f"[green]Trust list:[/green] {len(trusted_dists)} "
            f"distribution(s) allowlisted via ${_TRUSTED_DISTS_ENV}."
        )

    if strict and had_errors:
        raise typer.Exit(code=1)
    if strict_trust and had_untrusted:
        raise typer.Exit(code=1)


__all__ = ["doctor_app", "doctor_cmd"]


# ``_set_registry_override`` is intentionally omitted from ``__all__`` because
# it is a private test hook; public callers should not rely on it.
