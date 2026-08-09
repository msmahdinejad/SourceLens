from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from paperlens.provider_failover import ProviderPool, retryable_search_provider_error
from paperlens.workspaces import DocumentRecord, WorkspaceStore, utc_now

SearchProviderName = Literal["exa", "tavily"]


class WebSearchOptions(BaseModel):
    """Provider-neutral controls exposed by source discovery."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=2000)
    max_results: int = Field(default=20, ge=1, le=100)
    providers: list[SearchProviderName] = Field(default_factory=lambda: ["exa", "tavily"])
    exa_search_type: Literal["auto", "instant", "fast", "deep-lite", "deep", "deep-reasoning"] = (
        "auto"
    )
    tavily_search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"
    topic: Literal["general", "news", "finance"] = "general"
    category: Literal[
        "auto",
        "company",
        "people",
        "research paper",
        "news",
        "personal site",
        "financial report",
    ] = "auto"
    include_domains: list[str] = Field(default_factory=list, max_length=100)
    exclude_domains: list[str] = Field(default_factory=list, max_length=100)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    freshness: Literal["cached", "balanced", "fresh"] = "balanced"

    @field_validator("providers")
    @classmethod
    def unique_non_empty_providers(cls, value: list[SearchProviderName]) -> list[str]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("Select at least one search provider")
        return unique

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        normalized = []
        for item in value:
            domain = item.casefold().strip().removeprefix("www.")
            if not re.fullmatch(r"[a-z0-9.-]{1,253}", domain):
                raise ValueError(f"Invalid domain filter: {item}")
            if domain not in normalized:
                normalized.append(domain)
        return normalized


class WebSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    title: str
    url: str
    provider: SearchProviderName
    providers: list[SearchProviderName] = Field(default_factory=list)
    author: str | None = None
    published_at: str | None = None
    summary: str = ""
    highlights: list[str] = Field(default_factory=list)
    score: float = 0.0
    image_url: str | None = None
    favicon_url: str | None = None
    content_available: bool = False


class WebSearchSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: str
    workspace_id: str
    options: WebSearchOptions
    status: Literal["ready", "partial", "failed"]
    results: list[WebSourceResult] = Field(default_factory=list)
    provider_errors: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class WebSourceImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentRecord] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class SearchProvider(Protocol):
    name: SearchProviderName

    def search(self, options: WebSearchOptions, limit: int) -> list[dict[str, Any]]: ...

    def extract(
        self,
        urls: Sequence[str],
        *,
        query: str,
        freshness: str,
    ) -> dict[str, dict[str, Any]]: ...


class _PooledJsonClient:
    """HTTP adapter backed by the shared provider scheduler."""

    def __init__(
        self,
        keys: Sequence[str],
        *,
        timeout_seconds: float,
        cooldown_seconds: float,
        minimum_gap_seconds: float = 0.75,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.keys = tuple(dict.fromkeys(key.strip() for key in keys if key.strip()))
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._pool = (
            ProviderPool(
                self.keys,
                provider="Web source API",
                cooldown_seconds=cooldown_seconds,
                retryable=retryable_search_provider_error,
                minimum_interval_seconds=minimum_gap_seconds,
                retry_rounds=2,
                wait_for_cooldown=True,
                restore_priority_after_cooldown=False,
                max_wait_seconds=600,
                clock=clock,
                sleeper=sleeper,
            )
            if self.keys
            else None
        )

    def post(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        auth_header: str,
        auth_prefix: str = "",
    ) -> dict[str, Any]:
        if self._pool is None:
            raise RuntimeError("No API credentials are configured")

        def request(key: str, _slot: int) -> dict[str, Any]:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                transport=self.transport,
            ) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={auth_header: f"{auth_prefix}{key}", "Accept": "application/json"},
                )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise RuntimeError("Provider returned a non-object JSON response")
            return body

        return self._pool.run(request)

    def failover_status(self) -> dict[str, object]:
        return self._pool.status() if self._pool is not None else {"credential_count": 0}


class ExaSearchProvider:
    name: SearchProviderName = "exa"

    def __init__(self, client: _PooledJsonClient) -> None:
        self.client = client

    def search(self, options: WebSearchOptions, limit: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "query": options.query,
            "type": options.exa_search_type,
            "numResults": limit,
            "moderation": True,
            "contents": {"highlights": True},
        }
        if options.category != "auto":
            payload["category"] = options.category
        if options.include_domains:
            payload["includeDomains"] = options.include_domains
        if options.exclude_domains:
            payload["excludeDomains"] = options.exclude_domains
        if options.start_date:
            payload["startPublishedDate"] = f"{options.start_date}T00:00:00.000Z"
        if options.end_date:
            payload["endPublishedDate"] = f"{options.end_date}T23:59:59.999Z"
        body = self.client.post("https://api.exa.ai/search", payload, auth_header="x-api-key")
        return list(body.get("results") or [])

    def extract(
        self,
        urls: Sequence[str],
        *,
        query: str,
        freshness: str,
    ) -> dict[str, dict[str, Any]]:
        max_age = {"cached": -1, "balanced": 24, "fresh": 0}[freshness]
        body = self.client.post(
            "https://api.exa.ai/contents",
            {
                "urls": list(urls),
                "text": {"verbosity": "full", "excludeSections": ["navigation", "footer"]},
                "highlights": {"query": query, "maxCharacters": 3000},
                "maxAgeHours": max_age,
                "livecrawlTimeout": 15000,
            },
            auth_header="x-api-key",
        )
        return {
            canonical_url(str(item.get("url") or item.get("id") or "")): item
            for item in body.get("results") or []
        }


class TavilySearchProvider:
    name: SearchProviderName = "tavily"

    def __init__(self, client: _PooledJsonClient) -> None:
        self.client = client

    def search(self, options: WebSearchOptions, limit: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "query": options.query,
            "search_depth": options.tavily_search_depth,
            "topic": options.topic,
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if options.include_domains:
            payload["include_domains"] = options.include_domains
        if options.exclude_domains:
            payload["exclude_domains"] = options.exclude_domains
        if options.start_date:
            payload["start_date"] = options.start_date
        if options.end_date:
            payload["end_date"] = options.end_date
        body = self.client.post(
            "https://api.tavily.com/search",
            payload,
            auth_header="Authorization",
            auth_prefix="Bearer ",
        )
        return list(body.get("results") or [])

    def extract(
        self,
        urls: Sequence[str],
        *,
        query: str,
        freshness: str,
    ) -> dict[str, dict[str, Any]]:
        body = self.client.post(
            "https://api.tavily.com/extract",
            {
                "urls": list(urls),
                "query": query,
                "extract_depth": "advanced" if freshness == "fresh" else "basic",
                "format": "markdown",
            },
            auth_header="Authorization",
            auth_prefix="Bearer ",
        )
        return {
            canonical_url(str(item.get("url") or "")): item for item in body.get("results") or []
        }


class WebSearchStore:
    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root.resolve()

    def save(self, session: WebSearchSession) -> WebSearchSession:
        path = self._path(session.workspace_id, session.search_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return session

    def get(self, workspace_id: str, search_id: str) -> WebSearchSession:
        path = self._path(workspace_id, search_id)
        if not path.is_file():
            raise KeyError(f"Unknown web search: {search_id}")
        return WebSearchSession.model_validate_json(path.read_text(encoding="utf-8"))

    def _path(self, workspace_id: str, search_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", workspace_id):
            raise ValueError("Invalid workspace id")
        if not re.fullmatch(r"search-[a-f0-9]{12}", search_id):
            raise ValueError("Invalid web search id")
        path = (self.root / workspace_id / "web-searches" / f"{search_id}.json").resolve()
        if self.root not in path.parents:
            raise ValueError("Web search path escapes storage root")
        return path


class WebSourceService:
    """Discover, curate, fully extract, and import web sources into a workspace."""

    def __init__(
        self,
        *,
        workspaces: WorkspaceStore,
        searches: WebSearchStore,
        providers: Sequence[SearchProvider],
    ) -> None:
        self.workspaces = workspaces
        self.searches = searches
        self.providers = {provider.name: provider for provider in providers}

    def discover(self, workspace_id: str, options: WebSearchOptions) -> WebSearchSession:
        self.workspaces.get(workspace_id)
        active = [name for name in options.providers if name in self.providers]
        if not active:
            raise ValueError("None of the selected web search providers is configured")
        # One request per provider is enough. Asking each provider for the final ceiling keeps
        # enough reserve to replace cross-provider duplicates without extra API round trips.
        per_provider = min(100, options.max_results)
        merged: dict[str, WebSourceResult] = {}
        provider_order: dict[str, list[str]] = {name: [] for name in active}
        errors: dict[str, str] = {}
        raw_by_provider: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(
            max_workers=len(active), thread_name_prefix="source-search"
        ) as pool:
            futures = {
                provider_name: pool.submit(
                    self.providers[provider_name].search,
                    options,
                    per_provider,
                )
                for provider_name in active
            }
            for provider_name in active:
                try:
                    raw_by_provider[provider_name] = futures[provider_name].result()
                except Exception as error:  # noqa: BLE001 - preserve partial provider success
                    errors[provider_name] = f"{type(error).__name__}: {str(error)[:500]}"
        for provider_name in active:
            raw_results = raw_by_provider.get(provider_name)
            if raw_results is None:
                continue
            try:
                iterator = enumerate(raw_results)
            except TypeError:
                errors[provider_name] = "TypeError: provider returned a non-iterable result"
                continue
            for rank, raw in iterator:
                result = self._normalize(provider_name, raw, rank=rank, total=per_provider)
                key = canonical_url(result.url)
                if not key:
                    continue
                if key not in provider_order[provider_name]:
                    provider_order[provider_name].append(key)
                previous = merged.get(key)
                if previous:
                    providers = list(dict.fromkeys([*previous.providers, provider_name]))
                    highlights = list(dict.fromkeys([*previous.highlights, *result.highlights]))[:8]
                    merged[key] = previous.model_copy(
                        update={
                            "providers": providers,
                            "highlights": highlights,
                            "score": max(previous.score, result.score),
                        }
                    )
                else:
                    merged[key] = result
        results = self._balanced_results(merged, provider_order, active, options.max_results)
        if not results:
            message = "; ".join(f"{name}: {error}" for name, error in errors.items())
            raise RuntimeError(message or "No web sources were found")
        session = WebSearchSession(
            search_id=f"search-{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            options=options,
            status="partial" if errors else "ready",
            results=results,
            provider_errors=errors,
        )
        return self.searches.save(session)

    @staticmethod
    def _balanced_results(
        merged: dict[str, WebSourceResult],
        provider_order: dict[str, list[str]],
        active: Sequence[str],
        limit: int,
    ) -> list[WebSourceResult]:
        """Interleave providers while retaining their own relevance order and URL deduplication."""

        positions = {name: 0 for name in active}
        selected: set[str] = set()
        results: list[WebSourceResult] = []
        while len(results) < limit:
            added = False
            for provider_name in active:
                queue = provider_order.get(provider_name, [])
                while positions[provider_name] < len(queue):
                    key = queue[positions[provider_name]]
                    positions[provider_name] += 1
                    if key in selected:
                        continue
                    item = merged[key]
                    results.append(item.model_copy(update={"provider": provider_name}))
                    selected.add(key)
                    added = True
                    break
                if len(results) >= limit:
                    break
            if not added:
                break
        return results

    def import_selected(
        self,
        workspace_id: str,
        search_id: str,
        source_ids: Sequence[str],
    ) -> WebSourceImportResult:
        session = self.searches.get(workspace_id, search_id)
        wanted = set(dict.fromkeys(source_ids))
        selected = [item for item in session.results if item.source_id in wanted]
        unknown = wanted - {item.source_id for item in selected}
        if unknown:
            raise ValueError(f"Unknown selected web source: {sorted(unknown)[0]}")
        if not selected:
            raise ValueError("Select at least one web source to import")
        extracted: dict[str, dict[str, Any]] = {}
        extracted_by: dict[str, SearchProviderName] = {}
        attempt_errors: dict[str, list[str]] = {item.source_id: [] for item in selected}
        errors: list[dict[str, str]] = []
        provider_order = list(
            dict.fromkeys(
                provider_name
                for item in selected
                for provider_name in (item.providers or [item.provider])
            )
        )
        for provider_name in provider_order:
            provider = self.providers.get(provider_name)
            provider_items = [
                item
                for item in selected
                if item.source_id not in extracted
                and provider_name in (item.providers or [item.provider])
            ]
            if not provider_items:
                continue
            if provider is None:
                for item in provider_items:
                    attempt_errors[item.source_id].append(f"{provider_name} is unavailable")
                continue
            for batch_start in range(0, len(provider_items), 20):
                batch = provider_items[batch_start : batch_start + 20]
                try:
                    provider_results = provider.extract(
                        [item.url for item in batch],
                        query=session.options.query,
                        freshness=session.options.freshness,
                    )
                except Exception as error:  # noqa: BLE001 - other sources still import
                    detail = f"{provider_name}: {type(error).__name__}: {str(error)[:400]}"
                    for item in batch:
                        attempt_errors[item.source_id].append(detail)
                    continue
                for item in batch:
                    raw = provider_results.get(canonical_url(item.url), {})
                    content = str(raw.get("text") or raw.get("raw_content") or "").strip()
                    if content:
                        extracted[item.source_id] = raw
                        extracted_by[item.source_id] = provider_name
                    else:
                        attempt_errors[item.source_id].append(
                            f"{provider_name}: full content extraction returned empty"
                        )
        documents = []
        for item in selected:
            raw = extracted.get(item.source_id, {})
            content = str(raw.get("text") or raw.get("raw_content") or "").strip()
            if not content:
                errors.append(
                    {
                        "source_id": item.source_id,
                        "error": "; ".join(attempt_errors[item.source_id])[:1000]
                        or "Full content extraction returned empty",
                    }
                )
                continue
            origin_provider = extracted_by[item.source_id]
            resolved_item = item.model_copy(update={"provider": origin_provider})
            body = self._source_document(resolved_item, content)
            document = self.workspaces.add_document(
                workspace_id,
                body.encode("utf-8"),
                f"{item.title[:120]}.md",
                content_type="text/markdown",
            )
            document = self.workspaces.update_document(
                workspace_id,
                document.document_id,
                title=item.title,
                origin_url=item.url,
                origin_provider=origin_provider,
                author=item.author,
                published_at=item.published_at,
            )
            documents.append(document)
        return WebSourceImportResult(documents=documents, errors=errors)

    @staticmethod
    def _source_document(item: WebSourceResult, content: str) -> str:
        metadata = [f"Source URL: {item.url}", f"Discovery provider: {item.provider}"]
        if item.author:
            metadata.append(f"Author: {item.author}")
        if item.published_at:
            metadata.append(f"Published: {item.published_at}")
        return f"# {item.title}\n\n" + "\n".join(metadata) + f"\n\n---\n\n{content}"

    @staticmethod
    def _normalize(
        provider: SearchProviderName,
        raw: dict[str, Any],
        *,
        rank: int,
        total: int,
    ) -> WebSourceResult:
        url = str(raw.get("url") or raw.get("id") or "").strip()
        highlights = raw.get("highlights") or []
        if not isinstance(highlights, list):
            highlights = [str(highlights)]
        summary = str(raw.get("content") or raw.get("summary") or "").strip()
        score = raw.get("score")
        if not isinstance(score, (int, float)):
            score = max(0.01, 1.0 - rank / max(1, total))
        return WebSourceResult(
            source_id=source_id(url),
            title=str(raw.get("title") or urlsplit(url).netloc or "Untitled source").strip(),
            url=url,
            provider=provider,
            providers=[provider],
            author=str(raw.get("author") or "").strip() or None,
            published_at=str(raw.get("publishedDate") or raw.get("published_date") or "").strip()
            or None,
            summary=summary,
            highlights=[str(item).strip() for item in highlights if str(item).strip()][:8],
            score=float(score),
            image_url=str(raw.get("image") or "").strip() or None,
            favicon_url=str(raw.get("favicon") or "").strip() or None,
            content_available=bool(raw.get("text") or raw.get("raw_content")),
        )


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return ""
    if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
        return ""
    host = parts.hostname.casefold().removeprefix("www.") if parts.hostname else ""
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), f"{host}{port}", path, parts.query, ""))


def source_id(url: str) -> str:
    digest = hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]
    return f"web-{digest}"


def create_web_source_service(
    *,
    settings,
    workspaces: WorkspaceStore,
    transport: httpx.BaseTransport | None = None,
) -> WebSourceService:
    providers: list[SearchProvider] = []
    common = {
        "timeout_seconds": settings.research_api_timeout_seconds,
        "cooldown_seconds": settings.research_api_cooldown_seconds,
        "minimum_gap_seconds": settings.research_api_minimum_gap_seconds,
        "transport": transport,
    }
    if settings.exa_keys():
        providers.append(ExaSearchProvider(_PooledJsonClient(settings.exa_keys(), **common)))
    if settings.tavily_keys():
        providers.append(TavilySearchProvider(_PooledJsonClient(settings.tavily_keys(), **common)))
    return WebSourceService(
        workspaces=workspaces,
        searches=WebSearchStore(workspaces.root),
        providers=providers,
    )
