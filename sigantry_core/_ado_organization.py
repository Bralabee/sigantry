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


class AdoOrganizationError(ValueError):
    """The value is a URL, but not an Azure DevOps Services collection URL."""


def ado_organization_name(value: str) -> str:
    """Return the organization name in ``value``, a name or a collection URL.

    A value with no ``://`` is taken to be the name and comes back unchanged.
    A ``https://dev.azure.com/<org>/...`` or ``https://<org>.visualstudio.com/...``
    URL gives ``<org>``.

    Raises:
        AdoOrganizationError: ``value`` is a URL on any other host, such as an
            Azure DevOps Server collection, which the ``dev.azure.com`` base
            these clients use cannot reach, or it names no organization. The
            message carries the host only, never the rest of the URL, which may
            hold credentials.
    """
    if "://" not in value:
        return value
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
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
