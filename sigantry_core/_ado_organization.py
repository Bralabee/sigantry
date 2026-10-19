"""Read an Azure DevOps organization from a name or a collection URL.

An Azure DevOps pipeline hands over its organization as ``$(System.CollectionUri)``,
``https://dev.azure.com/<org>/`` (``https://<org>.visualstudio.com/`` on an older
organization), while a person types the bare name. Every ADO REST client in this
package builds ``https://dev.azure.com/<org>`` from it, so each one calls
:func:`ado_organization_name` first and accepts either form.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_SERVICES_HOST = "dev.azure.com"
_LEGACY_SUFFIX = ".visualstudio.com"
_NOT_IN_A_NAME = "/\\?#:@"


class AdoOrganizationError(ValueError):
    """The value is a URL, but not an Azure DevOps Services collection URL."""


def ado_organization_name(value: str) -> str:
    """Return the organization name in ``value``, a name or a collection URL.

    A value with no ``://`` is taken to be the name and comes back with its
    surrounding whitespace removed, provided it could be a name at all.
    A ``https://dev.azure.com/<org>/...`` or ``https://<org>.visualstudio.com/...``
    URL gives ``<org>``.

    Raises:
        AdoOrganizationError: ``value`` is empty, is not a name (it holds
            whitespace or one of ``/ \\ ? # : @``, as in ``dev.azure.com/contoso``),
            is not a parseable URL, or is a URL on any other host, such as an
            Azure DevOps Server collection, which the ``dev.azure.com`` base
            these clients use cannot reach, or it names no organization. No
            message carries any part of ``value`` beyond a host, and the error
            is raised outside the ``except`` so the parser's own message, which
            can quote a URL's credentials, is not kept as ``__context__``.
    """
    value = value.strip()
    if "://" not in value:
        if not value or any(c.isspace() or c in _NOT_IN_A_NAME for c in value):
            raise AdoOrganizationError(
                "an Azure DevOps organization name is not empty and holds no whitespace "
                f"or any of {_NOT_IN_A_NAME}; pass the name, or https://{_SERVICES_HOST}/<organization>/"
            )
        return value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        parse_failed = False
    except ValueError:
        parse_failed = True
    if parse_failed:
        raise AdoOrganizationError(
            f"that is not a parseable URL; pass the organization name, or https://{_SERVICES_HOST}/<organization>/"
        )
    if host == _SERVICES_HOST:
        segments = [s for s in parts.path.split("/") if s]
        if segments:
            return segments[0]
        raise AdoOrganizationError(
            f"{_SERVICES_HOST} URL names no organization; expected https://{_SERVICES_HOST}/<organization>/"
        )
    if host.endswith(_LEGACY_SUFFIX):
        organization = host[: -len(_LEGACY_SUFFIX)]
        if organization and "." not in organization:
            return organization
    raise AdoOrganizationError(
        f"{host or 'that URL'!r} is not an Azure DevOps Services collection URL. Pass the "
        f"organization name, or https://{_SERVICES_HOST}/<organization>/ "
        f"(Azure DevOps Server is not supported)."
    )
