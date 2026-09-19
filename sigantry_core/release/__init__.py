"""Release-record subdomain -- DeployRecord + audit-hash flow + sigantry release CLI.

Public surface:

- :class:`DeployRecord` -- the pydantic v2 audit-record value object
  (Plan 11-02, TRACE-04) with deterministic ``audit_hash`` + ``with_hash`` /
  ``verify_hash`` helpers.
- :data:`release_app` -- the Typer subapp registered as the 13th top-level
  ``sigantry`` subapp (Plan 11-06, TRACE-05). Plan 12-03 added the
  read-side ledger module (``ledger.py``); Plan 12-03 also extended the
  CLI with ``list`` / ``show`` / ``diff`` subcommands alongside the
  existing ``record`` subcommand.
- :func:`iter_records`, :func:`find_by_release_id`, :func:`diff_records` --
  read-side ledger helpers over ``~/.sigantry/audit/deploys.jsonl``
  (Plan 12-03 / PIPELINE-04). The ledger is read-only; the only writer
  is :func:`sigantry_core.governance.audit.emit_deploy_record`.
"""

from __future__ import annotations

from sigantry_core.release.cli import release_app
from sigantry_core.release.ledger import (
    diff_records,
    find_by_release_id,
    iter_records,
)
from sigantry_core.release.record import DeployRecord

__all__ = (
    "DeployRecord",
    "diff_records",
    "find_by_release_id",
    "iter_records",
    "release_app",
)
