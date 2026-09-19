"""Phase 16 audit-plane records (Plan 16-02 + 16-03).

Houses ``SecretChangeRecord`` and ``ApprovalRecord`` -- frozen pydantic v2
models written through ``sigantry_core/governance/audit.py``'s
``emit_secret_change_record()`` + ``emit_approval_record()`` functions
(sibling jsonl files: ``secret_changes.jsonl``, ``approvals.jsonl``).

These are observation-plane records, NOT release records -- DeployRecord
(release plane) lives in ``sigantry_core/release/record.py``. The split
keeps `release/` scoped to release-record concerns and `governance/`
scoped to observation-plane (audit) concerns.

Per RESEARCH §3 correction: CONTEXT D-16/D-17 references a non-existent
``sigantry_core/release/audit.py``; the correct home for SecretChangeRecord +
ApprovalRecord is HERE (``sigantry_core/governance/records.py``).

Wave 0 (Plan 16-00) shipped this file as an empty stub. Plan 16-02 lands
``SecretChangeRecord``; Plan 16-03 lands ``ApprovalRecord``.

Anti-pattern guard: ``SecretChangeRecord`` deliberately does NOT carry a
``value`` field. ``model_config = ConfigDict(extra='forbid')`` makes a
pydantic ValidationError fire at construction time if a caller tries to
slip a ``value`` kwarg through -- the audit log records WHAT changed,
WHO changed it, WHEN, and a content-hash over the metadata. It NEVER
records the secret's plaintext (RESEARCH §Anti-Patterns line 427).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from sigantry_core.protocols import ApprovalOutcome

_CANONICAL_KWARGS: Final[dict[str, Any]] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}


class SecretChangeRecord(BaseModel):
    """Audit record for a single ``SecretStore.set`` / ``.delete`` operation.

    Mirrors ``DeployRecord``'s frozen + ``extra='forbid'`` shape (Plan 11-02)
    so the canonical-JSON ``audit_hash`` invariant is preserved across the
    audit plane.

    Fields
    ------
    operation : Literal["set", "delete"]
        Which mutating operation was invoked. Read operations
        (``get`` / ``list_keys`` / ``ping``) are NOT audited (RESEARCH
        §Anti-Patterns + Security domain V6: read-frequency leakage is
        out-of-scope for this seam).
    key : str
        The secret name (e.g. ``"STRIPE_API_KEY"``). NEVER the value.
    store_name : str
        The ``SecretStore.name`` class attribute of the impl that handled
        the call (``"key_vault"`` / ``"github_secrets"`` /
        ``"ado_variable_group"``). Audit-readers use this to triangulate
        which backend a secret lives in.
    actor : str
        Principal that initiated the change. Either an explicit
        caller-supplied identity (``"user@corp.com"`` /
        ``"app-2f7a-managed-identity"``) or the credential-class string
        from the underlying ``TokenProvider`` (e.g.
        ``"DefaultAzureCredential"``); ``"unknown"`` if the impl cannot
        infer one.
    timestamp : datetime
        Timezone-aware UTC timestamp of the mutation.
    audit_hash : str
        SHA-256 hex digest over the canonical JSON of every other field.
        Populated via :meth:`with_hash`; never set by the constructor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: Literal["set", "delete"]
    key: str
    store_name: str
    actor: str
    timestamp: datetime
    # Audit-2026-05-07 W3.1: chain link to the previous record on the
    # same JSONL ledger. ``None`` on the first record only; from the
    # second record onward the writer (``emit_secret_change_record``)
    # populates this with the prior record's ``audit_hash``. The field
    # is part of ``canonical_payload`` so tampering with the chain
    # invalidates the next record's ``audit_hash``.
    prev_hash: str | None = None
    audit_hash: str = ""

    def canonical_payload(self) -> bytes:
        """JSON canonicalisation of every field except ``audit_hash``.

        ``prev_hash`` IS included so chain-link tampering is detected
        by :meth:`verify_hash` (Audit-2026-05-07 W3.1). Output is UTF-8
        bytes with sorted keys and minimal separators -- identical to
        the input fed into :func:`hashlib.sha256` by both
        :meth:`with_hash` and :meth:`verify_hash`. Mirrors the
        :class:`DeployRecord` canonicalisation byte-for-byte (Plan 11-02).
        """
        payload = self.model_dump(mode="json")
        payload.pop("audit_hash", None)
        return json.dumps(payload, **_CANONICAL_KWARGS).encode("utf-8")

    def with_hash(self) -> SecretChangeRecord:
        """Return a copy with ``audit_hash`` recomputed from the payload.

        Always overwrites any constructor-supplied seed value -- calling
        ``.with_hash()`` on a record that already carries an
        ``audit_hash`` is the canonical re-canonicalisation path.
        """
        digest = hashlib.sha256(self.canonical_payload()).hexdigest()
        return self.model_copy(update={"audit_hash": digest})

    def verify_hash(self) -> bool:
        """Re-derive the hash and compare against the stored value.

        Returns ``True`` iff the stored ``audit_hash`` matches a fresh
        recomputation. The recomputation excludes ``audit_hash`` itself,
        so a tampered ``audit_hash`` is detected because the recomputation
        reflects the un-tampered remaining fields.
        """
        recomputed = hashlib.sha256(self.canonical_payload()).hexdigest()
        return recomputed == self.audit_hash


class ApprovalRecord(BaseModel):
    """Audit record for a single ``ApprovalGate.wait`` terminal outcome.

    Mirrors :class:`SecretChangeRecord`'s frozen + ``extra='forbid'``
    pattern so the canonical-JSON ``audit_hash`` invariant is preserved
    across the audit plane.

    Fields
    ------
    request_id : str
        ``ApprovalGate.request()`` identity. ADO supplies the approval id
        directly (the YAML pipeline emits the value); GitHub uses the
        run id; OPA generates a uuid4 because OPA has no native request
        id.
    release_id : str
        Identifier of the deploy release the approval gates.
    env : str
        Target environment (``"dev"`` / ``"preprod"`` / ``"prod"``).
    approvers : list[str]
        UPNs / display names that the gate notified.
    outcome : ApprovalOutcome
        ``"approved"`` / ``"rejected"`` / ``"timeout"`` literal from
        :data:`sigantry_core.protocols.ApprovalOutcome`.
    decided_by : str | None
        Principal that approved or rejected the request
        (``"alice@corp.com"`` / ``"opa-policy"``). ``None`` on timeout
        because no human ever decided.
    decided_at : datetime | None
        UTC datetime of the terminal decision. ``None`` on timeout for
        the same reason.
    last_observed_status : str | None
        Upstream status at the LAST poll before the gate returned. The
        signal that distinguishes "we gave up waiting"
        (``last_observed_status="pending"`` + ``outcome="timeout"``)
        from "ADO rejected the request" -- RESEARCH §Pitfall 4.
    audit_hash : str
        SHA-256 hex digest over the canonical JSON of every other field.
        Populated via :meth:`with_hash`; never set by the constructor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    release_id: str
    env: str
    approvers: list[str]
    outcome: ApprovalOutcome
    decided_by: str | None = None
    decided_at: datetime | None = None
    last_observed_status: str | None = None
    # Audit-2026-05-07 W3.1: chain link (see SecretChangeRecord.prev_hash).
    prev_hash: str | None = None
    audit_hash: str = ""

    def canonical_payload(self) -> bytes:
        """JSON canonicalisation of every field except ``audit_hash``.

        Output is UTF-8 bytes with sorted keys and minimal separators --
        identical to :meth:`SecretChangeRecord.canonical_payload` so the
        audit-plane invariant matches across record types.
        """
        payload = self.model_dump(mode="json")
        payload.pop("audit_hash", None)
        return json.dumps(payload, **_CANONICAL_KWARGS).encode("utf-8")

    def with_hash(self) -> ApprovalRecord:
        """Return a copy with ``audit_hash`` recomputed from the payload."""
        digest = hashlib.sha256(self.canonical_payload()).hexdigest()
        return self.model_copy(update={"audit_hash": digest})

    def verify_hash(self) -> bool:
        """Re-derive the hash and compare against the stored value.

        Returns ``True`` iff the stored ``audit_hash`` matches a fresh
        recomputation. The recomputation excludes ``audit_hash`` itself
        so a tampered ``audit_hash`` is detected because the
        recomputation reflects the un-tampered remaining fields.
        """
        recomputed = hashlib.sha256(self.canonical_payload()).hexdigest()
        return recomputed == self.audit_hash


class DestructiveOpRecord(BaseModel):
    """Audit record for a single ``@destructive_op``-decorated invocation.

    Audit-2026-05-07 W3.1 (Wave 1 re-audit follow-up): pre-W3.1 the
    decorator only emitted a structured logger event -- whether the
    record reached disk depended on the operator's logging config.
    Adding this record + ``emit_destructive_op_record`` JSONL writer
    brings destructive-op auditing into parity with the other three
    audit kinds (DeployRecord, SecretChangeRecord, ApprovalRecord), so
    a misconfigured logger no longer eats the audit trail.

    Mirrors the other audit records' frozen + ``extra='forbid'`` shape
    so the canonical-JSON ``audit_hash`` invariant is preserved across
    the audit plane. ``prev_hash`` chains the destructive-op ledger
    (W3.1) so deletion / reordering tampering is detectable.

    Fields
    ------
    resource_kind : str
        First positional argument to ``@destructive_op`` (e.g.
        ``"workspace"``, ``"capacity"``, ``"deploy"``).
    action : str
        Second positional argument (e.g. ``"delete"``, ``"pause"``,
        ``"rollback"``).
    resource_id : str | None
        The id that appears in the audit (workspace id, capacity id,
        release id). ``None`` when the wrapped function did not pass
        ``resource_id=`` as a keyword argument.
    principal : str
        Caller-supplied identity (UPN / app id) or the credential class
        name read off ``kwargs["token_provider"]``. ``"unknown"`` when
        the decorator could infer nothing.
    runbook_id : str | None
        Incident reference (mandatory for ``capacity.pause`` /
        ``capacity.resume``); the decorator rejects calls that omit
        it for those two actions.
    outcome : Literal["succeeded", "failed"]
        ``"succeeded"`` when the wrapped function returned normally;
        ``"failed"`` when it raised. The decorator emits the audit
        record on the failure path TOO so a 5xx after a partial DELETE
        still leaves an audit trail (Audit-2026-05-07 W1.8 invariant).
    exc_type : str | None
        Exception class name on the failure path (e.g.
        ``"AuthError"``); ``None`` on success.
    correlation_id : str | None
        Phase-2 correlation id read off ``get_correlation_id()`` at
        emit time so the audit row joins to the request log.
    timestamp : datetime
        Timezone-aware UTC timestamp of the decorator's emit call.
    prev_hash : str | None
        Chain link to the previous record on
        ``destructive_ops.jsonl``. ``None`` on the chain head only.
    audit_hash : str
        SHA-256 hex digest over the canonical JSON of every other field.
        Populated via :meth:`with_hash`; never set by the constructor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_kind: str
    action: str
    resource_id: str | None = None
    principal: str
    runbook_id: str | None = None
    outcome: Literal["succeeded", "failed"]
    exc_type: str | None = None
    correlation_id: str | None = None
    timestamp: datetime
    prev_hash: str | None = None
    audit_hash: str = ""

    def canonical_payload(self) -> bytes:
        """JSON canonicalisation of every field except ``audit_hash``."""
        payload = self.model_dump(mode="json")
        payload.pop("audit_hash", None)
        return json.dumps(payload, **_CANONICAL_KWARGS).encode("utf-8")

    def with_hash(self) -> DestructiveOpRecord:
        """Return a copy with ``audit_hash`` recomputed from the payload."""
        digest = hashlib.sha256(self.canonical_payload()).hexdigest()
        return self.model_copy(update={"audit_hash": digest})

    def verify_hash(self) -> bool:
        """Re-derive the hash and compare against the stored value."""
        recomputed = hashlib.sha256(self.canonical_payload()).hexdigest()
        return recomputed == self.audit_hash


__all__ = ["ApprovalRecord", "DestructiveOpRecord", "SecretChangeRecord"]
