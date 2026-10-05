"""The one rule for a directory or file named by a manifest or a workspace.

Three places join names that come from outside the program into a local
path: the packagers stage ``<staging>/<target_folder>/<name>.<Type>`` from a
manifest, ``sync pull`` writes ``<into>/<folder path>/<display name>/``
from a workspace listing, and each definition part from the service names
its own path under that. :func:`contained_path` is the check all three use,
and :func:`has_path_separator` is the "one name, not a path" rule that the
manifest validator and ``sync pull`` both apply to a display name.
"""

from __future__ import annotations

from pathlib import Path

#: A display name containing either of these is a path, whatever the platform.
PATH_SEPARATORS: tuple[str, ...] = ("/", "\\")


def has_path_separator(name: str) -> bool:
    """True when ``name`` contains ``/`` or ``\\`` (so it is not one name)."""
    return any(sep in name for sep in PATH_SEPARATORS)


def contained_path(base: Path, *parts: str | Path, base_name: str) -> Path:
    """Return ``base`` joined with ``parts``, resolved, if that is strictly inside ``base``.

    Both ends are resolved through symlinks before they are compared, and
    the resolved path is returned, so the caller creates and writes exactly
    the path that was checked. An absolute part, a ``..`` segment, or a
    symlink that points elsewhere all resolve outside ``base``; the result
    may not be ``base`` itself either.

    Args:
        base: the directory everything must stay inside.
        parts: the path segments to join under it.
        base_name: how the error message names ``base`` (e.g.
            ``"the staging directory"``).

    Raises:
        ValueError: the joined path cannot be resolved, or resolves to
            ``base`` itself or to somewhere outside it. Callers re-raise it
            as their own error type.
    """
    base_resolved = base.resolve()
    joined = base.joinpath(*parts)
    try:
        resolved = joined.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"the path {str(joined)!r} could not be resolved: {exc}") from exc
    if resolved == base_resolved:
        raise ValueError(f"the path resolves to {base_name} {base_resolved} itself")
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(
            f"the path resolves to {resolved}, which is outside {base_name} {base_resolved}"
        )
    return resolved
