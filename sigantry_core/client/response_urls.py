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

What counts as absolute is decided by one parse, shared by this check and
by :func:`absolute_url`, which ``BaseRestClient._url`` uses to choose
between requesting a URL as given and joining it to the base URL. An https
URL is returned in the form ``_url`` requests: leading and trailing C0
controls and spaces removed, tabs and line breaks removed, and the scheme
in lower case. So ``HTTPS://host/x``, or ``https://host/x`` after a leading
space, is requested at ``https://host/x``, not joined to the base URL as a
path.

The check is deliberately not in ``BaseRestClient._url``: the configured
base URL is operator configuration, and a local policy endpoint such as
OPA's default ``http://localhost:8181`` is plain http by design.

Which https hosts may be followed is not decided here; that needs samples
of the hosts the services actually return.
"""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit

from sigantry_core.client.errors import ResponseUrlRefusedError

_ALLOWED_SCHEME = "https"
#: The schemes ``BaseRestClient._url`` requests as given rather than joining
#: to the base URL.
_ABSOLUTE_SCHEMES = frozenset({"http", "https"})
# Parsers differ on leading C0 controls and spaces (urlsplit strips them on
# current Pythons, older ones do not), so they are removed here first, along
# with the tab and line breaks urlsplit drops from anywhere in a URL. What
# urlsplit then parses is exactly the text that is requested.
_C0_AND_SPACE = "".join(chr(i) for i in range(0x21))
_TAB_AND_LINE_BREAKS = str.maketrans("", "", "\t\r\n")


def _split(url: str) -> tuple[SplitResult, str]:
    """Parse ``url`` once; return the parts and the URL text they describe.

    The text has the scheme in lower case (``urlsplit`` reports it that
    way). Raises :class:`ValueError` when ``urlsplit`` cannot parse it.
    """
    text = url.strip(_C0_AND_SPACE).translate(_TAB_AND_LINE_BREAKS)
    parts = urlsplit(text)
    if parts.scheme:
        text = parts.scheme + text[len(parts.scheme) :]
    return parts, text


def absolute_url(url: str) -> str | None:
    """Return ``url`` as the client requests it if it is absolute, else ``None``.

    The client's one definition of an absolute URL: an ``http`` or
    ``https`` scheme in any case, after the characters described in the
    module docstring are removed. ``None`` means the URL is relative to the
    configured base URL. Raises :class:`ValueError` when ``url`` cannot be
    parsed.
    """
    parts, text = _split(url)
    return text if parts.scheme in _ABSOLUTE_SCHEMES else None


def check_response_url(url: str, *, source: str) -> str:
    """Return the URL to request for ``url`` if the client may follow it; raise otherwise.

    Args:
        url: the URL exactly as the response supplied it.
        source: where it came from (e.g. ``"Location header"``), for the
            error message.

    Returns:
        An https URL in the form :func:`absolute_url` gives, or a relative
        URL unchanged.

    Raises:
        ResponseUrlRefusedError: ``url`` has a scheme other than ``https``,
            or cannot be parsed.
    """
    try:
        parts, text = _split(url)
        host = parts.hostname
    except ValueError:
        raise ResponseUrlRefusedError(source=source, scheme="<unparseable>", host=None) from None
    if not parts.scheme:
        return url
    if parts.scheme != _ALLOWED_SCHEME:
        raise ResponseUrlRefusedError(source=source, scheme=parts.scheme, host=host)
    return text
