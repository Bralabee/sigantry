"""PR-Provider Protocol seam + value-objects (Plan 14-05 Task 1).

Audit-2026-05-07 W2.5 lift: the canonical Protocol + value-objects now
live in :mod:`sigantry_core.protocols` as :class:`PrReviewBot`,
:class:`PullRequest`, and :class:`ChangedFile` -- matching the reserved
``sigantry.pr_review_bots`` registry group name and joining the
canonical 11-protocol surface that ``FabricDataOps`` widens to in W2.4.

This module re-exports them under the legacy ``Provider`` alias so
existing callers (the AdoProvider / GithubProvider impls + their
contract tests) keep working unchanged.

The Protocol mirrors Phase 11's
:class:`sigantry_core.protocols.WorkItemProvider` shape: a runtime-
checkable :class:`typing.Protocol` paired with frozen
:func:`dataclasses.dataclass` value-objects. Each concrete provider
COMPOSES :class:`sigantry_core.client.base.BaseRestClient` rather than
subclassing it (per RESEARCH §Pattern 2 + Phase 11 Pattern 1) so the
banned-API gate stays green: providers route all HTTP through the
single client front door.

Source patterns:
    - sigantry_core/protocols.py (Phase 11 WorkItemProvider Protocol shape)
    - sigantry_core/workitems/__init__.py (Phase 11 re-export pattern)
    - 14-RESEARCH.md §Pattern 2 (full Provider Protocol code)
"""

from __future__ import annotations

from sigantry_core.protocols import ChangedFile, PrReviewBot, PullRequest

# Legacy alias kept for back-compat with the Phase 14 callers. New code
# should import :class:`PrReviewBot` directly from
# :mod:`sigantry_core.protocols`.
Provider = PrReviewBot


__all__ = ["ChangedFile", "Provider", "PullRequest"]
