"""BootstrapRecord pydantic v2 model + deterministic audit hash.

Mirrors :class:`sigantry_core.release.record.DeployRecord` exactly --
same canonical-JSON hashing strategy, same millisecond-truncated
datetime, same frozen ``ConfigDict`` with ``extra="forbid"``. The audit
plane treats deploys and bootstraps symmetrically: each has its own
JSONL ledger but both verify-without-trust under the same algorithm.

Hash algorithm (lifted verbatim from ``release/record.py`` so a verifier
implemented for one record type works for the other):

- ``audit_hash`` is SHA-256 over a canonical JSON serialisation
  (``sort_keys=True``, ``separators=(',', ':')``, ``ensure_ascii=False``,
  UTF-8) of every field except the hash itself.
- ``audit_hash`` MUST be populated via :meth:`with_hash` rather than
  passed to the constructor.

Bootstrap-specific fields capture the workspace materialisation outcome:
which steps fired (vs were already-converged no-ops), the resolved
folder list, and the optional Git target. The record is appended to
``~/.sigantry/audit/bootstraps.jsonl`` exactly once per
``sigantry workspace bootstrap`` invocation that reaches step-5
completion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CANONICAL_KWARGS: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}

#: Default audit-ledger path. Mirrors ``release/record.py``'s
#: ``~/.sigantry/audit/deploys.jsonl`` convention.
DEFAULT_BOOTSTRAP_LEDGER: Final[Path] = Path.home() / ".sigantry" / "audit" / "bootstraps.jsonl"


#: Outcome of a single bootstrap step. ``created`` and ``reconnected``
#: indicate the toolkit took action; ``already-converged`` indicates
#: probe-before-act found the desired state already in place (idempotent
#: re-run case).
StepOutcome = Literal["created", "already-converged", "reconnected", "skipped"]


class BootstrapRecord(BaseModel):
    """Audit-plane record of a single Sigantry workspace bootstrap.

    Fields
    ------
    workspace_id : str
        Fabric workspace GUID resolved post-creation. The operator-supplied
        ``workspace.name`` lives in ``workspace_name`` so renames are visible.
    workspace_name : str
        Display name as recorded in ``workspace.yml`` at bootstrap time.
    stage : str
        Stage marker (DEV / TEST / PROD / FEATURE / etc.). Free-form on the
        record side; the operator-facing schema in ``bootstrap.py`` enforces
        the conventional set.
    capacity_id : str
        Fabric capacity GUID the workspace is bound to.
    blueprint : str
        Blueprint catalog key (e.g. ``minimal_starter``) or ``"explicit"``
        when the operator passed an explicit folder list.
    folders_created : list[str]
        Folder display names the bootstrap actually created on this run
        (excludes already-converged ones). Empty on idempotent re-run.
    folders_present : list[str]
        Full set of folders present on the workspace at bootstrap completion
        -- the union of ``folders_created`` + already-converged folders.
    git_target : dict[str, str] | None
        Resolved Git binding (``organization_name`` / ``project_name`` /
        ``repository_name`` / ``branch_name`` / ``directory_name``) or
        ``None`` when ``git.enabled=false`` in the config.
    step_outcomes : dict[str, str]
        Map of step-name -> outcome. Keys are the canonical step names
        (``workspace`` / ``capacity`` / ``folders`` / ``git`` / ``initialize``);
        values are :data:`StepOutcome` literals as plain strings (Pydantic
        cannot serialise Literal types into JSONL while preserving stable
        ordering, so we widen to str at the storage edge).
    operator : str
        Identity (UPN, email, login) that ran the bootstrap. Mirrors
        ``DeployRecord.approver`` semantics.
    audit_hash : str
        SHA-256 hex digest over the canonical JSON of every other field.
        Populated by :meth:`with_hash`; never set by the constructor.
    created_at : datetime
        Timezone-aware UTC timestamp, truncated to millisecond precision
        for round-trip determinism (see ``release/record.py`` Pitfall 8
        rationale).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str
    workspace_name: str
    stage: str
    capacity_id: str
    blueprint: str
    folders_created: list[str] = Field(default_factory=list)
    folders_present: list[str] = Field(default_factory=list)
    git_target: dict[str, str] | None = None
    step_outcomes: dict[str, str] = Field(default_factory=dict)
    operator: str
    # Audit-2026-05-07 W3.1: chain link (see DeployRecord.prev_hash).
    prev_hash: str | None = None
    audit_hash: str = ""
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _truncate_to_millis(cls, value: datetime) -> datetime:
        """Reject naive datetimes; truncate microseconds to millisecond grid."""
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC recommended)")
        micros = (value.microsecond // 1000) * 1000
        return value.replace(microsecond=micros)

    def canonical_payload(self) -> bytes:
        """JSON canonicalisation of every field except ``audit_hash``."""
        d = self.model_dump(mode="json")
        d.pop("audit_hash", None)
        return json.dumps(d, **_CANONICAL_KWARGS).encode("utf-8")

    def with_hash(self) -> BootstrapRecord:
        """Return a copy with ``audit_hash`` recomputed from the payload."""
        digest = hashlib.sha256(self.canonical_payload()).hexdigest()
        return self.model_copy(update={"audit_hash": digest})

    def verify_hash(self) -> bool:
        """Re-derive the hash and compare against the stored value."""
        recomputed = hashlib.sha256(self.canonical_payload()).hexdigest()
        return recomputed == self.audit_hash


def emit_bootstrap_record(record: BootstrapRecord, *, audit_dir: Path | str | None = None) -> Path:
    """Append ``record`` (with hash + chain link) to the bootstrap ledger.

    Mirrors :func:`sigantry_core.release.record.emit_deploy_record`. The
    record is rebound with ``prev_hash`` set to the previous record's
    ``audit_hash`` (Audit-2026-05-07 W3.1), then sealed via
    ``with_hash()`` so the chain link enters the canonical payload. A
    direct caller cannot accidentally write an unchained record because
    this function ignores any constructor-supplied ``prev_hash``.

    Args:
        record: The :class:`BootstrapRecord` to append. Need not have
            ``audit_hash`` or ``prev_hash`` populated -- this function
            recomputes both unconditionally.
        audit_dir: Override the audit directory. Used in tests + hermetic
            CI runners. Defaults to ``~/.sigantry/audit/``.

    Returns:
        The :class:`Path` to the JSONL file the record was appended to.
    """
    from sigantry_core.governance.audit_io import audit_chain_lock, read_last_audit_hash

    target = (
        Path(audit_dir) / "bootstraps.jsonl" if audit_dir is not None else DEFAULT_BOOTSTRAP_LEDGER
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    # Audit-2026-05-07 review follow-up (BL-02): serialise W3.1 chain
    # linkage. The read+seal+append sequence below shares the audit-
    # ledger race condition that was patched in
    # ``write_audit_record``; using the same flock helper keeps the
    # bootstrap ledger consistent with the deploy / approval / secret /
    # destructive-op ledgers under multi-writer load.
    with audit_chain_lock(target):
        prev = read_last_audit_hash(target)
        sealed = record.model_copy(update={"prev_hash": prev}).with_hash()
        line = json.dumps(sealed.model_dump(mode="json"), **_CANONICAL_KWARGS) + "\n"
        with target.open("a", encoding="utf-8") as fp:
            fp.write(line)
    return target


__all__ = [
    "DEFAULT_BOOTSTRAP_LEDGER",
    "BootstrapRecord",
    "StepOutcome",
    "emit_bootstrap_record",
]
