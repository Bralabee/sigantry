"""Destructive-op gate (Audit-2026-05-07 W2.6 split).

Owns the ``@destructive_op`` decorator and the ``DestructiveOpError``
exception. Carved out of :mod:`sigantry_core.governance.audit` so the
gate is independently importable and testable. The audit module
re-exports both names for backwards-compatibility.

Every write that deletes or takes offline a user-visible resource
decorates its entry point with ``@destructive_op(resource_kind, action)``.
The decorator:

1. Rejects the call if ``force=True`` is not explicitly passed as a
   keyword arg.
2. For ``(resource_kind, action)`` in
   ``{("capacity","pause"), ("capacity","resume")}``, additionally rejects
   the call if ``runbook_id`` is missing or empty (Pitfall 11).
3. Emits a single structured log record on the
   ``sigantry_core.governance.audit`` logger (matching the pre-split
   emitter name so log consumers don't notice the move). The record
   carries principal + resource + action + timestamp + correlation_id +
   runbook_id + force, and NEVER contains a token or Authorization header
   value.

Audit-2026-05-07 W1.8 invariant: the audit record is emitted EVEN ON
EXCEPTION via a try/except wrapper -- a failed delete that issued the
DELETE then received a 5xx mutated the workspace AND must leave an audit
trail. The ``outcome`` field distinguishes ``"succeeded"`` from
``"failed"``; failures also record the exception class name.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from sigantry_core.auth import TokenProvider
from sigantry_core.client.logging import get_correlation_id

# Logger name preserved across the W2.6 split so JSONL log consumers /
# dashboard parsers / journald filters keyed on
# ``sigantry_core.governance.audit`` keep working unchanged.
logger = logging.getLogger("sigantry_core.governance.audit")

F = TypeVar("F", bound=Callable[..., Any])


class DestructiveOpError(RuntimeError):
    """Raised when a destructive operation is attempted without force=True
    or without a required runbook_id (capacity pause/resume only)."""


_REQUIRES_RUNBOOK: frozenset[tuple[str, str]] = frozenset(
    {
        ("capacity", "pause"),
        ("capacity", "resume"),
    }
)


def destructive_op(
    resource_kind: str,
    action: str,
    *,
    resource_arg: str | tuple[str, ...] | None = None,
) -> Callable[[F], F]:
    """Gate a destructive operation behind force=True + structured audit log.

    Wrapped function contract:
      - keyword-only ``force: bool`` is MANDATORY at call site (not defaulted).
      - keyword-only ``runbook_id: str | None`` is MANDATORY for capacity pause/resume.
      - keyword-only ``principal: str | None = None`` -- caller-supplied appId/UPN;
        if None, decorator tries kwargs["token_provider"].last_credential_class("probe").
      - keyword-only ``resource_id: str | None = None`` -- the id that appears in the audit.
      - keyword-only ``token_provider: TokenProvider | None = None`` -- optional;
        used only for principal inference when ``principal`` is absent.

    Audit-2026-05-08 review follow-up (BL-01): when ``resource_arg`` is
    set the decorator falls back to the wrapped function's *named*
    parameter as the audit ``resource_id`` if the caller did not pass
    ``resource_id`` explicitly. ``resource_arg`` may be a single
    parameter name (``resource_arg="workspace_id"``) or a tuple of
    names joined with ``/`` (``resource_arg=("workspace_id",
    "principal_id")``) for compound-key resources like role
    assignments. This closes the BL-01 hole where five public
    ``delete_*`` verbs accepted ``resource_id`` purely as decorator
    plumbing then discarded it via ``_ = resource_id or X``, leaving
    the audit JSONL with ``resource_id=null`` for every operator
    workflow that did not explicitly pass the kwarg (the common
    case). With ``resource_arg`` set, the wired arg is read via
    ``inspect.signature(fn).bind_partial`` regardless of whether the
    caller passed it positionally or by keyword.

    Raises:
      DestructiveOpError -- before the wrapped function runs -- if force!=True
      or (for capacity pause/resume) runbook_id is missing/empty.
    """

    def decorator(fn: F) -> F:
        # Cache the wrapped function's signature so resource-arg lookup
        # at call time is a dict access, not a re-parse on every call.
        sig: inspect.Signature | None = inspect.signature(fn) if resource_arg is not None else None

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            force = kwargs.get("force", False)
            runbook_id = kwargs.get("runbook_id")
            resource_id = kwargs.get("resource_id")
            if resource_id is None and sig is not None and resource_arg is not None:
                resource_id = _resolve_resource_id_from_args(sig, resource_arg, args, kwargs)
            principal = kwargs.get("principal") or _infer_principal(kwargs)

            if force is not True:
                raise DestructiveOpError(
                    f"{resource_kind}.{action} requires force=True "
                    f"(explicit keyword). No audit record emitted."
                )
            if (resource_kind, action) in _REQUIRES_RUNBOOK and not runbook_id:
                raise DestructiveOpError(
                    f"{resource_kind}.{action} requires runbook_id "
                    f"(incident reference, e.g. 'INC-1234'). "
                    f"No audit record emitted."
                )

            # Audit-2026-05-07 W1.8 + W3.1: emit audit record EVEN ON
            # EXCEPTION, and persist to disk via emit_destructive_op_record
            # (not just the structured logger). The disk write goes through
            # the same prev_hash-chained ledger writer as the other three
            # audit kinds, so a misconfigured operator logger no longer
            # eats the audit trail.
            from sigantry_core.governance.audit import emit_destructive_op_record
            from sigantry_core.governance.records import DestructiveOpRecord

            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                logger.warning(
                    "destructive_op",
                    extra={
                        "event": "destructive_op",
                        "resource_kind": resource_kind,
                        "action": action,
                        "resource_id": resource_id,
                        "principal": principal,
                        "force": True,
                        "runbook_id": runbook_id,
                        "outcome": "failed",
                        "exc_type": type(exc).__name__,
                        "timestamp": datetime.now(tz=UTC).isoformat(),
                        "correlation_id": get_correlation_id(),
                    },
                )
                try:
                    emit_destructive_op_record(
                        DestructiveOpRecord(
                            resource_kind=resource_kind,
                            action=action,
                            resource_id=resource_id,
                            principal=principal,
                            runbook_id=runbook_id,
                            outcome="failed",
                            exc_type=type(exc).__name__,
                            correlation_id=get_correlation_id(),
                            timestamp=datetime.now(tz=UTC),
                        )
                    )
                except OSError:
                    # Audit-plane disk failure on the failure path: log
                    # and re-raise the original exception. We must not
                    # mask the wrapped function's exception with an
                    # audit-write OSError.
                    logger.warning(
                        "destructive_op_audit_write_failed",
                        exc_info=True,
                    )
                raise

            logger.info(
                "destructive_op",
                extra={
                    "event": "destructive_op",
                    "resource_kind": resource_kind,
                    "action": action,
                    "resource_id": resource_id,
                    "principal": principal,
                    "force": True,
                    "runbook_id": runbook_id,
                    "outcome": "succeeded",
                    "timestamp": datetime.now(tz=UTC).isoformat(),
                    "correlation_id": get_correlation_id(),
                },
            )
            # Wave 3 re-audit FU-1: wrap the success-path JSONL write in
            # the SAME try/except as the failure path so an audit-disk
            # outage does not mask the wrapped function's successful
            # return. Symmetric with the failure-path handling above.
            # The structured logger event already fired, so the audit
            # trail still exists in journald / the operator's log
            # backend; only the durable JSONL write is best-effort.
            try:
                emit_destructive_op_record(
                    DestructiveOpRecord(
                        resource_kind=resource_kind,
                        action=action,
                        resource_id=resource_id,
                        principal=principal,
                        runbook_id=runbook_id,
                        outcome="succeeded",
                        correlation_id=get_correlation_id(),
                        timestamp=datetime.now(tz=UTC),
                    )
                )
            except OSError:
                # Audit-plane disk failure on the success path: log and
                # swallow. The wrapped function returned normally; we
                # must not turn a successful op into a failed one
                # because the audit ledger could not be flushed.
                logger.warning(
                    "destructive_op_audit_write_failed",
                    exc_info=True,
                )
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def _infer_principal(kwargs: dict[str, Any]) -> str:
    """Read credential class name from an optional TokenProvider kwarg.

    Never reads the token itself -- ``last_credential_class(scope)`` returns
    only the class name (e.g. 'ManagedIdentityCredential').
    """
    tp = kwargs.get("token_provider")
    if not isinstance(tp, TokenProvider):
        return "unknown"
    cred = tp.last_credential_class("probe") or "unknown"
    return cred


def _resolve_resource_id_from_args(
    sig: inspect.Signature,
    resource_arg: str | tuple[str, ...],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str | None:
    """Look up the audit ``resource_id`` from a wrapped function's bound args.

    Audit-2026-05-08 review follow-up (BL-01) helper. Uses
    :meth:`inspect.Signature.bind_partial` so the lookup works whether
    the caller passed the resource id positionally or as a keyword.
    Returns ``None`` if the named parameter is absent (e.g. the caller
    omitted it entirely) so the audit record reads ``resource_id=null``
    rather than fabricating a sentinel.

    For compound resources (e.g. an RBAC role assignment identified by
    ``(workspace_id, principal_id)``) ``resource_arg`` may be a tuple
    of parameter names; missing-but-not-None values are silently
    dropped before the join so a partial bind still yields a useful id.
    """
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except TypeError:
        return None
    if isinstance(resource_arg, str):
        value = bound.arguments.get(resource_arg)
        return str(value) if value is not None else None
    parts: list[str] = []
    for name in resource_arg:
        value = bound.arguments.get(name)
        if value is not None:
            parts.append(str(value))
    return "/".join(parts) if parts else None


__all__ = ["DestructiveOpError", "destructive_op"]
