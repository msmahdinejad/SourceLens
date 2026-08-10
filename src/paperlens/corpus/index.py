from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import chromadb
import numpy as np
from chromadb.api.configuration import (
    CollectionConfigurationInternal,
    ConfigurationParameter,
    HNSWConfigurationInternal,
)
from google import genai
from google.genai import types
from openai import OpenAI

from paperlens.domain import DocumentChunk, Evidence
from paperlens.provider_failover import ProviderFailoverError, ProviderPool

_chroma_configuration_lock = threading.Lock()


def migrate_legacy_chroma_configurations(path: Path) -> int:
    """Repair empty Chroma 1.x collection configs for the pinned 0.6 runtime."""
    database = path / "chroma.sqlite3"
    if not database.is_file():
        return 0
    with _chroma_configuration_lock, sqlite3.connect(database, timeout=30) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"collections", "collection_metadata"}.issubset(tables):
            return 0
        rows = list(
            connection.execute(
                """
                SELECT collections.id, collections.config_json_str,
                       COALESCE(collection_metadata.str_value, 'l2')
                FROM collections
                LEFT JOIN collection_metadata
                  ON collection_metadata.collection_id = collections.id
                 AND collection_metadata.key = 'hnsw:space'
                """
            )
        )
        legacy = []
        for collection_id, raw_configuration, distance_space in rows:
            try:
                configuration = json.loads(raw_configuration or "{}")
            except (TypeError, ValueError):
                continue
            if configuration != {}:
                continue
            space = str(distance_space)
            if space not in {"cosine", "ip", "l2"}:
                space = "l2"
            hnsw = HNSWConfigurationInternal(
                [ConfigurationParameter("space", space)]
            )
            migrated = CollectionConfigurationInternal(
                [ConfigurationParameter("hnsw_configuration", hnsw)]
            )
            legacy.append((migrated.to_json_str(), str(collection_id)))
        if not legacy:
            return 0

        backup = database.with_suffix(".sqlite3.pre-0.6-config.bak")
        if not backup.exists():
            with sqlite3.connect(backup) as destination:
                connection.backup(destination)
        connection.executemany(
            "UPDATE collections SET config_json_str = ? WHERE id = ?",
            legacy,
        )
        connection.commit()
        return len(legacy)


class TextEmbedder(Protocol):
    model_name: str

    def embed_passages(
        self,
        texts: Sequence[str],
        titles: Sequence[str] | None = None,
    ) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class MultilingualE5Embedder:
    """Normalized multilingual embeddings with the E5 query/passage convention."""

    def __init__(self, model_name: str, device: str | None = None) -> None:
        # Keep the ~1 GB PyTorch stack out of the primary Gemini startup path.
        # It is imported only if the local E5 fallback is actually requested.
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        dimension = getattr(self._model, "get_embedding_dimension", None)
        if dimension is None:
            dimension = self._model.get_sentence_embedding_dimension
        self.dimensions = int(dimension())

    def embed_passages(
        self,
        texts: Sequence[str],
        titles: Sequence[str] | None = None,
    ) -> np.ndarray:
        return self._encode([f"passage: {text}" for text in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._encode([f"query: {text}"])[0]

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        return self._model.encode(
            list(texts),
            batch_size=32,
            show_progress_bar=len(texts) > 32,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )


class GeminiEmbedder:
    """Gemini Embedding 2 using Google's asymmetric retrieval prompt format."""

    def __init__(
        self,
        model_name: str,
        api_key: str | Sequence[str],
        *,
        dimensions: int = 768,
        batch_size: int = 32,
        requests_per_minute: int = 90,
        client=None,
        clients: Sequence | None = None,
    ) -> None:
        keys = [api_key] if isinstance(api_key, str) else list(api_key)
        keys = [key.strip() for key in keys if key.strip()]
        if not keys:
            raise ValueError("EMBEDDING_API_KEY or LLM_API_KEY is required for Gemini embeddings")
        if client is not None and clients is not None:
            raise ValueError("Pass either client or clients, not both")
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.requests_per_minute = requests_per_minute
        resources = (
            list(clients)
            if clients is not None
            else ([client] if client is not None else [genai.Client(api_key=key) for key in keys])
        )
        if len(resources) != len(keys):
            raise ValueError("The number of Gemini clients must match the number of keys")
        self._request_times = [deque() for _ in resources]
        self._pool = ProviderPool(resources, provider="Gemini Embedding API")

    def embed_passages(
        self,
        texts: Sequence[str],
        titles: Sequence[str] | None = None,
    ) -> np.ndarray:
        if titles is not None and len(titles) != len(texts):
            raise ValueError("titles and texts must have the same length")
        titles = titles or ["none"] * len(texts)
        formatted = [
            f"title: {title} | text: {text}" for title, text in zip(titles, texts, strict=True)
        ]
        return self._embed_many(formatted)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed_many([f"task: search result | query: {text}"])[0]

    def _embed_many(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            contents = [types.Content(parts=[types.Part.from_text(text=text)]) for text in batch]
            response = self._pool.run(
                lambda client, index, batch=batch, contents=contents: self._embed_batch(
                    client, index, batch, contents
                )
            )
            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise RuntimeError("Gemini returned an unexpected number of embedding vectors")
            vectors.extend(embedding.values for embedding in embeddings)

        array = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        return array / np.maximum(norms, 1e-12)

    def _embed_batch(self, client, index: int, batch, contents):
        self._throttle(index, len(batch))
        return client.models.embed_content(
            model=self.model_name,
            contents=contents,
            config=types.EmbedContentConfig(
                output_dimensionality=self.dimensions,
            ),
        )

    def failover_status(self) -> dict[str, object]:
        return self._pool.status()

    def _throttle(self, key_index: int, request_count: int) -> None:
        """Stay below the observed free-tier per-minute document quota."""
        request_times = self._request_times[key_index]
        while True:
            now = time.monotonic()
            while request_times and now - request_times[0] >= 60:
                request_times.popleft()
            if len(request_times) + request_count <= self.requests_per_minute:
                request_times.extend([now] * request_count)
                return
            time.sleep(max(0.1, 60 - (now - request_times[0]) + 0.25))


class OpenAICompatibleEmbedder:
    """Secret-safe pooled embeddings for hosted or local OpenAI-compatible APIs."""

    def __init__(
        self,
        model_name: str,
        api_keys: str | Sequence[str],
        *,
        base_url: str,
        dimensions: int,
        batch_size: int = 64,
        timeout_seconds: float = 45.0,
        client=None,
        clients: Sequence | None = None,
    ) -> None:
        keys = [api_keys] if isinstance(api_keys, str) else list(api_keys)
        keys = [key.strip() for key in keys if key.strip()]
        if not keys:
            raise ValueError("EMBEDDING_API_KEY is required for remote embeddings")
        if client is not None and clients is not None:
            raise ValueError("Pass either client or clients, not both")
        resources = (
            list(clients)
            if clients is not None
            else (
                [client]
                if client is not None
                else [
                    OpenAI(
                        api_key=key,
                        base_url=base_url,
                        timeout=timeout_seconds,
                        max_retries=0,
                    )
                    for key in keys
                ]
            )
        )
        if len(resources) != len(keys):
            raise ValueError("The number of embedding clients must match the number of keys")
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._pool = ProviderPool(resources, provider="OpenAI-compatible Embedding API")

    def embed_passages(
        self,
        texts: Sequence[str],
        titles: Sequence[str] | None = None,
    ) -> np.ndarray:
        if titles is not None and len(titles) != len(texts):
            raise ValueError("titles and texts must have the same length")
        titles = titles or ["none"] * len(texts)
        return self._embed_many(
            [f"title: {title} | text: {text}" for title, text in zip(titles, texts, strict=True)]
        )

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed_many([text])[0]

    def _embed_many(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            response = self._pool.run(
                lambda client, _index, batch=batch: client.embeddings.create(
                    model=self.model_name,
                    input=batch,
                )
            )
            rows = sorted(response.data, key=lambda item: item.index)
            if len(rows) != len(batch):
                raise RuntimeError("Embedding provider returned an unexpected vector count")
            vectors.extend(item.embedding for item in rows)
        array = np.asarray(vectors, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.dimensions:
            actual = array.shape[1] if array.ndim == 2 else 0
            raise ValueError(
                "Embedding dimension mismatch: "
                f"configured {self.dimensions}, provider returned {actual}"
            )
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        return array / np.maximum(norms, 1e-12)

    def failover_status(self) -> dict[str, object]:
        return self._pool.status()


class ChromaEmbeddingIndex:
    """Persistent cosine index for paper chunks."""

    def __init__(
        self,
        path: Path,
        embedder: TextEmbedder,
        collection_name: str = "paper_chunks",
    ) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        migrate_legacy_chroma_configurations(path)
        self.client = chromadb.PersistentClient(path=str(path))
        dimensions = getattr(embedder, "dimensions", None)
        collection_metadata: dict[str, str | int] = {
            "hnsw:space": "cosine",
            "embedding_model": embedder.model_name,
        }
        if dimensions is not None:
            collection_metadata["embedding_dimensions"] = int(dimensions)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata=collection_metadata,
        )
        existing_model = (self.collection.metadata or {}).get("embedding_model")
        if existing_model != embedder.model_name:
            raise ValueError(
                "The Chroma collection was created with a different embedding model. "
                "Rebuild the index before continuing."
            )
        existing_dimensions = (self.collection.metadata or {}).get("embedding_dimensions")
        if dimensions is not None and existing_dimensions is not None:
            if int(existing_dimensions) != int(dimensions):
                raise ValueError(
                    "The Chroma collection was created with incompatible embedding "
                    "dimensions. Rebuild the index before continuing."
                )
        elif dimensions is not None and self.collection.count() == 0:
            # A legacy empty collection may omit dimensions. The first upsert still
            # validates the returned width; avoid mutating Chroma's immutable HNSW keys.
            pass
        elif dimensions is not None and self.collection.count() > 0:
            sample = self.collection.get(limit=1, include=["embeddings"])
            vectors = sample.get("embeddings")
            observed = len(vectors[0]) if vectors is not None and len(vectors) else 0
            if observed and observed != int(dimensions):
                raise ValueError(
                    "The Chroma collection contains incompatible embedding dimensions. "
                    "Rebuild the index before continuing."
                )

    def upsert(self, chunks: Sequence[DocumentChunk]) -> None:
        if not chunks:
            return
        embeddings = self.embedder.embed_passages(
            [chunk.text for chunk in chunks],
            [chunk.paper_title for chunk in chunks],
        )
        if embeddings.ndim != 2 or embeddings.shape[0] != len(chunks):
            raise ValueError("Embedder returned an invalid passage matrix")
        dimensions = int(embeddings.shape[1])
        configured_dimensions = getattr(self.embedder, "dimensions", None)
        if configured_dimensions is not None and dimensions != int(configured_dimensions):
            raise ValueError(
                "Embedder returned vectors with dimensions that do not match its configuration"
            )
        stored_dimensions = (self.collection.metadata or {}).get("embedding_dimensions")
        if stored_dimensions is not None and dimensions != int(stored_dimensions):
            raise ValueError("Embedding dimensions do not match the existing Chroma collection")
        for paper_id in {chunk.paper_id for chunk in chunks}:
            self.collection.delete(where={"paper_id": paper_id})
        batch_size = 128
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            self.collection.upsert(
                ids=[chunk.chunk_id for chunk in batch],
                documents=[chunk.text for chunk in batch],
                metadatas=[chunk.chroma_metadata() for chunk in batch],
                embeddings=embeddings[start : start + len(batch)].tolist(),
            )

    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        if self.collection.count() == 0:
            return []
        query_embedding = self.embedder.embed_query(query)
        return self._search_embedding(
            query_embedding,
            top_k=top_k,
            paper_ids=paper_ids,
            section_types=section_types,
        )

    def search_balanced(
        self,
        query: str,
        paper_ids: Sequence[str],
        *,
        top_k: int = 6,
        per_paper: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        """Retrieve per-paper evidence with one query embedding API call."""
        if self.collection.count() == 0:
            return []
        query_embedding = self.embedder.embed_query(query)
        buckets = {
            paper_id: self._search_embedding(
                query_embedding,
                top_k=per_paper,
                paper_ids={paper_id},
                section_types=section_types,
            )
            for paper_id in paper_ids
        }
        selected: list[Evidence] = []
        while len(selected) < top_k and any(buckets.values()):
            for paper_id in paper_ids:
                if buckets[paper_id] and len(selected) < top_k:
                    selected.append(buckets[paper_id].pop(0))
        return selected

    def _search_embedding(
        self,
        query_embedding: np.ndarray,
        *,
        top_k: int,
        paper_ids: set[str] | None,
        section_types: set[str] | None,
    ) -> list[Evidence]:
        where = self._where_filter(paper_ids, section_types)
        result = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=min(top_k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
            **({"where": where} if where else {}),
        )
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        ids = result.get("ids", [[]])[0]

        evidence = []
        for chunk_id, text, metadata, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            evidence.append(
                Evidence(
                    chunk_id=chunk_id,
                    paper_id=str(metadata["paper_id"]),
                    paper_title=str(metadata["paper_title"]),
                    section=str(metadata["section"]),
                    section_type=str(metadata["section_type"]),
                    page_start=int(metadata["page_start"]),
                    page_end=int(metadata["page_end"]),
                    text=text,
                    similarity=max(-1.0, min(1.0, 1.0 - float(distance))),
                    workspace_id=str(metadata.get("workspace_id", "demo")),
                    document_id=str(metadata.get("document_id", metadata["paper_id"])),
                    filename=str(metadata.get("filename", "")),
                    chunk_type=str(metadata.get("chunk_type", "text")),
                    extraction_method=str(metadata.get("extraction_method", "text")),
                )
            )
        return evidence

    def count(self) -> int:
        return self.collection.count()

    def delete_document(self, document_id: str) -> None:
        """Delete all vectors that belong to one source document."""
        self.collection.delete(where={"paper_id": document_id})

    def delete_collection(self) -> None:
        """Delete this index collection from persistent storage."""
        self.client.delete_collection(self.collection.name)

    @staticmethod
    def _where_filter(
        paper_ids: set[str] | None,
        section_types: set[str] | None,
    ) -> dict | None:
        clauses = []
        if paper_ids:
            values = sorted(paper_ids)
            clauses.append(
                {"paper_id": values[0]} if len(values) == 1 else {"paper_id": {"$in": values}}
            )
        if section_types:
            values = sorted(section_types)
            clauses.append(
                {"section_type": values[0]}
                if len(values) == 1
                else {"section_type": {"$in": values}}
            )
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}


class FallbackEmbeddingIndex:
    """Use a mirrored local index only after all cloud credentials are exhausted."""

    def __init__(
        self,
        primary: ChromaEmbeddingIndex,
        fallback_factory: Callable[[], ChromaEmbeddingIndex],
    ) -> None:
        self.primary = primary
        self._fallback_factory = fallback_factory
        self._fallback_index: ChromaEmbeddingIndex | None = None
        self._lock = threading.Lock()
        self.last_backend = "primary"

    def upsert(self, chunks: Sequence[DocumentChunk]) -> None:
        primary_error: ProviderFailoverError | None = None
        try:
            self.primary.upsert(chunks)
        except ProviderFailoverError as error:
            primary_error = error
        self._fallback().upsert(chunks)
        self.last_backend = "fallback" if primary_error else "primary"

    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        try:
            result = self.primary.search(query, top_k, paper_ids, section_types)
            self.last_backend = "primary"
            return result
        except ProviderFailoverError:
            self.last_backend = "fallback"
            return self._fallback().search(query, top_k, paper_ids, section_types)

    def search_balanced(
        self,
        query: str,
        paper_ids: Sequence[str],
        *,
        top_k: int = 6,
        per_paper: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        try:
            result = self.primary.search_balanced(
                query,
                paper_ids,
                top_k=top_k,
                per_paper=per_paper,
                section_types=section_types,
            )
            self.last_backend = "primary"
            return result
        except ProviderFailoverError:
            self.last_backend = "fallback"
            return self._fallback().search_balanced(
                query,
                paper_ids,
                top_k=top_k,
                per_paper=per_paper,
                section_types=section_types,
            )

    def count(self) -> int:
        return self.primary.count()

    def failover_status(self) -> dict[str, object]:
        embedder = self.primary.embedder
        status = (
            embedder.failover_status()
            if hasattr(embedder, "failover_status")
            else {"credential_count": 1, "active_slot": 1}
        )
        return {**status, "last_backend": self.last_backend, "local_fallback": True}

    def _fallback(self) -> ChromaEmbeddingIndex:
        if self._fallback_index is not None:
            return self._fallback_index
        with self._lock:
            if self._fallback_index is None:
                self._fallback_index = self._fallback_factory()
        return self._fallback_index
