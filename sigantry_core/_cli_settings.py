"""Settings loading for the ``sigantry`` commands.

:func:`sigantry_core.config.load_settings` reports the legacy config surface,
and the rules that keep sigantry 1.0.0's results, through Python warnings.
That suits library callers. A command has two problems with them, and 1.0.0
printed none of them:

- under ``PYTHONWARNINGS=error`` (or ``-W error``) a warning is raised as an
  exception, so a command that ran on 1.0.0 would stop with a traceback;
- printed, each warning is a line on stderr, and a pipeline step that fails
  on any stderr output (``failOnStderr`` in Azure Pipelines) would fail where
  it passed on 1.0.0.

So the commands record these warnings and drop them. Library callers still
get them; the CHANGELOG carries the migration notes.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sigantry_core.config import ToolkitSettings, load_settings


@contextmanager
def settings_warnings_dropped() -> Iterator[None]:
    """Record every warning raised inside the block, then drop it.

    ``simplefilter("always")`` sits in front of any filter the user set, so
    an ``error`` filter cannot turn one of these into an exception. The
    previous filters and ``showwarning`` come back when the block exits.
    """
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        yield


def load_settings_for_cli(path: str | Path | None = None) -> ToolkitSettings:
    """Return :func:`load_settings` with its warnings recorded and dropped.

    Exceptions from the loader propagate unchanged.
    """
    with settings_warnings_dropped():
        return load_settings(path)


__all__ = ["load_settings_for_cli", "settings_warnings_dropped"]
