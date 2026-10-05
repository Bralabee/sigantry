"""Where a packager stages an item, checked to stay inside the staging tree.

Both packagers build ``<staging_dir>/<target_folder>/<folder_name>`` from
values that come out of a manifest. The manifest validator rejects a
``display_name`` that is a path, but a packager can also be called
directly (it is a public seam), and :mod:`pathlib` discards everything to
the left of an absolute right-hand operand. So the joined path is checked
once more here, after symlinks are resolved, before anything is created.
"""

from __future__ import annotations

from pathlib import Path


def staged_item_dir(
    staging_dir: Path,
    target_folder: str,
    folder_name: str,
    *,
    packager: str,
) -> Path:
    """Return ``<staging_dir>/<target_folder>/<folder_name>``.

    ``staging_dir`` must already be resolved. Raises :class:`ValueError`
    when the result is not strictly inside ``staging_dir`` once resolved,
    which is the case for an absolute ``folder_name``, a ``..`` segment, or
    a path through a symlink that points out of the staging tree.
    """
    sub_path = target_folder.lstrip("/").lstrip("\\")
    target_dir = staging_dir / sub_path / folder_name if sub_path else staging_dir / folder_name
    resolved = target_dir.resolve()
    if resolved == staging_dir or not resolved.is_relative_to(staging_dir):
        raise ValueError(
            f"{packager}.pack: refusing to stage {folder_name!r} under target folder "
            f"{target_folder!r}: the path resolves to {resolved}, which is outside the "
            f"staging directory {staging_dir}"
        )
    return target_dir
