"""The tenant a command pins, and how a command stops when the pin refuses.

Every command that gets an Azure token for Fabric, Power BI, ARM, Graph or
Purview takes ``--tenant-id`` and resolves it with :func:`resolve_tenant_id`:

1. ``--tenant-id``, when given and not blank. The settings are not loaded.
2. Otherwise ``core.tenant_id`` from :func:`~sigantry_core.config.load_settings`
   (``[core] tenant_id`` in ``.sigantry.toml``, ``SIGANTRY_CORE__TENANT_ID``,
   or the legacy file and ``FDT_`` names, in the order the loader ranks them),
   loaded with its warnings dropped (:mod:`sigantry_core._cli_settings`). A
   blank value counts as unset.
3. Neither: ``None``, no pin, and the credential chain is used as it is.

The value must be a tenant ID GUID
(:func:`sigantry_core.auth.tenant.require_tenant_guid`). Settings that cannot
be loaded stop the command when no ``--tenant-id`` is given: they may carry
the pin, so running unpinned would ignore it.

:func:`stops_on_tenant_error` and :func:`exit_on_tenant_error` turn a refusal
(:class:`TenantMismatchError`, :class:`InvalidTenantIdError`,
:class:`TenantSettingsError`, also when another exception wraps it, as
fabric-cicd's ``TokenError`` does) into one line on stderr and the command's
own exit code, with no traceback.
"""

from __future__ import annotations

import functools
import tomllib
from collections.abc import Callable
from typing import Any, NoReturn, TypeVar

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError

from sigantry_core._cli_settings import load_settings_for_cli
from sigantry_core.auth.errors import InvalidTenantIdError, TenantMismatchError
from sigantry_core.auth.tenant import require_tenant_guid

F = TypeVar("F", bound=Callable[..., Any])

#: ``--help`` text of every ``--tenant-id`` that :func:`resolve_tenant_id` resolves.
TENANT_ID_HELP = (
    "Entra tenant ID (GUID) to pin: a token from any other tenant is refused. "
    "Default: core.tenant_id in the settings."
)

#: What loading the settings raises for a file or environment it cannot use.
_SETTINGS_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    UnicodeDecodeError,
    tomllib.TOMLDecodeError,
    ValidationError,
    SettingsError,
)


class TenantSettingsError(Exception):
    """The settings could not be loaded, so the tenant to pin is unknown."""


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def resolve_tenant_id(flag: str | None) -> str | None:
    """Return the tenant a command pins: the flag, else ``core.tenant_id``, else ``None``.

    Raises:
        InvalidTenantIdError: the value is not a tenant ID GUID.
        TenantSettingsError: no ``--tenant-id`` and the settings could not be loaded.
    """
    if flag is not None and flag.strip():
        return require_tenant_guid(flag, source="--tenant-id")
    try:
        settings = load_settings_for_cli()
    except _SETTINGS_ERRORS as exc:
        raise TenantSettingsError(
            f"the settings could not be loaded ({type(exc).__name__}: {_one_line(str(exc))}), "
            "so the tenant to pin from core.tenant_id is unknown: pass --tenant-id, or fix "
            "the settings file"
        ) from exc
    configured = settings.core.tenant_id
    if configured is None or not str(configured).strip():
        return None
    return require_tenant_guid(str(configured), source="core.tenant_id")


_TENANT_ERRORS = (TenantMismatchError, InvalidTenantIdError, TenantSettingsError)


def find_tenant_error(exc: BaseException) -> Exception | None:
    """Return the tenant refusal ``exc`` is or wraps (``__cause__`` / ``__context__``)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, _TENANT_ERRORS):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def tenant_error_line(error: Exception) -> str:
    """The one line a command prints for a tenant refusal; never contains a token."""
    text = _one_line(str(error))
    remediation = getattr(error, "remediation", None)
    if remediation:
        text = f"{text}. {_one_line(remediation)}"
    return text


def exit_on_tenant_error(exc: BaseException, *, exit_code: int, prefix: str = "sigantry") -> None:
    """Stop the command if ``exc`` is or wraps a tenant refusal; otherwise return.

    For a command's own ``except Exception`` handler, so the refusal is
    reported as one line rather than inside the handler's message.
    """
    error = find_tenant_error(exc)
    if error is not None:
        _refuse(error, exit_code=exit_code, prefix=prefix, cause=exc)


def _refuse(error: Exception, *, exit_code: int, prefix: str, cause: BaseException) -> NoReturn:
    typer.echo(f"{prefix}: error: {tenant_error_line(error)}", err=True)
    raise typer.Exit(code=exit_code) from cause


def stops_on_tenant_error(*, exit_code: int) -> Callable[[F], F]:
    """Decorate a command so a tenant refusal ends it with ``exit_code``.

    ``typer.Exit`` raised by the command passes through untouched.
    """

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except typer.Exit:
                raise
            except Exception as exc:
                exit_on_tenant_error(exc, exit_code=exit_code)
                raise

        return wrapper  # type: ignore[return-value]

    return decorate


__all__ = [
    "TENANT_ID_HELP",
    "TenantSettingsError",
    "exit_on_tenant_error",
    "find_tenant_error",
    "resolve_tenant_id",
    "stops_on_tenant_error",
    "tenant_error_line",
]
