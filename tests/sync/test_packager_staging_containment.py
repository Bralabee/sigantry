"""A staged item is always created inside the staging directory.

Two layers, each tested on its own:

- ``SyncItem.display_name`` is one name, not a path: ``/`` and ``\\`` are
  rejected, and with them every absolute path.
- Both packagers check the joined ``<staging>/<target_folder>/<name>.<Type>``
  after resolving it, because a packager can be called directly with a
  ``display_name`` that never went through the manifest validator. The
  check comes before the logicalId sidecar is read or minted, so a refused
  pack writes nothing anywhere, the source tree included, whether or not the
  caller passed a ``logical_id``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from sigantry_core.sync.manifest import SyncItem
from sigantry_core.sync.packagers import GenericPackager, NotebookPackager

_LOGICAL_ID = "00000000-0000-4000-8000-000000000001"


def _tree(root: Path) -> list[str]:
    """Every path under ``root`` (symlinks not followed), relative and sorted."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


@pytest.mark.parametrize(
    "display_name",
    [
        "/abs/escape",
        "a/b",
        "a\\b",
        "\\\\server\\share\\x",
        "\\rooted",
        "/",
    ],
)
def test_display_name_that_is_a_path_is_rejected(display_name: str) -> None:
    with pytest.raises(ValidationError, match="path separator"):
        SyncItem(local_path=Path("nb.ipynb"), type="Notebook", display_name=display_name)


def test_absolute_display_name_of_this_platform_is_rejected(tmp_path: Path) -> None:
    absolute = str(tmp_path / "ESCAPED")
    assert Path(absolute).is_absolute()
    with pytest.raises(ValidationError, match="path separator"):
        SyncItem(local_path=Path("nb.ipynb"), type="Notebook", display_name=absolute)


@pytest.mark.parametrize("display_name", ["Benign", "Sales Report 2026", "nb_load-raw"])
def test_plain_display_name_is_accepted(display_name: str) -> None:
    item = SyncItem(local_path=Path("nb.ipynb"), type="Notebook", display_name=display_name)
    assert item.display_name == display_name


def _sources(tmp_path: Path) -> dict[str, tuple[object, Path, str]]:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "report.json").write_text("{}", encoding="utf-8")
    nb = tmp_path / "nb.ipynb"
    nb.write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    return {
        "generic": (GenericPackager(item_type="Report"), src_dir, "Report"),
        "notebook": (NotebookPackager(), nb, "Notebook"),
    }


@pytest.mark.parametrize("logical_id", [_LOGICAL_ID, None], ids=["given-id", "minted-id"])
@pytest.mark.parametrize("which", ["generic", "notebook"])
@pytest.mark.parametrize(
    ("label", "target_folder"),
    [("absolute", "/"), ("parent", "/"), ("parent-in-folder", "/sub")],
)
def test_packager_refuses_a_name_that_resolves_outside_staging(
    tmp_path: Path, which: str, label: str, target_folder: str, logical_id: str | None
) -> None:
    packager, source, item_type = _sources(tmp_path)[which]
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "outside"
    display_name = {
        "absolute": str(outside / "ESCAPED"),
        "parent": "../ESCAPED",
        "parent-in-folder": "../../ESCAPED",
    }[label]
    before = _tree(tmp_path)

    with pytest.raises(ValueError, match="outside the staging directory"):
        packager.pack(  # type: ignore[attr-defined]
            source,
            display_name=display_name,
            target_folder=target_folder,
            logical_id=logical_id,
            staging_dir=staging,
        )

    # Nothing was created anywhere: not at the absolute target, not next to
    # staging, not in staging, and not in the source tree (no sidecar with a
    # minted logicalId either).
    assert _tree(tmp_path) == before
    assert not (outside / f"ESCAPED.{item_type}").exists()
    assert not (tmp_path / f"ESCAPED.{item_type}").exists()
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("logical_id", [_LOGICAL_ID, None], ids=["given-id", "minted-id"])
@pytest.mark.parametrize("which", ["generic", "notebook"])
def test_packager_refuses_a_target_folder_symlinked_out_of_staging(
    tmp_path: Path, which: str, logical_id: str | None
) -> None:
    """A directory inside staging that is a symlink to elsewhere is not inside staging."""
    packager, source, _item_type = _sources(tmp_path)[which]
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, staging / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")
    before = _tree(tmp_path)

    with pytest.raises(ValueError, match="outside the staging directory"):
        packager.pack(  # type: ignore[attr-defined]
            source,
            display_name="Benign",
            target_folder="/linked",
            logical_id=logical_id,
            staging_dir=staging,
        )
    assert list(outside.iterdir()) == []
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("which", ["generic", "notebook"])
def test_packager_with_no_logical_id_mints_one_for_an_accepted_item(
    tmp_path: Path, which: str
) -> None:
    """Control: the sidecar is still written when the item is staged."""
    packager, source, item_type = _sources(tmp_path)[which]
    out = packager.pack(  # type: ignore[attr-defined]
        source,
        display_name="Benign",
        target_folder="/",
        logical_id=None,
        staging_dir=tmp_path / "staging",
    )
    sidecar = Path(source).parent / ".sigantry" / f"{item_type.lower()}-ids.json"
    minted = json.loads(sidecar.read_text(encoding="utf-8"))["ids"][Path(source).name]
    platform = json.loads((Path(out) / ".platform").read_text(encoding="utf-8"))
    assert platform["config"]["logicalId"] == minted


@pytest.mark.parametrize("which", ["generic", "notebook"])
@pytest.mark.parametrize("target_folder", ["/", "/raw/2026"])
def test_packager_stages_a_plain_name_inside_staging(
    tmp_path: Path, which: str, target_folder: str
) -> None:
    """Control: the check does not refuse an ordinary item."""
    packager, source, item_type = _sources(tmp_path)[which]
    staging = tmp_path / "staging"
    out = packager.pack(  # type: ignore[attr-defined]
        source,
        display_name="Benign",
        target_folder=target_folder,
        logical_id=_LOGICAL_ID,
        staging_dir=staging,
    )
    resolved_staging = staging.resolve()
    expected = resolved_staging.joinpath(*target_folder.strip("/").split("/"))
    assert Path(out) == expected / f"Benign.{item_type}"
    assert Path(out).resolve().is_relative_to(resolved_staging)
    assert (Path(out) / ".platform").is_file()
