"""Plan 14-02: byte-snapshot + purity + audit-hash determinism tests.

Asserts the STARTER-07 renderer-half invariants:

1. ``render_markdown(payload)`` is pure -- same input always produces
   byte-identical output.
2. ``render_markdown(factory())`` matches the committed byte-snapshot
   fixture under ``tests/fixtures/pr-bot/snapshots/`` for each of the
   four scenarios (tmdl-only / lakehouse-only / both / none).
3. ``PrCommentPayload.with_hash()`` produces a deterministic SHA-256 over
   the canonical payload JSON; equal inputs produce equal hashes.
4. The fixed Lakehouse footer (RESEARCH §Critical Finding) is present in
   rendered output for every scenario where ``lakehouse_diff`` is
   non-None, and absent otherwise.
5. ``with_hash()`` overwrites any constructor-supplied seed value (the
   seed is excluded from canonicalisation by ``canonical_payload()``
   popping ``audit_hash``).

Cross-provider POST-body byte-equality (the other half of STARTER-07)
is asserted in Plan 14-05's provider tests.

Note on conftest imports: the repo does NOT ship a top-level
``tests/__init__.py`` (Phase 13 idiom; see Phase 14 Wave 0 deviation 1
in ``14-00-SUMMARY.md``), so ``from tests.sigantry_core.pr_bot.conftest
import ...`` does not resolve as a Python package path. The factories
defined in conftest.py are loaded via ``importlib.util`` from the file
path -- this is the same idiom used by Phase 13 integration tests when
they need to share a helper across modules without an ``__init__.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_conftest() -> ModuleType:
    """Load the sibling ``conftest.py`` as an importable module.

    Pytest auto-discovers ``conftest.py`` for fixture purposes, but
    fixture-injection differs from module-level imports. We need the
    factory functions as plain callables so they can be invoked outside
    a pytest fixture context (e.g. by the snapshot-regeneration script
    in 14-02-PLAN). This loader makes the conftest's symbols available
    as a regular module without requiring ``tests/__init__.py``.
    """
    conftest_path = Path(__file__).resolve().parent / "conftest.py"
    spec = importlib.util.spec_from_file_location("_pr_bot_conftest_loaded", conftest_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_CONFTEST = _load_conftest()
FIXTURES_DIR: Path = _CONFTEST.FIXTURES_DIR
deterministic_pr_summary = _CONFTEST.deterministic_pr_summary
deterministic_tmdl_diff = _CONFTEST.deterministic_tmdl_diff
deterministic_lakehouse_diff = _CONFTEST.deterministic_lakehouse_diff
deterministic_tmdl_only_payload = _CONFTEST.deterministic_tmdl_only_payload
deterministic_lakehouse_only_payload = _CONFTEST.deterministic_lakehouse_only_payload
deterministic_both_payload = _CONFTEST.deterministic_both_payload
deterministic_no_changes_payload = _CONFTEST.deterministic_no_changes_payload


def test_render_is_pure() -> None:
    """render_markdown(payload) is a pure function: same input -> same output, no I/O."""
    from sigantry_core.pr_bot.payload import render_markdown

    p = deterministic_tmdl_only_payload()
    a = render_markdown(p)
    b = render_markdown(p)
    assert a == b, "render_markdown produced different output for the same payload"


def test_three_scenarios_byte_identical() -> None:
    """Four scenarios all produce byte-identical output to the committed snapshots.

    Despite the function name (Wave 0 stub used 'three' for symmetry
    with Phase 11; this test covers four scenarios -- including the
    D-07 'no changes detected' case). The function name is preserved
    for migration ease.
    """
    from sigantry_core.pr_bot.payload import render_markdown

    scenarios = [
        ("tmdl-only", deterministic_tmdl_only_payload),
        ("lakehouse-only", deterministic_lakehouse_only_payload),
        ("both", deterministic_both_payload),
        ("none", deterministic_no_changes_payload),
    ]
    for name, factory in scenarios:
        snapshot_path = FIXTURES_DIR / "snapshots" / f"{name}.md"
        expected = snapshot_path.read_text(encoding="utf-8")
        actual = render_markdown(factory())
        assert actual == expected, (
            f"render_markdown drift for scenario '{name}' -- "
            "regenerate via the snapshot script in 14-02-PLAN."
        )


def test_payload_audit_hash_deterministic() -> None:
    """PrCommentPayload.audit_hash is SHA-256 over canonical JSON; equal inputs -> equal hashes."""
    a = deterministic_tmdl_only_payload()
    b = deterministic_tmdl_only_payload()
    assert a.audit_hash == b.audit_hash
    assert a.verify_hash() is True
    assert b.verify_hash() is True
    # Distinct factory output produces distinct hash (negative control).
    c = deterministic_lakehouse_only_payload()
    assert a.audit_hash != c.audit_hash


def test_payload_lakehouse_footer_present() -> None:
    """Lakehouse-section payload includes the fixed reviewer-facing 'column types not in Git' footer.

    Source: 14-RESEARCH.md §Critical Finding. The footer MUST appear in
    every rendered comment whose ``lakehouse_diff`` is non-None, and MUST
    be absent when ``lakehouse_diff`` is None.
    """
    from sigantry_core.pr_bot.payload import render_markdown

    footer_phrase = "Microsoft Fabric does not track Lakehouse table column types in Git"

    for factory in (deterministic_lakehouse_only_payload, deterministic_both_payload):
        out = render_markdown(factory())
        assert footer_phrase in out, f"Lakehouse footer missing from {factory.__name__} render"

    # Negative control: TMDL-only and no-changes scenarios MUST NOT carry the footer
    # (only the Lakehouse section emits it; the warnings list inside the JSON
    # block uses the short phrase but the long-form footer line is gated on
    # `lakehouse_diff is not None`).
    for factory in (deterministic_tmdl_only_payload, deterministic_no_changes_payload):
        out = render_markdown(factory())
        assert footer_phrase not in out, f"Lakehouse footer leaked into {factory.__name__} render"


def test_audit_hash_excludes_volatile_fields() -> None:
    """with_hash() ignores any constructor-supplied seed value.

    Constructing a payload with audit_hash="DIFFERENT_SEED_VALUE" then
    calling .with_hash() MUST produce the same digest as constructing
    the same payload with the default seed (empty string). This proves
    the seed is excluded from canonicalisation by canonical_payload()
    popping the ``audit_hash`` key before hashing.
    """
    from sigantry_core.pr_bot.payload import (
        PrCommentPayload,
        compute_audit_hash,
    )

    summary = deterministic_pr_summary()
    tmdl = deterministic_tmdl_diff()

    # Two payloads with identical content but different seed values.
    p_default = PrCommentPayload(summary=summary, tmdl_diff=tmdl, audit_hash="").with_hash()
    p_seeded = PrCommentPayload(
        summary=summary, tmdl_diff=tmdl, audit_hash="DIFFERENT_SEED_VALUE"
    ).with_hash()

    assert p_default.audit_hash == p_seeded.audit_hash, (
        "with_hash() leaked the constructor seed into the canonical payload"
    )

    # The free-function helper agrees with the instance method.
    assert compute_audit_hash(p_default) == p_default.audit_hash
    assert compute_audit_hash(p_seeded) == p_seeded.audit_hash
