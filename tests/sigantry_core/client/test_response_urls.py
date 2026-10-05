"""A URL that a response tells the client to follow must be https.

Every site that follows a response-supplied URL goes through
``check_response_url``: the LRO ``Location`` header, the ``/result``
``Location`` of a succeeded operation, ARM's ``Azure-AsyncOperation`` and
``Location`` headers, Fabric's ``continuationUri`` and Power BI's
``@odata.nextLink``. For each, a plain-http URL on another host is refused
and the transport receives no request beyond the flow's own; the https
control on the service's own host still receives the request with its
``Authorization`` header, which proves the transport here can see the header.
The tests read the list of requests the transport itself recorded.

An https URL whose scheme is in upper case, or that starts with a space, is
requested at that URL: the check and ``BaseRestClient._url`` share one
definition of an absolute URL, so it is not joined to the base URL as a path.

The configured base URL is not checked: OPA's ``http://localhost:8181`` keeps
working.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import httpx
import pytest

from sigantry_core.client import (
    BaseRestClient,
    ClientError,
    FabricArmRestClient,
    ResponseUrlRefusedError,
)
from sigantry_core.client.response_urls import absolute_url, check_response_url

FAB = "https://api.fabric.microsoft.com"
ARM = "https://management.azure.com"
TOKEN = "Bearer test-token-xyz"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)


# --------------------------------------------------------------------------
# The helper
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"{FAB}/v1/operations/op-1",
        "https://other.example/v1/x?sig=1",
        "/v1/operations/op-1",
        "v1/operations/op-1",
        "//api.fabric.microsoft.com/v1/x",
    ],
)
def test_https_and_relative_urls_are_returned_unchanged(url: str) -> None:
    assert check_response_url(url, source="Location header") == url


@pytest.mark.parametrize(
    "url",
    [
        "HTTPS://api.fabric.microsoft.com/v1/x",
        "Https://api.fabric.microsoft.com/v1/x",
        " https://api.fabric.microsoft.com/v1/x",
        "\x00\x1f https://api.fabric.microsoft.com/v1/x ",
        "ht\ttps://api.fabric.microsoft.com/v1/x",
    ],
)
def test_https_url_is_returned_in_the_form_the_client_requests(url: str) -> None:
    expected = f"{FAB}/v1/x"
    assert check_response_url(url, source="Location header") == expected
    assert absolute_url(url) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{FAB}/v1/x", f"{FAB}/v1/x"),
        ("HTTPS://api.fabric.microsoft.com/v1/x", f"{FAB}/v1/x"),
        (" https://api.fabric.microsoft.com/v1/x", f"{FAB}/v1/x"),
        ("HTTP://localhost:8181/v1/data", "http://localhost:8181/v1/data"),
        ("/v1/x", f"{FAB}/v1/x"),
        ("v1/x", f"{FAB}/v1/x"),
        ("v1/items:batch", f"{FAB}/v1/items:batch"),
        ("items:batch", f"{FAB}/items:batch"),
    ],
)
def test_client_url_uses_the_same_definition_of_absolute(path: str, expected: str) -> None:
    client = BaseRestClient(token_provider=_tp(), base_url=FAB, default_scope=f"{FAB}/.default")
    assert client._url(path) == expected


@pytest.mark.parametrize(
    ("url", "scheme"),
    [
        ("http://elsewhere.example/v1/x", "http"),
        ("HTTP://elsewhere.example/v1/x", "http"),
        (" http://elsewhere.example/v1/x", "http"),
        ("\x00\x1fhttp://elsewhere.example/v1/x", "http"),
        ("ftp://elsewhere.example/x", "ftp"),
        ("file:///etc/hosts", "file"),
        ("javascript:alert(1)", "javascript"),
        ("http://[::1/x", "<unparseable>"),
    ],
)
def test_non_https_urls_are_refused(url: str, scheme: str) -> None:
    with pytest.raises(ResponseUrlRefusedError) as excinfo:
        check_response_url(url, source="continuationUri")
    assert excinfo.value.scheme == scheme
    assert excinfo.value.source == "continuationUri"
    assert isinstance(excinfo.value, ClientError)


def test_refusal_message_does_not_repeat_the_url() -> None:
    with pytest.raises(ResponseUrlRefusedError) as excinfo:
        check_response_url("http://elsewhere.example/p?sig=PLANTEDQS", source="Location header")
    message = str(excinfo.value)
    assert "PLANTEDQS" not in message
    assert "elsewhere.example" in message
    assert "no request was sent" in message


# --------------------------------------------------------------------------
# Every call site
# --------------------------------------------------------------------------


def _tp() -> MagicMock:
    tp = MagicMock()
    tp.get_token.return_value = "test-token-xyz"
    tp.last_credential_class.return_value = "MockCredential"
    return tp


Handler = Callable[[httpx.Request], httpx.Response]


def _site_handler(site: str, target: str, received: list[httpx.Request]) -> Handler:
    """Serve one site's flow; record every request the transport receives."""

    def handler(req: httpx.Request) -> httpx.Response:
        received.append(req)
        if req.url.host not in ("api.fabric.microsoft.com", "management.azure.com"):
            return httpx.Response(200, json={"status": "Succeeded", "value": []})
        if site == "lro-location":
            if req.method == "POST":
                return httpx.Response(
                    202,
                    headers={"Location": target, "x-ms-operation-id": "op-1", "Retry-After": "0"},
                )
            return httpx.Response(200, json={"status": "Succeeded"})
        if site == "lro-result":
            if req.method == "POST":
                return httpx.Response(
                    202,
                    headers={
                        "Location": f"{FAB}/v1/operations/op-1",
                        "x-ms-operation-id": "op-1",
                        "Retry-After": "0",
                    },
                )
            if req.url.path.endswith("/result"):
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(200, json={"status": "Succeeded"}, headers={"Location": target})
        if site in ("arm-async", "arm-location"):
            if req.method == "POST":
                header = "Azure-AsyncOperation" if site == "arm-async" else "Location"
                return httpx.Response(202, headers={header: target, "Retry-After": "0"})
            return httpx.Response(200, json={"status": "Succeeded"})
        # pagination
        if "page2" in str(req.url):
            return httpx.Response(200, json={"value": [{"id": 2}]})
        key = "continuationUri" if site == "continuationUri" else "@odata.nextLink"
        return httpx.Response(200, json={"value": [{"id": 1}], key: target})

    return handler


def _drive(site: str, target: str, received: list[httpx.Request]) -> None:
    """Run one site's flow; ``received`` collects what the transport receives.

    The caller owns ``received``, so it still holds every request when the
    flow raises.
    """
    http = httpx.Client(transport=httpx.MockTransport(_site_handler(site, target, received)))
    if site.startswith("arm-"):
        arm = FabricArmRestClient(token_provider=_tp(), http_client=http)
        arm.send_arm_lro("POST", "/subscriptions/s/providers/x/capacities/c/suspend")
        return
    client = BaseRestClient(
        token_provider=_tp(), base_url=FAB, default_scope=f"{FAB}/.default", http_client=http
    )
    if site in ("continuationUri", "odata.nextLink"):
        list(client.list_paginated("/v1/workspaces"))
    else:
        client.send_lro("POST", "/v1/workspaces/w/items", json={})


_SITES = {
    # site: (path on the target host, the service's own base URL, the number
    # of requests the flow makes before it follows the URL under test)
    "lro-location": ("/v1/operations/op-1", FAB, 1),
    "lro-result": ("/v1/operations/op-1/result", FAB, 2),
    "arm-async": ("/subscriptions/s/operations/op-1", ARM, 1),
    "arm-location": ("/subscriptions/s/operations/op-1", ARM, 1),
    "continuationUri": ("/v1/workspaces?page2=1", FAB, 1),
    "odata.nextLink": ("/v1/workspaces?page2=1", FAB, 1),
}


def _followed(site: str, received: list[httpx.Request]) -> list[tuple[str, str | None]]:
    """The requests after the flow's own, as ``(url, Authorization)``."""
    before = _SITES[site][2]
    return [(str(r.url), r.headers.get("Authorization")) for r in received[before:]]


@pytest.mark.parametrize("scheme", ["http", "HTTP"])
@pytest.mark.parametrize("site", sorted(_SITES))
def test_plain_http_url_from_a_response_is_refused_and_not_requested(
    site: str, scheme: str
) -> None:
    path, _base, before = _SITES[site]
    received: list[httpx.Request] = []
    with pytest.raises(ResponseUrlRefusedError) as excinfo:
        _drive(site, f"{scheme}://elsewhere.example{path}", received)
    assert excinfo.value.scheme == "http"
    assert excinfo.value.host == "elsewhere.example"
    # The flow's own requests were made (so the transport was in use), and
    # nothing after them: no request reached any host, that one included.
    assert len(received) == before
    assert _followed(site, received) == []
    assert all(r.url.host != "elsewhere.example" for r in received)


@pytest.mark.parametrize("site", sorted(_SITES))
def test_https_url_on_the_service_host_is_still_followed_with_the_token(site: str) -> None:
    """Control: the same flow with an https URL reaches the server with its header."""
    path, base, _before = _SITES[site]
    received: list[httpx.Request] = []
    _drive(site, base + path, received)
    assert _followed(site, received) == [(base + path, TOKEN)]


@pytest.mark.parametrize("variant", ["upper-case scheme", "leading space"])
@pytest.mark.parametrize("site", sorted(_SITES))
def test_https_url_in_another_form_is_requested_at_that_url(site: str, variant: str) -> None:
    """``HTTPS://host/...`` and `` https://host/...`` are absolute, not base-relative paths."""
    path, base, _before = _SITES[site]
    url = base + path
    target = {
        "upper-case scheme": url.replace("https://", "HTTPS://", 1),
        "leading space": " " + url,
    }[variant]
    received: list[httpx.Request] = []
    _drive(site, target, received)
    assert _followed(site, received) == [(url, TOKEN)]


def test_relative_location_is_resolved_against_the_configured_base() -> None:
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(
                202,
                headers={
                    "Location": "/v1/operations/op-1",
                    "x-ms-operation-id": "op-1",
                    "Retry-After": "0",
                },
            )
        seen[str(req.url)] = req.headers.get("Authorization")
        return httpx.Response(200, json={"status": "Succeeded"})

    client = BaseRestClient(
        token_provider=_tp(),
        base_url=FAB,
        default_scope=f"{FAB}/.default",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.send_lro("POST", "/v1/workspaces/w/items", json={})
    assert seen == {f"{FAB}/v1/operations/op-1": TOKEN}


def test_configured_http_base_url_is_not_checked() -> None:
    """OPA's default endpoint is plain http on localhost; the check is not in ``_url``."""
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen[f"{req.url.scheme}://{req.url.host}:{req.url.port}"] = req.headers.get("Authorization")
        return httpx.Response(200, json={"result": True})

    client = BaseRestClient(
        token_provider=_tp(),
        base_url="http://localhost:8181",
        default_scope="opa",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    resp = client.send("POST", "/v1/data/sigantry/approval/allow", json={"input": {}})
    assert resp.json_body == {"result": True}
    assert seen == {"http://localhost:8181": TOKEN}
