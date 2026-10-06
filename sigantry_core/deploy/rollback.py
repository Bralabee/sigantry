"""Rollback by release id.

Publishes again the items a recorded ``DeployRecord`` names
(``fabric_items_changed``), against the workspace the record names. It is
a forward deploy through fabric-cicd's ``items_to_include`` selective
publish (an experimental feature; both feature flags are appended at
module import).

The record holds item names only, not content and not a commit: the
content published is read from ``repository_directory``, so the caller
checks out the source to restore first. fabric-cicd publishes only the
item types in ``item_type_in_scope``, so a named item of another type is
skipped without an error. Items created after the release stay in place.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NoReturn

from fabric_cicd import append_feature_flag

# Pitfall 1 mitigation: items_to_include is silently no-op without BOTH
# experimental feature flags. Append at module import time so any
# downstream import path that calls rollback_to_release sees them set.
append_feature_flag("enable_experimental_features")
append_feature_flag("enable_items_to_include")

import json  # noqa: E402

from sigantry_core.deploy.core import DeployResult, deploy_workspace  # noqa: E402
from sigantry_core.governance.audit import destructive_op  # noqa: E402
from sigantry_core.governance.audit_io import _DEFAULT_AUDIT_DIR  # noqa: E402
from sigantry_core.release.ledger import find_by_release_id  # noqa: E402
from sigantry_core.release.record import DeployRecord  # noqa: E402

logger = logging.getLogger("sigantry_core.deploy.rollback")


def _raise_missing_or_tampered(release_id: str, *, audit_dir: Path | None) -> NoReturn:
    """Raise a ValueError that distinguishes 'never recorded' from 'tampered'.

    ``find_by_release_id`` iterates ``iter_records``, which LOGS-AND-SKIPS any
    line that fails ``verify_hash`` -- so a tampered target record never surfaces
    as a hit; it degrades to a plain "not found". A strict re-read (parse every
    line in file order, do NOT skip verification-failing records) tells the two
    apart, so a corrupt/hand-edited ledger line is diagnosed as tampering -- the
    condition ``sigantry release verify`` would name -- rather than sending the
    operator to hunt a mistyped release id.
    """
    base = audit_dir if audit_dir is not None else _DEFAULT_AUDIT_DIR
    path = Path(base) / "deploys.jsonl"
    tampered = False
    unparseable = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = DeployRecord(**json.loads(line))
                except Exception:
                    unparseable += 1
                    continue
                if rec.release_id == release_id and not rec.verify_hash():
                    tampered = True
    if tampered:
        raise ValueError(
            f"Release {release_id!r} is present in the deploy ledger but FAILS "
            f"audit_hash verification -- refusing to rollback against a tampered "
            f"ledger entry. Run `sigantry release verify` to inspect the chain."
        )
    if unparseable:
        raise ValueError(
            f"No verifiable release {release_id!r} in the deploy ledger, and it "
            f"has {unparseable} unparseable line(s) -- the target may be corrupt. "
            f"Run `sigantry release verify` to inspect the chain."
        )
    raise ValueError(
        f"No release {release_id!r} in deploy ledger. "
        f"Run `sigantry release list` to see recorded releases."
    )


@destructive_op("deploy", "rollback")
def rollback_to_release(
    release_id: str,
    workspace: str,
    *,
    repository_directory: str,
    environment: str,
    item_type_in_scope: list[str],
    parameters_path: str | None = None,
    dry_run: bool = False,
    audit_dir: Path | None = None,
    force: bool,
    runbook_id: str | None = None,
    principal: str | None = None,
) -> DeployResult:
    """Publish again the items a recorded release names.

    The rollback overwrites those items in the workspace with the content
    in ``repository_directory``, so it is destructive in the same class as
    ``delete_workspace`` / ``delete_folder``. The ``@destructive_op`` gate
    refuses unless ``force=True`` is passed as a keyword, and records the
    call (``outcome=succeeded`` on the clean path, ``outcome=failed`` with
    ``exc_type`` on an exception). ``runbook_id`` is recommended.

    Args:
        release_id: The DeployRecord.release_id to roll back to.
        workspace: Target Fabric workspace GUID (must match
            DeployRecord.workspace; cross-workspace rollback is rejected).
        repository_directory: Source tree containing the .platform items;
            the content published is read from here. It should hold every
            item the DeployRecord names.
        environment: Parameter-file environment key.
        item_type_in_scope: fabric-cicd's item types in scope. A named
            item of another type is not published.
        parameters_path: Override path to parameters.yml.
        dry_run: If True, log the planned rollback and return a zero-count
            DeployResult without touching the workspace.
        audit_dir: Override the default ~/.sigantry/audit/ directory.
        force: REQUIRED. ``force=True`` acknowledges the destructive
            replay; the decorator refuses without it.
        runbook_id: Optional incident reference (``ROLL-INC-...``).
        principal: Optional best-effort identity of the operator.

    Raises:
        ValueError: release not found / hash verification failed /
            workspace mismatch.

    Returns:
        DeployResult with item counts and the dependency-graph DOT path.
    """
    record = find_by_release_id(release_id, audit_dir=audit_dir)
    if record is None:
        # A miss via iter_records could mean "never recorded" OR "present but
        # tamper-failed" -- iter_records skips the latter, so both previously
        # surfaced the misleading "No release ..." message, sending an operator
        # to hunt a mistyped id when the real condition was a corrupt ledger
        # line. Strict re-read distinguishes the two and names tampering when it
        # is the cause (what `sigantry release verify` would report). NoReturn.
        _raise_missing_or_tampered(release_id, audit_dir=audit_dir)
    # Defence-in-depth: iter_records already filters hash-failing records, so on
    # the normal path this guard is unreachable (the diagnostic above handles a
    # tampered target). It is retained deliberately: if a future change ever let
    # a tamper-failed record reach here, refuse rather than roll back against it.
    if not record.verify_hash():
        raise ValueError(
            f"Release {release_id!r} failed audit_hash verification -- "
            f"refuse to rollback against a tampered ledger entry."
        )
    if record.workspace != workspace:
        raise ValueError(
            f"Release {release_id!r} was recorded against workspace "
            f"{record.workspace!r}; refusing to apply to {workspace!r}. "
            f"Cross-workspace rollback is not supported."
        )
    if not record.fabric_items_changed:
        logger.warning(
            "rollback no-op release=%s -- recorded fabric_items_changed is empty",
            release_id,
        )
        return DeployResult(
            workspace_id=workspace,
            environment=environment,
            items_published=0,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )
    if dry_run:
        logger.info(
            "rollback_dry_run release=%s items=%s",
            release_id,
            record.fabric_items_changed,
        )
        return DeployResult(
            workspace_id=workspace,
            environment=environment,
            items_published=0,
            items_failed=0,
            orphans_unpublished=0,
            dot_graph_path=None,
        )
    logger.info(
        "rollback_apply release=%s items=%d",
        release_id,
        len(record.fabric_items_changed),
    )
    return deploy_workspace(
        workspace_id=workspace,
        repository_directory=repository_directory,
        environment=environment,
        item_type_in_scope=item_type_in_scope,
        parameters_path=parameters_path,
        items_to_include=record.fabric_items_changed,
    )


__all__ = ("rollback_to_release",)
