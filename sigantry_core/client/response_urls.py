"""The one check for a URL that a response tells the client to follow.

Some responses name the next URL to request: an LRO ``Location`` header
(and its ``/result`` form once the operation succeeds), ARM's
``Azure-AsyncOperation`` header, Fabric's ``continuationUri`` and Power BI's
``@odata.nextLink``. The client follows each of them through
:meth:`BaseRestClient.send`, which attaches the bearer token. Every one of
those call sites passes the URL through :func:`check_response_url` first.

The rule: an absolute URL must use ``https``. Anything else is refused with
:class:`~sigantry_core.client.errors.ResponseUrlRefusedError` before a
request is built, so no token is sent. A relative URL (no scheme) is
resolved against the client's configured base URL and is returned as is.

The check is deliberately not in ``BaseRestClient._url``: the configured
base URL is operator configuration, and a local policy endpoint such as
OPA's default ``http://localhost:8181`` is plain http by design.

Which https hosts may be followed is not decided here; that needs samples
of the hosts the services actually return.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sigantry_core.client.errors import ResponseUrlRefusedError

_ALLOWED_SCHEME = "https"
# Parsers differ on leading C0 controls and spaces (urlsplit strips them on
# current Pythons, older ones do not), so the check strips them itself.
_C0_AND_SPACE = "".join(chr(i) for i in range(0x21))


def check_response_url(url: str, *, source: str) -> str:
    """Return ``url`` if the client may follow it with its token; raise otherwise.

    Args:
        url: the URL exactly as the response supplied it.
        source: where it came from (e.g. ``"Location header"``), for the
            error message.

    Raises:
        ResponseUrlRefusedError: ``url`` has a scheme other than ``https``,
            or cannot be parsed.
    """
    try:
        parts = urlsplit(url.strip(_C0_AND_SPACE))
        host = parts.hostname
    except ValueError:
        raise ResponseUrlRefusedError(source=source, scheme="<unparseable>", host=None) from None
    scheme = parts.scheme.lower()
    if scheme and scheme != _ALLOWED_SCHEME:
        raise ResponseUrlRefusedError(source=source, scheme=scheme, host=host)
    return url
