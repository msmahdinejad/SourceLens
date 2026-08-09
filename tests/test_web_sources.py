from pathlib import Path

import httpx

from paperlens.web_sources import (
    ExaSearchProvider,
    TavilySearchProvider,
    WebSearchOptions,
    WebSearchStore,
    WebSourceService,
    _PooledJsonClient,
)
from paperlens.workspaces import WorkspaceStore


class FakeProvider:
    def __init__(self, name, search_results, content):
        self.name = name
        self.search_results = search_results
        self.content = content
        self.extracted_urls = []

    def search(self, options, limit):
        return self.search_results[:limit]

    def extract(self, urls, *, query, freshness):
        self.extracted_urls.extend(urls)
        return {url: {"url": url, "text": self.content[url]} for url in urls}


def test_discovery_deduplicates_providers_and_imports_only_selected_sources(tmp_path: Path):
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Research")
    shared = "https://example.com/paper"
    exa = FakeProvider(
        "exa",
        [
            {"title": "Shared paper", "url": shared, "highlights": ["Exa highlight"]},
            {"title": "Second", "url": "https://example.com/second"},
        ],
        {shared: "Complete source text"},
    )
    tavily = FakeProvider(
        "tavily",
        [{"title": "Shared paper", "url": f"{shared}#abstract", "content": "Preview"}],
        {},
    )
    service = WebSourceService(
        workspaces=workspaces,
        searches=WebSearchStore(workspaces.root),
        providers=[exa, tavily],
    )

    search = service.discover(
        workspace.workspace_id,
        WebSearchOptions(query="retrieval augmented generation", max_results=10),
    )

    assert len(search.results) == 2
    assert search.results[0].providers == ["exa", "tavily"]
    imported = service.import_selected(
        workspace.workspace_id,
        search.search_id,
        [search.results[0].source_id],
    )
    assert exa.extracted_urls == [shared]
    assert len(imported.documents) == 1
    document = imported.documents[0]
    assert document.origin_url == shared
    assert document.origin_provider == "exa"
    assert "Complete source text" in workspaces.document_path(
        workspace.workspace_id, document.document_id
    ).read_text(encoding="utf-8")


def test_exa_and_tavily_adapters_use_current_official_request_shapes():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        requests.append((str(request.url), dict(request.headers), payload))
        if request.url.path == "/search" and request.url.host == "api.exa.ai":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    exa = ExaSearchProvider(
        _PooledJsonClient(
            ["exa-test-key"],
            timeout_seconds=5,
            cooldown_seconds=1,
            transport=transport,
        )
    )
    tavily = TavilySearchProvider(
        _PooledJsonClient(
            ["tavily-test-key"], timeout_seconds=5, cooldown_seconds=1, transport=transport
        )
    )
    options = WebSearchOptions(
        query="modern RAG",
        max_results=12,
        exa_search_type="deep",
        tavily_search_depth="advanced",
    )

    exa.search(options, 6)
    tavily.search(options, 6)

    exa_request, tavily_request = requests
    assert exa_request[2]["type"] == "deep"
    assert exa_request[2]["contents"] == {"highlights": True}
    assert exa_request[1]["x-api-key"] == "exa-test-key"
    assert tavily_request[2]["search_depth"] == "advanced"
    assert tavily_request[2]["include_raw_content"] is False
    assert tavily_request[1]["authorization"] == "Bearer tavily-test-key"


def test_pool_falls_back_after_rate_limit_without_exposing_credentials():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["x-api-key"])
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "limit"})
        return httpx.Response(200, json={"ok": True})

    client = _PooledJsonClient(
        ["first-secret", "second-secret"],
        timeout_seconds=5,
        cooldown_seconds=0,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )

    assert client.post("https://api.exa.ai/test", {}, auth_header="x-api-key") == {"ok": True}
    assert calls == ["first-secret", "second-secret"]
    assert client.failover_status()["last_failover"] is True
    assert client.failover_status()["last_attempt_count"] == 2
    assert "first-secret" not in repr(client.failover_status())


def test_pool_enforces_minimum_gap_between_requests_on_the_same_key():
    now = [10.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    client = _PooledJsonClient(
        ["only-key"],
        timeout_seconds=5,
        cooldown_seconds=0,
        minimum_gap_seconds=0.03,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
        clock=lambda: now[0],
        sleeper=sleep,
    )

    client.post("https://api.exa.ai/first", {}, auth_header="x-api-key")
    client.post("https://api.exa.ai/second", {}, auth_header="x-api-key")

    assert len(sleeps) == 1
    assert abs(sleeps[0] - 0.03) < 1e-9
    assert abs(now[0] - 10.03) < 1e-9
    assert client.failover_status()["total_rate_wait_ms"] == 30


def test_full_text_extraction_falls_back_to_another_discovery_provider(tmp_path: Path):
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Fallback")
    url = "https://example.com/shared"
    exa = FakeProvider("exa", [{"title": "Shared", "url": url}], {})
    tavily = FakeProvider(
        "tavily",
        [{"title": "Shared", "url": url}],
        {url: "Complete text from the fallback provider"},
    )
    service = WebSourceService(
        workspaces=workspaces,
        searches=WebSearchStore(workspaces.root),
        providers=[exa, tavily],
    )
    search = service.discover(workspace.workspace_id, WebSearchOptions(query="fallback"))

    imported = service.import_selected(
        workspace.workspace_id,
        search.search_id,
        [search.results[0].source_id],
    )

    assert len(imported.documents) == 1
    assert imported.documents[0].origin_provider == "tavily"
    assert imported.errors == []


def test_discovery_interleaves_exa_and_tavily_with_an_equal_share(tmp_path: Path):
    workspaces = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspaces.create_workspace("Balanced discovery")
    exa = FakeProvider(
        "exa",
        [
            {
                "title": f"Exa {index}",
                "url": f"https://exa.example/{index}",
                "score": 1 - index / 100,
            }
            for index in range(8)
        ],
        {},
    )
    tavily = FakeProvider(
        "tavily",
        [
            {
                "title": f"Tavily {index}",
                "url": f"https://tavily.example/{index}",
                "score": 0.2 - index / 100,
            }
            for index in range(8)
        ],
        {},
    )
    service = WebSourceService(
        workspaces=workspaces,
        searches=WebSearchStore(workspaces.root),
        providers=[exa, tavily],
    )

    search = service.discover(
        workspace.workspace_id,
        WebSearchOptions(query="balanced research", max_results=10),
    )

    assert len(search.results) == 10
    assert [item.provider for item in search.results].count("exa") == 5
    assert [item.provider for item in search.results].count("tavily") == 5
    assert [item.provider for item in search.results[:4]] == ["exa", "tavily", "exa", "tavily"]
