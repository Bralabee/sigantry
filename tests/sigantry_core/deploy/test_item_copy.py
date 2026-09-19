"""Unit tests for sigantry_core.deploy.item_copy (DEPLOY-02, Pitfall 3)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from sigantry_core.deploy import ItemCopyError, copy_item


def _make_item(
    root: Path,
    *,
    logical_id: str,
    display_name: str,
    item_type: str = "Lakehouse",
    with_crlf: bool = False,
) -> Path:
    item = root
    item.mkdir(parents=True)
    platform = {
        "version": "2.0",
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/platform/platformProperties.json",
        "config": {"logicalId": logical_id},
        "metadata": {
            "type": item_type,
            "displayName": display_name,
            "description": "src",
        },
    }
    content = json.dumps(platform, indent=2)
    if with_crlf:
        content = content.replace("\n", "\r\n")
    (item / ".platform").write_bytes(content.encode("utf-8"))

    # A body file to round-trip (with an LF tail so CRLF rewrite has teeth).
    py = 'print("hi")\n'
    if with_crlf:
        py = py.replace("\n", "\r\n")
    (item / "notebook-content.py").write_bytes(py.encode("utf-8"))
    return item


def test_raises_when_src_missing_platform(tmp_path: Path) -> None:
    src = tmp_path / "NoPlatform"
    src.mkdir()
    (src / "random.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ItemCopyError, match=r"not a Fabric item folder|\.platform file"):
        copy_item(src, tmp_path / "dst", new_display_name="X")


def test_raises_when_dst_exists(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    dst = tmp_path / "existing"
    dst.mkdir()
    (dst / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(ItemCopyError, match="already exists"):
        copy_item(src, dst, new_display_name="Silver")
    # Sentinel not mutated
    assert (dst / "sentinel").read_text(encoding="utf-8") == "keep"


def test_regenerates_uuid(tmp_path: Path) -> None:
    old = "11111111-1111-1111-1111-111111111111"
    src = _make_item(tmp_path / "Bronze.Lakehouse", logical_id=old, display_name="Bronze")
    new_id = copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    assert new_id != old
    parsed = uuid.UUID(new_id)
    assert parsed.version == 4
    dst_platform = json.loads(
        (tmp_path / "Silver.Lakehouse" / ".platform").read_text(encoding="utf-8")
    )
    assert dst_platform["config"]["logicalId"] == new_id


def test_rewrites_display_name(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    dst_platform = json.loads(
        (tmp_path / "Silver.Lakehouse" / ".platform").read_text(encoding="utf-8")
    )
    assert dst_platform["metadata"]["displayName"] == "Silver"


def test_sets_new_description(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    copy_item(
        src,
        tmp_path / "Silver.Lakehouse",
        new_display_name="Silver",
        new_description="silver zone",
    )
    dst_platform = json.loads(
        (tmp_path / "Silver.Lakehouse" / ".platform").read_text(encoding="utf-8")
    )
    assert dst_platform["metadata"]["description"] == "silver zone"


def test_preserves_description_when_omitted(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    dst_platform = json.loads(
        (tmp_path / "Silver.Lakehouse" / ".platform").read_text(encoding="utf-8")
    )
    # Description from src ("src") must be preserved when new_description is None.
    assert dst_platform["metadata"]["description"] == "src"


def test_lf_enforcement_on_platform_and_py(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
        with_crlf=True,
    )
    # Sanity: source has CRLF.
    assert b"\r\n" in (src / ".platform").read_bytes()
    assert b"\r\n" in (src / "notebook-content.py").read_bytes()

    copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    dst_platform = (tmp_path / "Silver.Lakehouse" / ".platform").read_bytes()
    dst_py = (tmp_path / "Silver.Lakehouse" / "notebook-content.py").read_bytes()
    assert b"\r\n" not in dst_platform
    assert b"\r\n" not in dst_py


def test_preserves_binary_bytes(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    # Binary with \r\n bytes that must NOT be normalised.
    blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (src / "thumbnail.png").write_bytes(blob)
    copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    assert (tmp_path / "Silver.Lakehouse" / "thumbnail.png").read_bytes() == blob


def test_preserves_nested_dirs(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    (src / "sub").mkdir()
    (src / "sub" / "nested.json").write_text('{"a": 1}', encoding="utf-8")
    copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    assert (tmp_path / "Silver.Lakehouse" / "sub" / "nested.json").is_file()
    assert json.loads(
        (tmp_path / "Silver.Lakehouse" / "sub" / "nested.json").read_text(encoding="utf-8")
    ) == {"a": 1}


def test_returned_uuid_is_v4(tmp_path: Path) -> None:
    src = _make_item(
        tmp_path / "Bronze.Lakehouse",
        logical_id="11111111-1111-1111-1111-111111111111",
        display_name="Bronze",
    )
    new_id = copy_item(src, tmp_path / "Silver.Lakehouse", new_display_name="Silver")
    assert uuid.UUID(new_id).version == 4


def test_public_surface_exports() -> None:
    """copy_item + ItemCopyError are part of sigantry_core.deploy's public API."""
    from sigantry_core import deploy as deploy_pkg

    assert "copy_item" in deploy_pkg.__all__
    assert "ItemCopyError" in deploy_pkg.__all__
