"""A URL that a response tells the client to follow must be https.

Every site that follows a response-supplied URL goes through
``check_response_url``: the LRO ``Location`` header, the ``/result``
``Location`` of a succeeded operation, ARM's ``Azure-AsyncOperation`` and
``Location`` headers, Fabric's ``continuationUri`` and Power BI's
``@odata.nextLink``. For each, a plain-http URL on another host is refused
and that host receives no request at all; the https control on the service's
own host still receives the request with its ``Authorization`` header, which
proves the transport here can see the header.

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
from sigantry_core.client.response_urls import check_response_url

FAB = "https://api.fabric.microsoft.com"
ARM = "https://management.azure.com"
EVIL = "http://elsewhere.example"
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
        "HTTPS://api.fabric.microsoft.com/v1/x",
        "https://other.example/v1/x?sig=1",
        "/v1/operations/op-1",
        "v1/operations/op-1",
        "//api.fabric.microsoft.com/v1/x",
    ],
)
def test_https_and_relative_urls_are_returned_unchanged(url: str) -> None:
    assert check_response_url(url, source="Location header") == url


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


def _site_handler(site: str, target: str, seen: dict[str, str | None]) -> Handler:
    def handler(req: httpx.Request) -> httpx.Response:
        origin = f"{req.url.scheme}://{req.url.host}"
        if req.url.host not in ("api.fabric.microsoft.com", "management.azure.com"):
            seen[origin] = req.headers.get("Authorization")
            return httpx.Response(200, json={"status": "Succeeded", "value": []})
        if site == "lro-location":
            if req.method == "POST":
                return httpx.Response(
                    202,
                    headers={"Location": target, "x-ms-operation-id": "op-1", "Retry-After": "0"},
                )
            seen[origin] = req.headers.get("Authorization")
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
                seen[origin] = req.headers.get("Authorization")
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(200, json={"status": "Succeeded"}, headers={"Location": target})
        if site in ("arm-async", "arm-location"):
            if req.method == "POST":
                header = "Azure-AsyncOperation" if site == "arm-async" else "Location"
                return httpx.Response(202, headers={header: target, "Retry-After": "0"})
            seen[origin] = req.headers.get("Authorization")
            return httpx.Response(200, json={"status": "Succeeded"})
        # pagination
        if "page2" in str(req.url):
            seen[origin] = req.headers.get("Authorization")
            return httpx.Response(200, json={"value": [{"id": 2}]})
        key = "continuationUri" if site == "continuationUri" else "@odata.nextLink"
        return httpx.Response(200, json={"value": [{"id": 1}], key: target})

    return handler


def _drive(site: str, target: str) -> dict[str, str | None]:
    seen: dict[str, str | None] = {}
    http = httpx.Client(transport=httpx.MockTransport(_site_handler(site, target, seen)))
    if site.startswith("arm-"):
        arm = FabricArmRestClient(token_provider=_tp(), http_client=http)
        arm.send_arm_lro("POST", "/subscriptions/s/providers/x/capacities/c/suspend")
        return seen
    client = BaseRestClient(
        token_provider=_tp(), base_url=FAB, default_scope=f"{FAB}/.default", http_client=http
    )
    if site in ("continuationUri", "odata.nextLink"):
        list(client.list_paginated("/v1/workspaces"))
    else:
        client.send_lro("POST", "/v1/workspaces/w/items", json={})
    return seen


_SITES = {
    # site: (path on the target host, the service's own base URL)
    "lro-location": ("/v1/operations/op-1", FAB),
    "lro-result": ("/v1/operations/op-1/result", FAB),
    "arm-async": ("/subscriptions/s/operations/op-1", ARM),
    "arm-location": ("/subscriptions/s/operations/op-1", ARM),
    "continuationUri": ("/v1/workspaces?page2=1", FAB),
    "odata.nextLink": ("/v1/workspaces?page2=1", FAB),
}


@pytest.mark.parametrize("site", sorted(_SITES))
def test_plain_http_url_from_a_response_is_refused_and_not_requested(site: str) -> None:
    path, _base = _SITES[site]
    seen: dict[str, str | None] = {}
    with pytest.raises(ResponseUrlRefusedError) as excinfo:
        seen = _drive(site, EVIL + path)
    assert excinfo.value.scheme == "http"
    assert excinfo.value.host == "elsewhere.example"
    assert EVIL not in seen


@pytest.mark.parametrize("site", sorted(_SITES))
def test_https_url_on_the_service_host_is_still_followed_with_the_token(site: str) -> None:
    """Control: the same flow with an https URL reaches the server with its header."""
    path, base = _SITES[site]
    seen = _drive(site, base + path)
    assert seen == {base: TOKEN}


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
