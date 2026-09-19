"""Work-item provider implementations + shared payload formatter.

Plan 11-04 shipped :class:`AdoWorkItemProvider`; Plan 11-05 ships
:class:`GithubWorkItemProvider`. Plan 11-02 shipped the shared comment
formatter (``sigantry_core.workitems._payload``) so both providers post
byte-identical comment payloads (TRACE-06 invariant).
"""

from __future__ import annotations

from sigantry_core.workitems.ado import AdoWorkItemProvider
from sigantry_core.workitems.github import GithubWorkItemProvider

__all__: tuple[str, ...] = ("AdoWorkItemProvider", "GithubWorkItemProvider")
