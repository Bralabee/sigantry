"""Pagination generator tests (Plan 02-02 Task 1).

Covers:
- Fabric 3-page continuationToken chain (continuationUri followed verbatim)
- continuationToken-only (no Uri) falls back to query-param append on original URL
- Fabric vs Power BI preference (continuationUri wins over @odata.nextLink)
- Power BI @odata.nextLink cursor
- dedupe_by filters duplicates across pages
- MAX_PAGES safeguard raises PaginationError on runaway cursor
- malformed `value` raises PaginationError
- empty response terminates cleanly
- BaseRestClient.list_paginated delegates through paginate()

Note: respx does NOT discriminate routes by query string - a route registered on
``/v1/workspaces`` matches the URL regardless of ``?continuationToken=...``.
We therefore use ``route.side_effect = [response1, response2, ...]`` to sequence
the responses and ``route.calls`` to assert the sequence of request URLs.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from sigantry_core.auth.audiences import FABRIC_AUDIENCE, FABRIC_SCOPE
from sigantry_core.client import BaseRestClient, PaginationError, paginate
from sigantry_core.client.pagination import MAX_PAGES

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client(mock_token_provider: MagicMock) -> BaseRestClient:
    return BaseRestClient(
        token_provider=mock_token_provider,
        base_url=FABRIC_AUDIENCE,
        default_scope=FABRIC_SCOPE,
    )


def test_pagination_module_exports_max_pages() -> None:
    assert MAX_PAGES == 1000


def test_paginate_exported_from_public_namespace() -> None:
    import sigantry_core.client as c

    assert hasattr(c, "paginate")
    assert "paginate" in c.__all__


def test_base_rest_client_has_list_paginated(client: BaseRestClient) -> None:
    assert hasattr(client, "list_paginated")
    assert callable(client.list_paginated)


class TestFabricThreePages:
    def test_yields_all_items_in_order(self, client: BaseRestClient) -> None:
        pages = _load("paginated_three_pages.json")
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                httpx.Response(200, json=pages[0]),
                httpx.Response(200, json=pages[1]),
                httpx.Response(200, json=pages[2]),
            ]
            results = list(client.list_paginated("/v1/workspaces"))
            assert [item["id"] for item in results] == [1, 2, 3, 4, 5]
            assert route.call_count == 3
            # Page 2 and Page 3 requests follow continuationUri verbatim.
            call_urls = [str(c.request.url) for c in route.calls]
            assert "continuationToken=t1" in call_urls[1]
            assert "continuationToken=t2" in call_urls[2]

    def test_continuation_uri_preferred_over_token(self, client: BaseRestClient) -> None:
        """When both continuationUri and continuationToken are present we follow the URI."""
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={
                        "value": [{"id": 1}],
                        "continuationToken": "tok-ignored",
                        "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?cursor=page2",
                    },
                ),
                httpx.Response(200, json={"value": [{"id": 2}]}),
            ]
            results = list(client.list_paginated("/v1/workspaces"))
            assert [r["id"] for r in results] == [1, 2]
            # The second request must use the pre-formatted continuationUri, not a reconstructed token url.
            assert "cursor=page2" in str(route.calls[1].request.url)
            # And must NOT have the raw token in the URL.
            assert "continuationToken=tok-ignored" not in str(route.calls[1].request.url)


class TestFabricContinuationTokenOnly:
    def test_token_only_appends_query_param(self, client: BaseRestClient) -> None:
        """When continuationUri is absent but continuationToken is present, we re-issue
        the original URL with ``?continuationToken=<token>``.
        """
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                # page 1: token-only (no Uri)
                httpx.Response(
                    200,
                    json={"value": [{"id": "a"}], "continuationToken": "ONLY_TOKEN"},
                ),
                # page 2: terminal
                httpx.Response(200, json={"value": [{"id": "b"}]}),
            ]
            results = list(client.list_paginated("/v1/workspaces"))
            assert [r["id"] for r in results] == ["a", "b"]
            # Verify page-2 request URL actually carries the token query param
            assert route.call_count == 2
            assert "continuationToken=ONLY_TOKEN" in str(route.calls[1].request.url)


class TestPowerBIODataNextLink:
    def test_odata_nextlink_followed(self, client: BaseRestClient) -> None:
        pages = _load("paginated_odata_nextlink.json")
        with respx.mock(assert_all_called=False) as router:
            route = router.get("https://api.powerbi.com/v1.0/myorg/groups")
            route.side_effect = [
                httpx.Response(200, json=pages[0]),
                httpx.Response(200, json=pages[1]),
            ]
            results = list(client.list_paginated("https://api.powerbi.com/v1.0/myorg/groups"))
            assert [r["id"] for r in results] == ["g1", "g2", "g3"]
            assert route.call_count == 2
            assert "$skip=2" in str(route.calls[1].request.url)


class TestPreferenceOrder:
    def test_continuation_uri_beats_odata_nextlink(self, client: BaseRestClient) -> None:
        """Hybrid responses prefer Fabric continuationUri over Power BI nextLink."""
        with respx.mock(assert_all_called=False) as router:
            fabric_route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            fabric_route.side_effect = [
                httpx.Response(
                    200,
                    json={
                        "value": [{"id": 1}],
                        "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?cursor=fabric",
                        "@odata.nextLink": "https://api.powerbi.com/v1.0/myorg/groups?$skip=50",
                    },
                ),
                httpx.Response(200, json={"value": [{"id": 2}]}),
            ]
            powerbi_route = router.get("https://api.powerbi.com/v1.0/myorg/groups").mock(
                return_value=httpx.Response(200, json={"value": [{"id": 99}]})
            )
            results = list(client.list_paginated("/v1/workspaces"))
            assert [r["id"] for r in results] == [1, 2]
            # We followed continuationUri (fabric_route called twice), never touched Power BI.
            assert fabric_route.call_count == 2
            assert powerbi_route.call_count == 0
            assert "cursor=fabric" in str(fabric_route.calls[1].request.url)


class TestDedupeBy:
    def test_dedupe_by_id_across_pages(self, client: BaseRestClient) -> None:
        """When dedupe_by='id', duplicate ids across pages are filtered."""
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={
                        "value": [{"id": 1}, {"id": 2}],
                        "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?cursor=p2",
                    },
                ),
                httpx.Response(200, json={"value": [{"id": 2}, {"id": 3}]}),
            ]
            results = list(client.list_paginated("/v1/workspaces", dedupe_by="id"))
            assert [r["id"] for r in results] == [1, 2, 3]

    def test_no_dedupe_by_default(self, client: BaseRestClient) -> None:
        """Without dedupe_by the generator preserves duplicates."""
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={
                        "value": [{"id": 1}, {"id": 2}],
                        "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?cursor=p2",
                    },
                ),
                httpx.Response(200, json={"value": [{"id": 2}, {"id": 3}]}),
            ]
            results = list(client.list_paginated("/v1/workspaces"))
            assert [r["id"] for r in results] == [1, 2, 2, 3]


class TestMaxPages:
    def test_runaway_cursor_raises_pagination_error(self, client: BaseRestClient) -> None:
        """If the server returns continuationUri indefinitely, we bail out."""
        with respx.mock(assert_all_called=False) as router:
            # Every call returns the same cursor -> infinite loop prevention
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "value": [{"id": 1}],
                        "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?cursor=same",
                    },
                )
            )
            with pytest.raises(PaginationError, match="MAX_PAGES"):
                list(client.list_paginated("/v1/workspaces", max_pages=5))
            assert route.call_count == 5


class TestDuplicateContinuationToken:
    """Defence-in-depth against a known Fabric API loop bug — same
    continuationToken returned twice in the token-only fallback path. See
    ``docs/RELATED-WORK.md`` gotcha #10 (lifted from usf_fabric_cli_cicd v1.8.4).
    """

    def test_same_token_twice_raises_pagination_error(self, client: BaseRestClient) -> None:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                # page 1: token-only with token=DUP
                httpx.Response(
                    200,
                    json={"value": [{"id": "a"}], "continuationToken": "DUP"},
                ),
                # page 2: SAME token returned again -> would loop forever
                httpx.Response(
                    200,
                    json={"value": [{"id": "b"}], "continuationToken": "DUP"},
                ),
            ]
            with pytest.raises(PaginationError, match="same continuationToken"):
                list(client.list_paginated("/v1/workspaces"))
            # We refuse to issue a third request — the loop is broken on detection.
            assert route.call_count == 2

    def test_distinct_tokens_in_sequence_succeed(self, client: BaseRestClient) -> None:
        """Sanity: the dedupe must NOT trip when tokens legitimately differ."""
        with respx.mock(assert_all_called=False) as router:
            route = router.get(f"{FABRIC_AUDIENCE}/v1/workspaces")
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"value": [{"id": "a"}], "continuationToken": "T1"},
                ),
                httpx.Response(
                    200,
                    json={"value": [{"id": "b"}], "continuationToken": "T2"},
                ),
                httpx.Response(200, json={"value": [{"id": "c"}]}),
            ]
            results = list(client.list_paginated("/v1/workspaces"))
            assert [r["id"] for r in results] == ["a", "b", "c"]
            assert route.call_count == 3


class TestMalformedResponses:
    def test_value_not_list_raises(self, client: BaseRestClient) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
                return_value=httpx.Response(200, json={"value": "not-a-list"})
            )
            with pytest.raises(PaginationError, match="not a list"):
                list(client.list_paginated("/v1/workspaces"))

    def test_response_not_object_raises(self, client: BaseRestClient) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
                return_value=httpx.Response(200, json=[1, 2, 3])
            )
            with pytest.raises(PaginationError, match="not a JSON object"):
                list(client.list_paginated("/v1/workspaces"))

    def test_empty_response_terminates(self, client: BaseRestClient) -> None:
        """Empty body / no ``value`` / no cursor -> generator yields nothing, no error."""
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
                return_value=httpx.Response(200, json={})
            )
            assert list(client.list_paginated("/v1/workspaces")) == []

    def test_value_null_treated_as_empty(self, client: BaseRestClient) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{FABRIC_AUDIENCE}/v1/workspaces").mock(
                return_value=httpx.Response(200, json={"value": None})
            )
            assert list(client.list_paginated("/v1/workspaces")) == []


class TestDirectPaginateCall:
    def test_paginate_function_accepts_base_rest_client(self, client: BaseRestClient) -> None:
        """Smoke test: callers can use paginate() directly without list_paginated()."""
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{FABRIC_AUDIENCE}/v1/items").mock(
                return_value=httpx.Response(200, json={"value": [{"id": "x"}]})
            )
            results = list(paginate(client, "GET", "/v1/items"))
            assert results == [{"id": "x"}]
