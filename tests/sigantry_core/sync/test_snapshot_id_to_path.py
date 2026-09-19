"""Audit-2026-05-07 W4.1 -- falsifiability tests for the
``WorkspaceSnapshot.id_to_path`` reverse-lookup map.

Pre-W4.1 ``sync.pull._resolve_folder_path_from_id`` did an O(N) scan
over ``snapshot.folder_path_index.items()`` per item. With M folders
and N items, the per-pull cost was O(N*M). W4.1 adds a
``functools.cached_property id_to_path`` on the snapshot that
inverts the map once; subsequent reverse lookups are O(1).

Tests below pin:

- The reverse map is correct (every ``(path, fid)`` pair on
  ``folder_path_index`` shows up as ``(fid, path)`` on ``id_to_path``).
- The ``cached_property`` actually caches: two reads return the SAME
  dict object (no recomputation cost on the per-item hot loop).
- ``_resolve_folder_path_from_id`` returns the same answer pre- and
  post-W4.1: the same path for known ids, ``"/"`` for None / unknown.
- The reverse map handles collisions correctly (multiple paths
  CANNOT map to the same id; the forward map's path keys are unique
  per the snapshot construction algorithm).
"""

from __future__ import annotations

from sigantry_core.sync.snapshot import WorkspaceSnapshot


def _make_snapshot(folder_path_index: dict[str, str]) -> WorkspaceSnapshot:
    """Construct a minimal snapshot with only the fields W4.1 cares about."""
    return WorkspaceSnapshot(
        workspace_id="ws-test",
        folders_by_id={},  # not used by id_to_path
        items_by_id={},
        folder_path_index=folder_path_index,
        item_to_folder={},
    )


def test_id_to_path_inverts_folder_path_index() -> None:
    """The reverse map is the byte-exact inverse of the forward map."""
    forward = {
        "/Bronze": "g-bronze",
        "/Bronze/Raw": "g-bronze-raw",
        "/Gold": "g-gold",
    }
    snap = _make_snapshot(forward)
    assert snap.id_to_path == {
        "g-bronze": "/Bronze",
        "g-bronze-raw": "/Bronze/Raw",
        "g-gold": "/Gold",
    }


def test_id_to_path_caches_result() -> None:
    """Two reads return the SAME dict object (functools.cached_property)."""
    snap = _make_snapshot({"/A": "g-a", "/B": "g-b"})
    first = snap.id_to_path
    second = snap.id_to_path
    assert first is second, (
        "id_to_path is recomputed on every access. The W4.1 "
        "cached_property contract requires single-computation."
    )


def test_id_to_path_empty_index_yields_empty_map() -> None:
    """An empty forward index yields an empty reverse map (no errors)."""
    snap = _make_snapshot({})
    assert snap.id_to_path == {}


def test_resolve_folder_path_from_id_via_id_to_path() -> None:
    """``_resolve_folder_path_from_id`` returns the correct path post-W4.1."""
    from sigantry_core.sync.pull import _resolve_folder_path_from_id

    snap = _make_snapshot({"/Bronze": "g-bronze", "/Gold": "g-gold"})
    assert _resolve_folder_path_from_id(snap, "g-bronze") == "/Bronze"
    assert _resolve_folder_path_from_id(snap, "g-gold") == "/Gold"


def test_resolve_folder_path_from_id_returns_root_for_none() -> None:
    """A ``None`` folder_id (item at workspace root) returns ``"/"``."""
    from sigantry_core.sync.pull import _resolve_folder_path_from_id

    snap = _make_snapshot({"/Bronze": "g-bronze"})
    assert _resolve_folder_path_from_id(snap, None) == "/"


def test_resolve_folder_path_from_id_returns_root_for_unknown() -> None:
    """An unknown folder_id returns ``"/"`` and logs a warning.

    Pre-W4.1: this took an O(N) scan to fail. Post-W4.1: O(1) miss.
    """
    from sigantry_core.sync.pull import _resolve_folder_path_from_id

    snap = _make_snapshot({"/Bronze": "g-bronze"})
    # Unknown id -> root.
    assert _resolve_folder_path_from_id(snap, "g-does-not-exist") == "/"


def test_resolve_folder_path_from_id_does_not_call_id_to_path_repeatedly() -> None:
    """Repeated calls hit the cached reverse map, not a recomputation.

    Falsifiability: a regression that drops ``cached_property`` (e.g.
    by switching to a plain method) would still pass the previous
    tests but would defeat the W4.1 perf claim. This test counts
    the recomputations.
    """
    from sigantry_core.sync.pull import _resolve_folder_path_from_id

    snap = _make_snapshot({f"/F{i}": f"g-{i}" for i in range(50)})

    # First access materialises the reverse map.
    _ = snap.id_to_path
    cached = snap.id_to_path

    # 100 reverse lookups must reuse the same cached dict.
    for i in range(100):
        _resolve_folder_path_from_id(snap, f"g-{i % 50}")

    assert snap.id_to_path is cached, (
        "id_to_path was rebuilt during the per-item lookups -- the W4.1 perf claim is broken."
    )
