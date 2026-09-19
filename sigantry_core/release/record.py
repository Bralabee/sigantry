"""DeployRecord pydantic v2 model + deterministic audit hash.

The record is the cryptographic anchor of the audit plane:

- ``audit_hash`` is SHA-256 over a canonical JSON serialisation
  (``sort_keys=True``, ``separators=(',', ':')``, ``ensure_ascii=False``,
  UTF-8) of every field except the hash itself.
- A reader can verify-without-trust by recomputing the hash from the
  remaining fields and comparing.
- The model is frozen (``ConfigDict(frozen=True)``) and rejects extra
  fields (``extra="forbid"``) — defensive against future drift in the
  audit-record schema.

Datetime determinism (Pitfall 8 in 11-RESEARCH.md): ``created_at`` is
truncated to millisecond precision so two records constructed at the same
wall-clock millisecond hash identically. Without this, a record that
round-trips through JSON (which loses sub-millisecond microseconds in some
serialisers) would fail ``verify_hash()`` against its on-disk twin.

The ``audit_hash`` MUST be populated via ``.with_hash()`` rather than
passed to the constructor. The constructor accepts a seed value for
ergonomics (e.g. JSON round-tripping), but ``.with_hash()`` always
overwrites it. Constructor-set hashes would otherwise include themselves
in the canonical input by mistake.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CANONICAL_KWARGS: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}


class DeployRecord(BaseModel):
    """Audit-plane record of a single Sigantry release.

    Fields
    ------
    workspace : str
        Fabric workspace identifier (id or name) the release targets.
    release_id : str
        Caller-assigned release identifier (e.g. ``R-2026-04-26-1``).
    work_items : list[str]
        ADO / GitHub work-item identifiers linked to this release.
    fabric_items_changed : list[str]
        Fabric items (notebooks, lakehouses, pipelines) modified by the
        release. Stored as ``"<name>.<type>"`` strings.
    test_evidence : dict[str, str]
        Free-form evidence map: e.g. ``{"smoke": "passed"}``.
    approver : str
        Identity (UPN, email, login) that approved the release.
    audit_hash : str
        SHA-256 hex digest over the canonical JSON of every other field.
        Populated by :meth:`with_hash`; never set by the constructor.
    created_at : datetime
        Timezone-aware UTC timestamp, truncated to millisecond precision.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace: str
    release_id: str
    work_items: list[str] = Field(default_factory=list)
    fabric_items_changed: list[str] = Field(default_factory=list)
    test_evidence: dict[str, str] = Field(default_factory=dict)
    approver: str
    # Audit-2026-05-07 W3.1: chain link to the previous record on the
    # ``deploys.jsonl`` ledger. ``None`` on the first record only; the
    # writer (``emit_deploy_record``) populates this on subsequent
    # records. Included in ``canonical_payload`` so chain-link tampering
    # is detected by ``verify_hash``.
    prev_hash: str | None = None
    audit_hash: str = ""
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _truncate_to_millis(cls, value: datetime) -> datetime:
        """Reject naive datetimes; truncate microseconds to millisecond grid.

        Pydantic's ``model_dump(mode='json')`` serialises datetimes with
        microsecond precision, but a record reconstructed from JSON (or
        from another tool's serialisation) may have lost the trailing 3
        digits of microseconds. Truncating both on input and on
        serialisation guarantees hash determinism across round-trips.
        """
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC recommended)")
        micros = (value.microsecond // 1000) * 1000
        return value.replace(microsecond=micros)

    def canonical_payload(self) -> bytes:
        """JSON canonicalisation of every field except ``audit_hash``.

        Output is UTF-8 bytes with sorted keys and minimal separators —
        identical to the input fed into :func:`hashlib.sha256` by both
        :meth:`with_hash` and :meth:`verify_hash`.
        """
        d = self.model_dump(mode="json")
        d.pop("audit_hash", None)
        return json.dumps(d, **_CANONICAL_KWARGS).encode("utf-8")

    def with_hash(self) -> DeployRecord:
        """Return a copy with ``audit_hash`` recomputed from the payload.

        Always overwrites any constructor-supplied seed value — calling
        ``.with_hash()`` on a record that already carries an
        ``audit_hash`` is the canonical re-canonicalisation path.
        """
        digest = hashlib.sha256(self.canonical_payload()).hexdigest()
        return self.model_copy(update={"audit_hash": digest})

    def verify_hash(self) -> bool:
        """Re-derive the hash and compare against the stored value.

        Returns ``True`` iff the stored ``audit_hash`` matches a fresh
        recomputation. The recomputation uses :meth:`canonical_payload`
        which excludes the hash field itself, so a tampered ``audit_hash``
        is detected because the recomputation reflects the un-tampered
        remaining fields.
        """
        recomputed = hashlib.sha256(self.canonical_payload()).hexdigest()
        return recomputed == self.audit_hash
