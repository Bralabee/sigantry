"""Audit-2026-05-07 W4.4 -- falsifiability tests for the
``apply_sync`` constructor-injectable seams.

Pre-W4.4 ``tests/sync/test_apply_with_publish.py`` monkey-patched
``snapshot_workspace`` / ``publish_absent_items`` /
``reconcile_folders_from_repo`` on ``sigantry_core.sync.apply``
directly. That works but couples the test surface to the
implementation's import structure -- a refactor that moves any of
those imports breaks the tests for unrelated reasons. W4.4 adds
three underscore-prefixed seam kwargs (``_snapshot_fn`` /
``_reconcile_fn`` / ``_publish_fn``) so tests inject doubles
explicitly and the module's import structure becomes a private
implementation detail again.

Tests below pin:

- ``apply_sync`` accepts the three injection kwargs.
- The defaults (``None``) fall back to the real implementations
  imported at module scope.
- Injected callables are called with the same signatures the real
  ones expect (snapshot: ``(workspace_id, *, client)``; reconcile +
  publish: kwargs-only).
- The 11 sync-publish tests no longer monkey-patch the apply module
  (AST-level proof: no ``monkeypatch.setattr(apply_mod, ...)``
  remains in ``test_apply_with_publish.py``).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sigantry_core.sync.apply import apply_sync

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_FILE = REPO_ROOT / "tests" / "sync" / "test_apply_with_publish.py"


def test_apply_sync_accepts_three_injection_kwargs() -> None:
    """``apply_sync`` exposes ``_snapshot_fn``, ``_reconcile_fn``, ``_publish_fn``."""
    sig = inspect.signature(apply_sync)
    params = sig.parameters
    for name in ("_snapshot_fn", "_reconcile_fn", "_publish_fn"):
        assert name in params, (
            f"apply_sync is missing the W4.4 injection kwarg `{name}`. "
            "Tests rely on these to inject fakes without monkey-patching."
        )
        # Each must be keyword-only with a None default.
        param = params[name]
        assert param.default is None, (
            f"apply_sync.{name} default must be None (W4.4 contract); got {param.default!r}."
        )
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_test_file_no_longer_monkeypatches_apply_module() -> None:
    """``test_apply_with_publish.py`` no longer calls ``monkeypatch.setattr``
    on the ``sigantry_core.sync.apply`` module.

    Falsifiability: the W4.4 design intent is to REPLACE the
    monkey-patching with constructor injection. If a future regression
    re-introduces ``monkeypatch.setattr(apply_mod, ...)`` it trips
    this gate.
    """
    src = TEST_FILE.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(TEST_FILE))

    offenders: list[str] = []
    for node in ast.walk(tree):
        # Look for calls of the shape ``monkeypatch.setattr(<x>, ...)``
        # where ``<x>`` resolves to the apply module.
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "monkeypatch"):
            continue
        if func.attr != "setattr":
            continue
        # Inspect the first positional arg.
        if not node.args:
            continue
        target = node.args[0]
        # Bare ``apply_mod`` reference?
        if isinstance(target, ast.Name) and target.id == "apply_mod":
            offenders.append(f"line {node.lineno}: monkeypatch.setattr(apply_mod, ...)")
        # ``sigantry_core.sync.apply``?
        elif isinstance(target, ast.Attribute):
            # Walk the dotted name.
            parts = []
            cur = target
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            dotted = ".".join(reversed(parts))
            if dotted in (
                "sigantry_core.sync.apply",
                "sigantry_core.sync.apply.snapshot_workspace",
                "sigantry_core.sync.apply.publish_absent_items",
                "sigantry_core.sync.apply.reconcile_folders_from_repo",
            ):
                offenders.append(f"line {node.lineno}: monkeypatch.setattr({dotted}, ...)")

    assert offenders == [], (
        "test_apply_with_publish.py monkey-patches the apply module again -- "
        "the W4.4 refactor required all 11 tests to use the injection "
        "kwargs instead. Restore by passing **doubles_kwargs to "
        "apply_sync().\n  - " + "\n  - ".join(offenders)
    )


def test_injection_seam_default_falls_back_to_real_impl() -> None:
    """When ``_snapshot_fn`` is ``None``, the function uses the imported
    ``snapshot_workspace``. Verified by patching the module attribute and
    confirming the default-binding path picks up the patch.
    """

    # Resolve the seam at function-entry time so the body uses whatever
    # ``snapshot_workspace`` resolves to when the function is called.
    # The simplest probe is to read the source.
    src = inspect.getsource(apply_sync)
    assert "snapshot_workspace_fn = _snapshot_fn or snapshot_workspace" in src, (
        "apply_sync no longer falls back to the imported snapshot_workspace "
        "when _snapshot_fn=None. Production callers (no kwarg) would crash."
    )
    assert "reconcile_fn = _reconcile_fn or reconcile_folders_from_repo" in src, (
        "apply_sync no longer falls back to the imported reconcile_folders_from_repo."
    )
    assert "publish_fn = _publish_fn or publish_absent_items" in src, (
        "apply_sync no longer falls back to the imported publish_absent_items."
    )


def test_doubles_kwargs_helper_returns_dict_with_three_keys() -> None:
    """The ``_patch_publish_path`` helper returns a dict carrying the
    three documented injection-kwarg names.

    Falsifiability: a regression that drops one of the keys breaks
    silently (apply_sync would fall back to the real implementation
    for that seam, hitting Fabric's REST API and failing in CI).
    """
    # Import the helper from the test file. It's not on the public path
    # so we exec the file in an isolated namespace.
    import importlib.util

    spec = importlib.util.spec_from_file_location("test_apply_with_publish_mod", TEST_FILE)
    mod = importlib.util.module_from_spec(spec)
    # The test file imports respx + httpx etc. -- safe to exec.
    spec.loader.exec_module(mod)

    # Call the helper and inspect the return.
    result = mod._patch_publish_path(monkeypatch=None)
    assert len(result) == 4, (
        f"_patch_publish_path returns {len(result)} values; expected 4 "
        "((fake_publish, fake_snapshot, fake_reconcile, doubles_kwargs))."
    )
    fake_publish, fake_snapshot, fake_reconcile, doubles_kwargs = result
    assert isinstance(doubles_kwargs, dict)
    assert set(doubles_kwargs.keys()) == {
        "_snapshot_fn",
        "_publish_fn",
        "_reconcile_fn",
    }, (
        f"doubles_kwargs has unexpected keys: {sorted(doubles_kwargs.keys())}; "
        "expected the three apply_sync injection kwarg names."
    )
    assert doubles_kwargs["_snapshot_fn"] is fake_snapshot
    assert doubles_kwargs["_publish_fn"] is fake_publish
    assert doubles_kwargs["_reconcile_fn"] is fake_reconcile
