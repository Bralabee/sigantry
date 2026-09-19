"""EmailNotificationSink -- first-class NotificationSink reference impl (Plan 16-01).

Migrated from ``sigantry_core/sync/notifications.py::EmailSmtpSink``
(Phase 13 stand-in) and modernised to use stdlib
:class:`email.message.EmailMessage` (NOT the legacy
:class:`email.mime.text.MIMEText`) per 16-RESEARCH.md §Pitfall 6 -- the
``email.mime.*`` API is the legacy "compat32" subtree; new code should
exclusively use the policy-aware :class:`EmailMessage`.

This module does NOT import :mod:`httpx` -- email transport uses the
stdlib :mod:`smtplib`. STARTTLS is the default handshake (``use_tls=True``).

X-Priority header
-----------------

Each :class:`NotificationLevel` maps to an RFC 4356 X-Priority value:

==========  ==========
``level``   X-Priority
==========  ==========
critical    1 (highest)
error       2
warning     3 (normal)
info        5 (lowest)
==========  ==========

WR-07 / threat T-16-01-02: ``smtp_password`` is wrapped in
:class:`Secret` so ``__repr__`` returns ``***`` and pickling drops the
credential.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sigantry_core.protocols import NotificationEvent, Secret

logger = logging.getLogger("sigantry_core.notifications.email")

#: Default SMTP timeout (seconds).
_DEFAULT_SMTP_TIMEOUT: float = 30.0

#: Severity -> X-Priority header value (RFC 4356).
_X_PRIORITY: dict[str, str] = {
    "info": "5",
    "warning": "3",
    "error": "2",
    "critical": "1",
}


class EmailNotificationSink:
    """Send a NotificationEvent as an SMTP email.

    Uses :class:`email.message.EmailMessage` (modern policy-aware API);
    NEVER :class:`email.mime.text.MIMEText` per RESEARCH §Pitfall 6.
    STARTTLS handshake is the default (``use_tls=True``); set to
    ``False`` only for plaintext / non-TLS internal relays.
    """

    name: str = "email"

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: Secret | str | None = None,
        from_addr: str,
        default_recipients: list[str],
        use_tls: bool = True,
        timeout: float = _DEFAULT_SMTP_TIMEOUT,
    ) -> None:
        if not default_recipients:
            raise ValueError("EmailNotificationSink requires at least one default recipient")
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        # Wrap a raw-string password so __repr__ / pickle redact it.
        if smtp_password is None or isinstance(smtp_password, Secret):
            self._smtp_password = smtp_password
        else:
            self._smtp_password = Secret(value=smtp_password)
        self._from_addr = from_addr
        self._default_recipients = list(default_recipients)
        self._use_tls = use_tls
        self._timeout = timeout

    # Read-only public accessors (the constructor mutates / wraps inputs).
    @property
    def smtp_host(self) -> str:
        return self._smtp_host

    @property
    def smtp_port(self) -> int:
        return self._smtp_port

    @property
    def from_addr(self) -> str:
        return self._from_addr

    @property
    def default_recipients(self) -> list[str]:
        return list(self._default_recipients)

    @property
    def use_tls(self) -> bool:
        return self._use_tls

    @property
    def smtp_user(self) -> str | None:
        return self._smtp_user

    @property
    def smtp_password(self) -> Secret | None:
        # Only Secret or None survives constructor coercion.
        return self._smtp_password if isinstance(self._smtp_password, Secret) else None

    def __repr__(self) -> str:
        """T-16-01-02: redact credentials from default repr."""
        return (
            f"EmailNotificationSink(smtp_host={self._smtp_host!r}, "
            f"smtp_port={self._smtp_port}, from_addr={self._from_addr!r}, "
            f"default_recipients=[{len(self._default_recipients)} recipients], "
            f"use_tls={self._use_tls}, "
            f"smtp_user={'<set>' if self._smtp_user else None}, "
            f"smtp_password={'<redacted>' if self._smtp_password else None})"
        )

    def __getstate__(self) -> dict[str, object]:
        """Drop credentials from pickle output (WR-07)."""
        state = dict(self.__dict__)
        if "_smtp_password" in state:
            state["_smtp_password"] = None
        if "_smtp_user" in state:
            state["_smtp_user"] = None
        return state

    # ---------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------

    @staticmethod
    def _priority_for_level(level: str) -> str:
        return _X_PRIORITY.get(level, "5")

    def _build_message(self, event: NotificationEvent, *, recipients: list[str]) -> EmailMessage:
        """Construct an :class:`EmailMessage` envelope from the event.

        Uses ``EmailMessage`` exclusively -- never
        ``email.mime.text.MIMEText`` (RESEARCH §Pitfall 6).
        """
        msg = EmailMessage()
        msg["Subject"] = event.title
        msg["From"] = self._from_addr
        msg["To"] = ", ".join(recipients)
        msg["X-Priority"] = self._priority_for_level(event.level)
        body_lines: list[str] = [event.body]
        if event.properties:
            body_lines.append("")
            body_lines.append("Properties:")
            for k, v in event.properties.items():
                body_lines.append(f"  {k}: {v}")
        msg.set_content("\n".join(body_lines))
        return msg

    # ---------------------------------------------------------------
    # NotificationSink Protocol surface
    # ---------------------------------------------------------------

    def send(self, event: NotificationEvent, *, channel: str | None = None) -> None:
        """Build an :class:`EmailMessage` and send via STARTTLS-wrapped SMTP.

        ``channel`` is unused for email -- accepted for Protocol
        conformance. A non-None value emits a debug log line so
        operators see the misuse.
        """
        if channel is not None:
            logger.debug(
                "email_sink_ignoring_channel_kwarg channel=%r (email sinks have "
                "no concept of channel; recipients come from the constructor)",
                channel,
            )
        msg = self._build_message(event, recipients=self._default_recipients)
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as smtp:
                if self._use_tls:
                    smtp.starttls()
                if self._smtp_user and self._smtp_password is not None:
                    pwd = (
                        self._smtp_password.value
                        if isinstance(self._smtp_password, Secret)
                        else self._smtp_password
                    )
                    smtp.login(self._smtp_user, pwd)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning("email_smtp_send_failed err=%s", exc)

    def ping(self) -> None:
        """Round-trip an SMTP NOOP (no message send) to verify reachability.

        Performs the STARTTLS handshake + authentication if configured,
        then sends ``NOOP``. On :class:`smtplib.SMTPException` the
        underlying error propagates so operators see a typed failure.
        """
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._smtp_user and self._smtp_password is not None:
                pwd = (
                    self._smtp_password.value
                    if isinstance(self._smtp_password, Secret)
                    else self._smtp_password
                )
                smtp.login(self._smtp_user, pwd)
            smtp.noop()


__all__ = ["EmailNotificationSink"]
