"""Every shipped ``sync.yml`` under ``templates/`` validates clean.

Parity guard mirroring ``test_workspace_example_validates.py``. The
consumer-facing scaffolds in ``templates/{starter,demo}/`` are the first
artefact a new adopter copies, so a manifest that does not parse against
``SyncManifest`` is a broken front door.

This gate exists because ``templates/demo/sync.yml`` shipped for months
using the pre-1.0 key names (``version`` / ``name`` / ``path``) while the
loader had moved to ``schema_version`` / ``display_name`` / ``local_path``.
``load_manifest`` rejected it with 18 violations. Nothing caught it: the
only test touching that file is
``tests/integration/demo/test_live_sync_apply.py``, which is (a) marked
``integration`` + ``sigantry_demo`` so it is deselected in the default run,
and (b) still a Wave-0 ``xfail`` stub that Plan 15-04 never populated. The
manifest therefore had no credential-free validation at all.

Discovery is by glob rather than a hardcoded path list so a manifest added
to a future scaffold is covered on arrival instead of silently unguarded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
_MANIFESTS = sorted(_TEMPLATES.rglob("sync.yml"))


def _rel(path: Path) -> str:
    return str(path.relative_to(_TEMPLATES.parent))


def test_discovery_found_at_least_one_manifest() -> None:
    """Guard against a vacuous glob.

    Without this, a rename of ``templates/`` (or of ``sync.yml``) would
    empty the parametrised set below and every manifest test would report
    green while asserting nothing.
    """
    assert _MANIFESTS, (
        f"no sync.yml found under {_TEMPLATES} -- the parametrised validation "
        f"tests below would be vacuous"
    )


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=_rel)
def test_shipped_sync_manifest_validates(manifest: Path) -> None:
    """``load_manifest`` parses the shipped manifest with no exception."""
    from sigantry_core.sync.manifest import load_manifest

    parsed = load_manifest(manifest)

    assert parsed.items, f"{_rel(manifest)} declares no items"


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=_rel)
def test_shipped_sync_manifest_item_paths_exist(manifest: Path, tmp_path: Path) -> None:
    """Every ``local_path`` resolves to a real source and packs cleanly via PACKAGER_REGISTRY.

    A manifest can validate against the schema while pointing at item
    folders that were renamed, an unsupported type (e.g. ``Lakehouse``),
    or a non-``.ipynb`` notebook path; ``sync apply`` would then fail at
    pack time for the adopter rather than at review time for us.
    """
    import shutil

    from sigantry_core.sync.apply import _pack_phase
    from sigantry_core.sync.manifest import load_manifest
    from sigantry_core.sync.packagers import PACKAGER_REGISTRY

    parsed = load_manifest(manifest)
    missing = [
        str(item.local_path)
        for item in parsed.items
        if not (manifest.parent / item.local_path).exists()
    ]
    assert not missing, f"{_rel(manifest)} references non-existent item paths: {missing}"

    unsupported = [item.type for item in parsed.items if item.type not in PACKAGER_REGISTRY]
    assert not unsupported, f"{_rel(manifest)} declares unsupported packager types: {unsupported}"

    scaffold_copy = tmp_path / "scaffold"
    shutil.copytree(manifest.parent, scaffold_copy)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _pack_phase(parsed.normalised(), scaffold_copy, staging_dir)
