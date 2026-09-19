"""Audit-2026-05-07 W2.2 — falsifiability tests for the canonical seam
resolver in :mod:`sigantry_core._dispatch`.

These tests pin the contract behind the four dispatchers that now share
:func:`sigantry_core._dispatch.resolve_seam`:
``deploy.orchestrator.deploy``, ``dq.dispatcher.run_gate``,
``monitor.dispatcher.resolve_sink``, and the ``FabricDataOps.from_config``
glue path. The same algorithm runs in every place; this test file
locks the algorithm itself.

Pre-fix branch (the inline-probe tree) cannot satisfy these tests --
``test_resolve_seam_is_used_by_every_dispatcher`` would fail because
``deploy.orchestrator``, ``dq.dispatcher``, and ``monitor.dispatcher``
each had their own private ``reg.resolve(...)`` + ``isinstance(impl,
type)`` dance instead of routing through one helper.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sigantry_core._dispatch import GROUP_TO_TOML_KEY, resolve_seam
from sigantry_core.config import ToolkitSettings
from sigantry_core.registry import (
    GROUP_DEPLOY_PROFILES,
    GROUP_DQ_GATES,
    GROUP_TELEMETRY_SINKS,
    Registry,
)
from sigantry_core.testing.doubles import (
    FakeDeployProfile,
    NoopGate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Algorithmic contract
# ---------------------------------------------------------------------------


def test_resolve_seam_direct_di_wins() -> None:
    """``impl`` kwarg short-circuits everything else, including a registry hit."""
    sentinel = object()
    reg = Registry()
    reg.register(GROUP_DEPLOY_PROFILES, "trap", FakeDeployProfile)
    out = resolve_seam(GROUP_DEPLOY_PROFILES, "trap", impl=sentinel, registry=reg)
    assert out is sentinel


def test_resolve_seam_empty_name_returns_none() -> None:
    """Falsy ``name`` -> ``None`` regardless of registry contents."""
    reg = Registry()
    assert resolve_seam(GROUP_DEPLOY_PROFILES, None, registry=reg) is None
    assert resolve_seam(GROUP_DEPLOY_PROFILES, "", registry=reg) is None


def test_resolve_seam_returns_class_instance() -> None:
    """Class result -> instantiated. Zero-arg path (no settings, no config)."""
    reg = Registry()
    reg.register(GROUP_DQ_GATES, "noop", NoopGate)
    out = resolve_seam(GROUP_DQ_GATES, "noop", registry=reg)
    assert isinstance(out, NoopGate)


def test_resolve_seam_returns_pre_built_factory_as_is() -> None:
    """Non-class registered impl is returned verbatim (factory contract)."""
    pre = NoopGate()
    reg = Registry()
    reg.register(GROUP_DQ_GATES, "pre", pre)
    out = resolve_seam(GROUP_DQ_GATES, "pre", registry=reg)
    assert out is pre


def test_resolve_seam_unknown_name_raises_key_error() -> None:
    """Missing name raises KeyError surfacing the name itself."""
    reg = Registry()
    with pytest.raises(KeyError, match="ghost"):
        resolve_seam(GROUP_DQ_GATES, "ghost", registry=reg)


# ---------------------------------------------------------------------------
# from_settings classmethod path -- a v3 contract NEW dispatchers acquired
# via the unification (orchestrator + dq dispatcher used to lack it).
# ---------------------------------------------------------------------------


class _SinkWithFromSettings:
    """Sink with a ``from_settings`` classmethod that remaps a key."""

    def __init__(self, *, endpoint: str) -> None:
        self.endpoint = endpoint

    @classmethod
    def from_settings(cls, raw: dict) -> _SinkWithFromSettings:
        return cls(endpoint=raw["url"])

    def emit(self, event) -> None:  # pragma: no cover - not exercised
        return None

    def flush(self, timeout_s: float = 5.0) -> None:  # pragma: no cover
        return None


def test_resolve_seam_from_settings_wins_over_kwargs_when_settings_provided() -> None:
    """``cls.from_settings(cfg)`` is preferred when ``settings`` is provided."""
    reg = Registry()
    reg.register(GROUP_TELEMETRY_SINKS, "factory_driven", _SinkWithFromSettings)
    settings = ToolkitSettings(
        core={"tenant_id": "t-1"},
        telemetry={
            "sink": "factory_driven",
            "factory_driven": {"url": "https://example.com"},
        },
    )

    out = resolve_seam(
        GROUP_TELEMETRY_SINKS,
        "factory_driven",
        registry=reg,
        settings=settings,
    )
    assert isinstance(out, _SinkWithFromSettings)
    assert out.endpoint == "https://example.com"


def test_resolve_seam_skips_from_settings_when_settings_is_none() -> None:
    """``settings is None`` short-circuits the from_settings branch.

    This preserves the orchestrator + dq-dispatcher zero-arg behaviour
    they had before W2.2 -- direct callers without TOML never trigger
    a factory classmethod they didn't opt into.
    """

    class _ZeroArgClass:
        def __init__(self) -> None:
            self.from_settings_called = False

        @classmethod
        def from_settings(cls, raw: dict) -> _ZeroArgClass:
            inst = cls()
            inst.from_settings_called = True
            return inst

        # Stub run() so it can play DataQualityGate if ever called.
        def run(self, suite, data_ref):  # pragma: no cover - not exercised
            return None

    reg = Registry()
    reg.register(GROUP_DQ_GATES, "ambiguous", _ZeroArgClass)
    out = resolve_seam(GROUP_DQ_GATES, "ambiguous", registry=reg)
    assert isinstance(out, _ZeroArgClass)
    assert out.from_settings_called is False


# ---------------------------------------------------------------------------
# Kwargs path + actionable TypeError wrapping
# ---------------------------------------------------------------------------


class _SinkNeedsArgs:
    def __init__(self, *, endpoint: str, dcr_id: str) -> None:
        self.endpoint = endpoint
        self.dcr_id = dcr_id

    def emit(self, event) -> None:  # pragma: no cover
        return None

    def flush(self, timeout_s: float = 5.0) -> None:  # pragma: no cover
        return None


def test_resolve_seam_passes_namespaced_kwargs_when_no_from_settings() -> None:
    """``[telemetry.<name>]`` kwargs flow into ``cls(**kwargs)``."""
    reg = Registry()
    reg.register(GROUP_TELEMETRY_SINKS, "needs_args", _SinkNeedsArgs)
    settings = ToolkitSettings(
        core={"tenant_id": "t-1"},
        telemetry={
            "sink": "needs_args",
            "needs_args": {"endpoint": "https://e", "dcr_id": "d"},
        },
    )

    out = resolve_seam(GROUP_TELEMETRY_SINKS, "needs_args", registry=reg, settings=settings)
    assert isinstance(out, _SinkNeedsArgs)
    assert out.endpoint == "https://e"
    assert out.dcr_id == "d"


def test_resolve_seam_actionable_typeerror_when_kwargs_missing() -> None:
    """Plugin needs args + no TOML subsection -> wrapped TypeError points at the fix."""
    reg = Registry()
    reg.register(GROUP_TELEMETRY_SINKS, "needs_args", _SinkNeedsArgs)
    settings = ToolkitSettings(
        core={"tenant_id": "t-1"},
        telemetry={"sink": "needs_args"},
    )

    with pytest.raises(TypeError, match=r"\[telemetry\.needs_args\]"):
        resolve_seam(
            GROUP_TELEMETRY_SINKS,
            "needs_args",
            registry=reg,
            settings=settings,
        )


def test_resolve_seam_typeerror_wraps_kwargs_constructor_failure() -> None:
    """Wrong-shape kwargs -> wrapped TypeError citing the section path."""

    class _StrictSink:
        def __init__(self, *, endpoint: str) -> None:  # only accepts endpoint
            self.endpoint = endpoint

        def emit(self, e) -> None:  # pragma: no cover
            return None

        def flush(self, t: float = 5.0) -> None:  # pragma: no cover
            return None

    reg = Registry()
    reg.register(GROUP_TELEMETRY_SINKS, "strict", _StrictSink)
    settings = ToolkitSettings(
        core={"tenant_id": "t-1"},
        telemetry={
            "sink": "strict",
            "strict": {"endpoint": "ok", "extra_field": "BOOM"},
        },
    )

    with pytest.raises(TypeError, match=r"\[telemetry\.strict\]"):
        resolve_seam(GROUP_TELEMETRY_SINKS, "strict", registry=reg, settings=settings)


def test_resolve_seam_rejects_non_table_subsection() -> None:
    """Subsection present but not a TOML table -> TypeError at config-extraction."""

    class _AnyClass:
        def __init__(self) -> None:  # pragma: no cover
            return None

        def run(self, suite, data_ref) -> None:  # pragma: no cover
            return None

    reg = Registry()
    reg.register(GROUP_DQ_GATES, "any", _AnyClass)
    # Rare but possible: operator wrote ``any = "wrong"`` instead of a
    # ``[dq.any]`` table. We reject loudly rather than silently dropping it.
    settings = ToolkitSettings(
        core={"tenant_id": "t-1"},
        dq={"gate": "any", "any": "wrong"},
    )

    with pytest.raises(TypeError, match=r"\[dq\.any\] must be a TOML table"):
        resolve_seam(GROUP_DQ_GATES, "any", registry=reg, settings=settings)


# ---------------------------------------------------------------------------
# Group -> TOML map -- the lockdown surface
# ---------------------------------------------------------------------------


def test_group_to_toml_key_covers_every_canonical_group() -> None:
    """Every ``GROUP_*`` constant in the registry has a TOML mapping."""
    from sigantry_core import registry as r

    canonical = {getattr(r, name) for name in dir(r) if name.startswith("GROUP_")}
    assert canonical == set(GROUP_TO_TOML_KEY.keys()), (
        "GROUP_TO_TOML_KEY drifted from sigantry_core.registry.GROUP_*. "
        "Adding a new seam? Update both the registry constants and the "
        "TOML map."
    )


# ---------------------------------------------------------------------------
# Falsifiability meta-gate -- the inline-probe pattern is gone for good
# ---------------------------------------------------------------------------


_DISPATCHER_PATHS = (
    Path("sigantry_core/deploy/orchestrator.py"),
    Path("sigantry_core/dq/dispatcher.py"),
    Path("sigantry_core/monitor/dispatcher.py"),
    Path("sigantry_core/api.py"),
)


def test_resolve_seam_is_used_by_every_dispatcher() -> None:
    """Every dispatcher imports ``resolve_seam`` -- the unification gate.

    Pre-W2.2 the four dispatchers each carried inline ``reg.resolve(...)``
    + ``isinstance(impl, type)`` probes. Post-W2.2 there is a single
    canonical helper. This test fails on the pre-fix tree and ratchets
    the unification: any future dispatcher that drifts back into an
    inline probe must also drop the ``resolve_seam`` import to slip past
    this gate, which is a much louder code review signal than a simple
    LOC creep.
    """
    for rel in _DISPATCHER_PATHS:
        path = REPO_ROOT / rel
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sigantry_core._dispatch":  # noqa: SIM102
                if any(alias.name == "resolve_seam" for alias in node.names):
                    imported = True
                    break
        assert imported, (
            f"{rel} no longer imports `resolve_seam` from "
            "sigantry_core._dispatch. The W2.2 unification has been broken."
        )


def test_no_inline_isinstance_type_dance_outside_dispatch() -> None:
    """No ``isinstance(impl, type)`` after a ``reg.resolve`` outside ``_dispatch.py``.

    The pattern ``reg.resolve(group, name)`` followed by ``isinstance(impl,
    type)`` is the canonical "pre-built vs class" branch. It now lives
    only in :mod:`sigantry_core._dispatch`. This test fails on the pre-fix
    tree where the pattern existed in three dispatcher modules.
    """
    sigantry_core = REPO_ROOT / "sigantry_core"
    allowed = {(sigantry_core / "_dispatch.py").resolve()}

    offenders: list[str] = []
    for path in sorted(sigantry_core.rglob("*.py")):
        if path.resolve() in allowed:
            continue
        src = path.read_text(encoding="utf-8")
        if ".resolve(" not in src or "isinstance(" not in src:
            continue
        tree = ast.parse(src, filename=str(path))
        # Look for the literal pattern: a line with `isinstance(<x>, type)`
        # appearing in the same function body as a `<reg>.resolve(<...>)` call.
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_resolve = False
            has_isinstance_type = False
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "resolve"
                ):
                    has_resolve = True
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "isinstance"
                    and len(node.args) == 2
                    and isinstance(node.args[1], ast.Name)
                    and node.args[1].id == "type"
                ):
                    has_isinstance_type = True
            if has_resolve and has_isinstance_type:
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}::{func.name} (line {func.lineno})")

    assert offenders == [], (
        "Inline `reg.resolve(...) + isinstance(impl, type)` dance found "
        "outside sigantry_core/_dispatch.py. Use resolve_seam(...) "
        "instead.\n  - " + "\n  - ".join(offenders)
    )
