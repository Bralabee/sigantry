"""Where a packager stages an item, checked to stay inside the staging tree.

Both packagers build ``<staging_dir>/<target_folder>/<folder_name>`` from
values that come out of a manifest. The manifest validator rejects a
``display_name`` that is a path, but a packager can also be called
directly (it is a public seam), and :mod:`pathlib` discards everything to
the left of an absolute right-hand operand. So the joined path is checked
once more here, with the rule ``sync pull`` also uses
(:func:`sigantry_core.sync._paths.contained_path`), before anything is
created. A packager calls this before it does anything else that writes,
its logicalId sidecar included.
"""

from __future__ import annotations

from pathlib import Path

from sigantry_core.sync._paths import contained_path


def staged_item_dir(
    staging_dir: Path,
    target_folder: str,
    folder_name: str,
    *,
    packager: str,
) -> Path:
    """Return ``<staging_dir>/<target_folder>/<folder_name>``, resolved.

    Raises :class:`ValueError` when the result is not strictly inside
    ``staging_dir`` once resolved, which is the case for an absolute
    ``folder_name``, a ``..`` segment, or a path through a symlink that
    points out of the staging tree. The resolved path is the one that was
    checked, so it is the one the packager creates.
    """
    sub_path = target_folder.lstrip("/").lstrip("\\")
    try:
        return contained_path(staging_dir, sub_path, folder_name, base_name="the staging directory")
    except ValueError as exc:
        raise ValueError(
            f"{packager}.pack: refusing to stage {folder_name!r} under target folder "
            f"{target_folder!r}: {exc}"
        ) from None
