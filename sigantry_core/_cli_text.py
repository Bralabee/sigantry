"""Hand data to Rich as text, never as markup.

Rich parses ``[...]`` in a ``str`` it prints -- or puts in a table cell, a
column header or a title -- as markup, and ``:name:`` as an emoji code. Data
that reaches such a ``str`` (an item name, a path, an option value, an
exception message) can raise ``MarkupError`` (``[/old]``) or lose text
(``[draft]``). A :class:`~rich.text.Text` is not parsed.

CLI modules pass data through :func:`plain_text`, or build a line with
``Text.assemble((label, style), f" {value}")``, whose plain ``str`` parts
are not parsed either. ``tests/sigantry_core/test_cli_markup_safety.py``
checks every module that prints through Rich for data reaching a markup sink.
"""

from __future__ import annotations

from rich.text import Text


def plain_text(value: object, style: str = "") -> Text:
    """``value`` as a :class:`~rich.text.Text` Rich prints as it is."""
    return Text(str(value), style=style)


__all__ = ("plain_text",)
