"""Fabric item folder duplicator (DEPLOY-02).

Copies an item folder (e.g. ``Bronze.Lakehouse/``) to a new destination,
regenerating the ``logicalId`` and rewriting ``displayName`` per Microsoft
Learn ``source-code-format``:

    "if you're creating a new item by copying an existing item directory,
     you do need to change the logicalId and display name to something
     unique in the repository."

LF enforcement at write time eliminates the CRLF round-trip documented as
Pitfall 3 (Fabric silently corrupts items with CRLF line endings in
``.platform`` / sibling ``.py`` / ``.json`` files).

TODO(v2): ``--rename-in-content`` flag for per-item self-reference rewrites
(e.g. a notebook's markdown cell mentioning 'Bronze' by name). Plan 04-02
ships the core mechanic only; self-reference rewrites are v2 scope.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

# Text-type suffixes inside a Fabric item folder that get LF normalisation on
# write. Everything else (e.g. .png, .whl, .parquet) is copied byte-for-byte.
_LF_TARGET_SUFFIXES: tuple[str, ...] = (".platform", ".py", ".json")


class ItemCopyError(RuntimeError):
    """Raised when ``copy_item`` cannot proceed.

    Fires on:
      * source directory missing a ``.platform`` file (not a Fabric item folder), OR
      * destination path already exists (refuse to overwrite).
    """


def copy_item(
    src: str | Path,
    dst: str | Path,
    *,
    new_display_name: str,
    new_description: str | None = None,
) -> str:
    """Copy a Fabric item folder, regenerating ``logicalId`` and enforcing LF.

    Parameters
    ----------
    src:
        Source item folder (must contain a ``.platform`` file at its root).
    dst:
        Destination item folder (must NOT exist — refuses to clobber).
    new_display_name:
        Unique ``metadata.displayName`` for the copy (required).
    new_description:
        Optional new ``metadata.description``. When ``None`` (default), the
        source's description is preserved.

    Returns
    -------
    str
        The newly-generated UUIDv4 ``logicalId``.

    Raises
    ------
    ItemCopyError
        When ``src`` has no ``.platform`` or ``dst`` already exists.
    """
    src_p, dst_p = Path(src), Path(dst)

    platform_src = src_p / ".platform"
    if not platform_src.is_file():
        raise ItemCopyError(
            f"{src_p} does not contain a .platform file - not a Fabric item folder."
        )
    if dst_p.exists():
        raise ItemCopyError(f"Destination {dst_p} already exists; refuse to overwrite.")

    dst_p.mkdir(parents=True)
    new_logical_id = str(uuid.uuid4())

    # Walk the source tree; copy every file with LF normalisation on known
    # text types (.platform, .py, .json). Binary bytes preserved verbatim.
    for src_file in src_p.rglob("*"):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(src_p)
        target = dst_p / rel
        target.parent.mkdir(parents=True, exist_ok=True)

        data = src_file.read_bytes()
        if src_file.suffix in _LF_TARGET_SUFFIXES or src_file.name == ".platform":
            # Normalise CRLF and lone CR to LF.
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        target.write_bytes(data)

    # Rewrite dst/.platform with a fresh logicalId + new displayName.
    platform_dst = dst_p / ".platform"
    payload = json.loads(platform_dst.read_bytes().decode("utf-8"))
    payload.setdefault("config", {})["logicalId"] = new_logical_id
    metadata = payload.setdefault("metadata", {})
    metadata["displayName"] = new_display_name
    if new_description is not None:
        metadata["description"] = new_description
    out = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    platform_dst.write_bytes(out.encode("utf-8"))

    return new_logical_id
