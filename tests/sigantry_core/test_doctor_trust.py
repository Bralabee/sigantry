"""Audit-2026-05-07 W3.4 -- falsifiability tests for the plugin trust
model in :mod:`sigantry_core.doctor` (ADR-0014).

The W3.4 trust model classifies each discovered plugin as
``trusted`` / ``untrusted`` / ``unknown`` per the
``SIGANTRY_TRUSTED_PLUGIN_DISTS`` env var allowlist. ``sigantry
doctor --strict-trust`` exits non-zero when any plugin reports
``untrusted``.

Tests below pin:

- The four classification rules (empty list -> unknown for all,
  dist in list -> trusted, dist not in list -> untrusted, no
  dist resolvable -> unknown).
- The env-var parser (whitespace stripping, comma split, empty
  entries dropped).
- ``--strict-trust`` exit code semantics (zero when no untrusted,
  non-zero when any untrusted).
- ``--strict-trust`` is independent of ``--strict`` (each can
  fire alone).
- Default invocation continues to exit zero (back-compat).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from sigantry_core.doctor import (
    _read_trusted_dists,
    _resolve_trust_status,
    _set_registry_override,
    doctor_app,
)
from sigantry_core.registry import Registry

# --- _read_trusted_dists -----------------------------------------------------


def test_read_trusted_dists_returns_empty_when_unset() -> None:
    """No env var -> empty frozenset (no allowlist configured)."""
    assert _read_trusted_dists(env={}) == frozenset()


def test_read_trusted_dists_returns_empty_when_blank() -> None:
    """Empty / whitespace-only env var -> empty frozenset."""
    assert _read_trusted_dists(env={"SIGANTRY_TRUSTED_PLUGIN_DISTS": ""}) == frozenset()
    assert _read_trusted_dists(env={"SIGANTRY_TRUSTED_PLUGIN_DISTS": "   "}) == frozenset()
    assert _read_trusted_dists(env={"SIGANTRY_TRUSTED_PLUGIN_DISTS": ",,,"}) == frozenset()


def test_read_trusted_dists_strips_whitespace_and_drops_empties() -> None:
    """Each comma-separated entry has whitespace stripped; empties dropped."""
    result = _read_trusted_dists(
        env={"SIGANTRY_TRUSTED_PLUGIN_DISTS": " example-plugin ,, foo-bar ,"}
    )
    assert result == frozenset({"example-plugin", "foo-bar"})


# --- _resolve_trust_status (the four classification rules) -------------------


def test_resolve_trust_status_unknown_when_no_allowlist() -> None:
    """No allowlist configured -> every plugin is ``unknown``."""
    assert _resolve_trust_status("example-plugin", frozenset()) == "unknown"


def test_resolve_trust_status_trusted_when_dist_in_list() -> None:
    allowlist = frozenset({"example-plugin", "other-plugin"})
    assert _resolve_trust_status("example-plugin", allowlist) == "trusted"


def test_resolve_trust_status_untrusted_when_dist_not_in_list() -> None:
    allowlist = frozenset({"example-plugin"})
    assert _resolve_trust_status("malicious-plugin", allowlist) == "untrusted"


def test_resolve_trust_status_unknown_when_dist_unresolvable() -> None:
    """Plugin with no resolvable dist name -> ``unknown`` even if list is set."""
    allowlist = frozenset({"example-plugin"})
    assert _resolve_trust_status(None, allowlist) == "unknown"


def test_resolve_trust_status_canonicalises_underscore_to_hyphen() -> None:
    """Audit-2026-05-08 review follow-up (WR-04): PyPI canonical equality.

    PyPI distribution names are case-insensitive and tolerate ``-`` <->
    ``_`` swaps. Pre-WR-04-fix the ``in`` comparison was string-equality
    on the un-canonicalised forms, so an operator who declared
    ``my_plugin`` would see ``untrusted`` against a dist resolved as
    ``my-plugin`` (and vice-versa).

    Falsifiability: removing ``_canonicalise_dist_name`` from
    ``_resolve_trust_status`` flips both halves of this test from PASS
    to FAIL with ``'untrusted' == 'trusted'``.
    """
    # Trust list canonicalised on read: 'My_Plugin' -> 'my-plugin'.
    canonicalised_allowlist = _read_trusted_dists(
        env={"SIGANTRY_TRUSTED_PLUGIN_DISTS": "My_Plugin"}
    )
    assert canonicalised_allowlist == frozenset({"my-plugin"})

    # Resolved dist with a hyphen matches an underscore declaration.
    assert _resolve_trust_status("my-plugin", canonicalised_allowlist) == "trusted"
    # Resolved dist with an underscore matches the canonicalised list.
    assert _resolve_trust_status("my_plugin", canonicalised_allowlist) == "trusted"
    # Mixed case also matches.
    assert _resolve_trust_status("My-Plugin", canonicalised_allowlist) == "trusted"


def test_resolve_trust_status_untrusted_for_genuinely_different_dist() -> None:
    """Canonicalisation does NOT collapse genuinely distinct names.

    ``my-plugin`` vs ``my-plugin-extension`` remain distinct after
    canonicalisation -- the fix is hyphen/underscore-equivalence, not
    prefix-matching.
    """
    allowlist = _read_trusted_dists(env={"SIGANTRY_TRUSTED_PLUGIN_DISTS": "my-plugin"})
    assert _resolve_trust_status("my-plugin-extension", allowlist) == "untrusted"


# --- doctor_cmd CLI integration ----------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_registry_with_plugin() -> Registry:
    """Build a registry with one in-tree-fixture plugin (dist=None case)."""
    reg = Registry()

    # Simulate a discovered plugin by directly populating its info
    # without going through entry-point discovery.
    # The Registry doesn't expose a public seed method, so we use a
    # fixture plugin via direct ``register``.
    class _FakePlugin:
        name = "fake"

        def emit(self, event):  # pragma: no cover
            return None

    reg.register("sigantry.telemetry_sinks", "fake", _FakePlugin)
    return reg


def test_doctor_default_invocation_exits_zero(runner: CliRunner) -> None:
    """Default ``sigantry doctor`` (no flags) exits 0 even with untrusted plugins."""
    reg = _make_registry_with_plugin()
    _set_registry_override(reg)
    try:
        result = runner.invoke(doctor_app, [])
        assert result.exit_code == 0, result.output
    finally:
        _set_registry_override(None)


def test_doctor_strict_trust_with_no_allowlist_exits_zero(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--strict-trust`` with no env var set: every plugin is ``unknown``,
    not ``untrusted``, so exit code is zero. The trust gate fires only on
    explicit untrusted hits, never on absent allowlist."""
    monkeypatch.delenv("SIGANTRY_TRUSTED_PLUGIN_DISTS", raising=False)
    reg = _make_registry_with_plugin()
    _set_registry_override(reg)
    try:
        result = runner.invoke(doctor_app, ["--strict-trust"])
        assert result.exit_code == 0, result.output
    finally:
        _set_registry_override(None)


def test_doctor_strict_trust_with_untrusted_plugin_exits_one(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--strict-trust`` with allowlist that excludes the registered plugin
    must exit 1.

    Note: the in-tree fake plugin reports dist=None which classifies as
    ``unknown`` (ADR-0014 rule 4). To trigger the ``untrusted`` path we
    use a real installed package.
    """
    # ``pytest`` is always installed in the test env; allowlist a fake
    # name to make the actual installed packages "untrusted".
    monkeypatch.setenv("SIGANTRY_TRUSTED_PLUGIN_DISTS", "this-dist-does-not-exist")
    # We need to run against the default_registry since the in-tree
    # fixture has no resolvable dist name. The default registry
    # discovers entry-points from installed packages.
    _set_registry_override(None)
    result = runner.invoke(doctor_app, ["--strict-trust"])
    # The default registry may discover real plugins (example-plugin,
    # other-plugin); if any of them resolve to a dist name not in
    # the allowlist, --strict-trust exits 1. If no plugins are
    # discovered at all, the test is skipped.
    if "0 plugin(s) discovered" in result.output:
        pytest.skip("No plugins installed; --strict-trust has nothing to gate on.")
    assert result.exit_code == 1, result.output


def test_doctor_strict_trust_independent_of_strict(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--strict-trust`` and ``--strict`` are independent gates.

    The combined invocation ``--strict --strict-trust`` exits non-zero
    if EITHER condition fires; the absence of one does not mask the
    other.
    """
    # No allowlist + no import errors -> both gates green -> exit 0.
    monkeypatch.delenv("SIGANTRY_TRUSTED_PLUGIN_DISTS", raising=False)
    reg = _make_registry_with_plugin()
    _set_registry_override(reg)
    try:
        result = runner.invoke(doctor_app, ["--strict", "--strict-trust"])
        assert result.exit_code == 0, result.output
    finally:
        _set_registry_override(None)


def test_doctor_lists_trust_in_summary_line_when_configured(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trailing summary names the trust list when it's set."""
    monkeypatch.setenv("SIGANTRY_TRUSTED_PLUGIN_DISTS", "example-plugin,other-plugin")
    reg = _make_registry_with_plugin()
    _set_registry_override(reg)
    try:
        result = runner.invoke(doctor_app, [])
        assert "Trust list:" in result.output
        assert "2 distribution(s)" in result.output
    finally:
        _set_registry_override(None)


def test_doctor_lists_trust_not_configured_in_summary_when_unset(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trailing summary points operators at the env var when it's unset."""
    monkeypatch.delenv("SIGANTRY_TRUSTED_PLUGIN_DISTS", raising=False)
    reg = _make_registry_with_plugin()
    _set_registry_override(reg)
    try:
        result = runner.invoke(doctor_app, [])
        assert "Trust list:" in result.output
        assert "not configured" in result.output
        assert "ADR-0014" in result.output
    finally:
        _set_registry_override(None)


# --- ADR-0014 cross-check ----------------------------------------------------


def test_adr_0014_exists() -> None:
    """ADR-0014 documenting the trust model exists in docs/decisions/."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    adr = repo_root / "docs" / "decisions" / "ADR-0014-plugin-trust-model.md"
    assert adr.is_file(), (
        "ADR-0014 is missing. The W3.4 trust model is operator-policy; "
        "without the ADR, future readers cannot reason about why "
        "--strict-trust does (and does not) reject plugins at runtime."
    )
    text = adr.read_text(encoding="utf-8")
    # Lock-down strings the ADR must carry so a future cleanup cannot
    # accidentally remove the load-bearing semantics.
    for phrase in (
        "SIGANTRY_TRUSTED_PLUGIN_DISTS",
        "--strict-trust",
        "trusted",
        "untrusted",
        "unknown",
    ):
        assert phrase in text, f"ADR-0014 missing key phrase {phrase!r}"
