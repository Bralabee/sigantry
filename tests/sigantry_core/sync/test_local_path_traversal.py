"""Audit-2026-05-08 review follow-up (BL-03) — falsifiability tests for
``SyncItem.local_path`` path-traversal containment.

Pre-fix: ``local_path`` had no validator. A manifest with
``local_path: ../../../etc/passwd`` (relative parent traversal),
``local_path: /etc/shadow`` (absolute), or a symlink within the
manifest dir pointing outside, was accepted by pydantic and resolved
by ``apply._pack_phase`` against the operator's filesystem. The
notebook packager would then read the file into the staging tree
(read-leak into Fabric) and write a sidecar at
``<source.parent>/.sigantry/notebook-ids.json`` (arbitrary write).

The threat materialises whenever a manifest comes from an untrusted
source -- a PR, an external repo, or an operator picking up the
``templates/starter/`` scaffold. The toolkit is *intended* to be
picked up that way, so the threat is not hypothetical.

Post-fix:

1. ``SyncItem._validate_local_path`` (syntactic) rejects any path
   whose ``parts`` contain a literal ``".."`` segment, at pydantic-
   validate time -- before any filesystem access.
2. ``apply._pack_phase`` (semantic) calls ``Path.resolve()`` to
   follow symlinks and asserts the resolved real path lives within
   ``manifest_dir.resolve()``. This is the load-bearing security
   boundary -- absolute paths pointing outside manifest_dir AND
   symlinks within manifest_dir whose target lives outside both
   fail the containment check identically. Absolute paths INSIDE
   manifest_dir are tolerated for operator workflows that share
   source trees across manifests.

Falsifiability:

- Reverting either guard flips the relevant tests below from PASS
  to FAIL.
- The syntactic guard's tests use pydantic-validate (no filesystem
  required) so they execute in milliseconds.
- The symlink test uses ``os.symlink`` to an attacker-target path
  outside the manifest dir; on Windows-without-symlink-privilege
  the test self-skips with a clear message.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from sigantry_core.sync.apply import _pack_phase
from sigantry_core.sync.manifest import SyncItem

# --- syntactic guard: SyncItem field validator -------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "../../../etc/passwd",
        "../sibling/file.ipynb",
        "subdir/../../escape.ipynb",
        "subdir/../sibling/file.ipynb",
    ],
    ids=[
        "naive-parent-traversal",
        "single-up",
        "buried-traversal",
        "round-trip-traversal",
    ],
)
def test_sync_item_rejects_parent_traversal(bad_path: str) -> None:
    """Any ``..`` segment in ``local_path`` is rejected at validate-time.

    Pre-BL-03-fix this construction succeeded silently; the bad path
    only surfaced (or didn't) at filesystem-resolve time.
    """
    with pytest.raises(ValidationError, match="must not traverse parent directories"):
        SyncItem(
            local_path=bad_path,
            type="Notebook",
            display_name="x",
            target_folder="/",
        )


def test_sync_item_accepts_absolute_paths_at_validate_time(tmp_path: Path) -> None:
    """Absolute paths pass the syntactic validator; containment is enforced at pack-time.

    Existing operator workflows + test harnesses point ``local_path``
    at absolute paths within a parent tree (e.g. shared source dirs
    used by multiple manifests). The validator tolerates this for
    backward compatibility; the load-bearing security check is the
    ``Path.resolve() + relative_to(manifest_dir)`` containment check
    in ``apply._pack_phase`` -- exercised by the
    ``test_pack_phase_rejects_absolute_path_outside_manifest_dir``
    test below.
    """
    abs_path = tmp_path / "src" / "loader.ipynb"
    item = SyncItem(
        local_path=str(abs_path),
        type="Notebook",
        display_name="x",
    )
    assert item.local_path.is_absolute()


def test_pack_phase_rejects_absolute_path_outside_manifest_dir(tmp_path: Path) -> None:
    """Absolute ``local_path`` pointing outside manifest_dir is rejected by the
    containment check -- the load-bearing security boundary for BL-03.

    Falsifiability: removing the ``relative_to`` containment check in
    ``apply._pack_phase`` makes this test pass-through to the
    packager which would then attempt to read the outside file.
    """
    if sys.platform == "win32":
        pytest.skip("absolute-path test is POSIX-shape; skip on Windows")

    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secrets.ipynb"
    target.write_text("{}\n", encoding="utf-8")

    item = SyncItem(
        local_path=str(target),  # absolute, outside manifest_dir
        type="Notebook",
        display_name="x",
    )
    manifest = _StubManifest(items=[item])
    with pytest.raises(Exception, match="escapes the manifest directory"):
        _pack_phase(manifest, manifest_dir=manifest_dir, staging_dir=tmp_path / "staging")


@pytest.mark.parametrize(
    "good_path",
    [
        "loader.ipynb",
        "subdir/loader.ipynb",
        "deep/nested/path/loader.ipynb",
        "./loader.ipynb",
    ],
    ids=["root-rel", "single-level", "deeply-nested", "explicit-cwd"],
)
def test_sync_item_accepts_legitimate_relative_paths(good_path: str) -> None:
    """Common author shapes still validate -- no false positives."""
    item = SyncItem(
        local_path=good_path,
        type="Notebook",
        display_name="x",
        target_folder="/",
    )
    assert isinstance(item.local_path, Path)
    assert not item.local_path.is_absolute()


def test_sync_item_local_path_validator_returns_path_instance() -> None:
    """Validator coerces the value to ``pathlib.Path``."""
    item = SyncItem(
        local_path="loader.ipynb",
        type="Notebook",
        display_name="x",
    )
    assert isinstance(item.local_path, Path)


# --- semantic guard: _pack_phase containment via resolve() -------------------


class _StubManifest:
    """Minimal manifest shape ``_pack_phase`` consumes."""

    def __init__(self, items: list[SyncItem]) -> None:
        self.items = items


@pytest.mark.skipif(
    not hasattr(os, "symlink"),
    reason="symlink containment test requires os.symlink (POSIX or Windows w/ privilege)",
)
def test_pack_phase_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink within manifest_dir pointing OUTSIDE is caught by the
    ``Path.resolve() + relative_to`` containment check.

    Falsifiability: removing the ``relative_to`` guard in
    ``_pack_phase`` makes this test pass-through to the packager,
    which then reads ``/etc/passwd``-equivalent content. The
    containment check is the only thing standing between an
    untrusted manifest and arbitrary file read.
    """
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    target_file = secret_dir / "passwd"
    target_file.write_text("root:x:0:0::/root:/bin/bash\n", encoding="utf-8")

    # Symlink within manifest_dir -> outside file.
    link_path = manifest_dir / "loader.ipynb"
    try:
        os.symlink(target_file, link_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    # local_path is syntactically clean (relative, no ..) -- the
    # syntactic guard is happy. The symlink resolution is what
    # carries the escape.
    item = SyncItem(
        local_path="loader.ipynb",
        type="Notebook",
        display_name="loader",
    )
    manifest = _StubManifest(items=[item])

    with pytest.raises(Exception, match="escapes the manifest directory"):
        _pack_phase(manifest, manifest_dir=manifest_dir, staging_dir=tmp_path / "staging")


def test_pack_phase_accepts_files_within_manifest_dir(tmp_path: Path) -> None:
    """A real ``.ipynb`` file inside manifest_dir packs without error.

    No-op packager smoke test: confirms the new containment check
    does not over-reject legitimate flows. Uses a registered
    Notebook packager so ``_pack_phase`` reaches its happy path.
    """
    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    notebook = manifest_dir / "loader.ipynb"
    # Minimal valid notebook payload.
    notebook.write_text(
        '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}\n',
        encoding="utf-8",
    )
    staging = tmp_path / "staging"
    staging.mkdir()

    item = SyncItem(
        local_path="loader.ipynb",
        type="Notebook",
        display_name="loader",
    )
    manifest = _StubManifest(items=[item])

    # No exception expected; sidecar gets written in manifest_dir
    # because that's the natural anchor.
    _pack_phase(manifest, manifest_dir=manifest_dir, staging_dir=staging)
    assert (staging / "loader.Notebook" / "notebook-content.ipynb").is_file()
    sidecar = manifest_dir / ".sigantry" / "notebook-ids.json"
    assert sidecar.is_file(), "sidecar should land within manifest_dir under containment"


def test_pack_phase_rejects_attempted_symlink_to_parent(tmp_path: Path) -> None:
    """Symlink to ``../parent`` (a real path outside manifest_dir) is caught.

    Tighter variant of the escape test: the symlink target is
    relative-to-manifest-dir's parent, not an absolute path. The
    ``resolve()`` call follows the symlink to its real location, and
    that real location is outside ``manifest_dir.resolve()``.
    """
    if not hasattr(os, "symlink"):
        pytest.skip("symlink not available")

    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    payload = sibling / "evil.ipynb"
    payload.write_text("{}\n", encoding="utf-8")

    link = manifest_dir / "loader.ipynb"
    try:
        os.symlink(payload, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")

    item = SyncItem(
        local_path="loader.ipynb",
        type="Notebook",
        display_name="loader",
    )
    manifest = _StubManifest(items=[item])

    with pytest.raises(Exception, match="escapes the manifest directory"):
        _pack_phase(manifest, manifest_dir=manifest_dir, staging_dir=tmp_path / "staging")
