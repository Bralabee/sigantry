"""sigantry_core.pr_bot.providers -- PR-Provider seam + implementations.

Plan 14-05 lands the Provider Protocol (``base.py``) plus ``GithubProvider``
(``github.py``) and ``AdoProvider`` (``ado.py``). Banned-API gate forbids
``import httpx`` outside ``sigantry_core/client/`` + 3 documented
exceptions; both providers compose :class:`BaseRestClient`.

Public surface:

- :class:`Provider`     -- runtime_checkable Protocol (the seam contract).
- :class:`PullRequest`  -- frozen value-object returned by ``get_pr``.
- :class:`ChangedFile`  -- frozen value-object returned by ``get_changed_files``.
- :class:`GithubProvider` -- PAT or App auth; composes BaseRestClient.
- :class:`AdoProvider`    -- TokenProvider chain against AZURE_DEVOPS_SCOPE.
"""

from __future__ import annotations

from sigantry_core.pr_bot.providers.ado import AdoProvider
from sigantry_core.pr_bot.providers.base import (
    ChangedFile,
    Provider,
    PullRequest,
)
from sigantry_core.pr_bot.providers.github import GithubProvider

__all__ = [
    "AdoProvider",
    "ChangedFile",
    "GithubProvider",
    "Provider",
    "PullRequest",
]
