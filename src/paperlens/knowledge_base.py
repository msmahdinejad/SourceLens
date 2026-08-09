from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from paperlens.corpus.index import ChromaEmbeddingIndex, TextEmbedder
from paperlens.domain import DocumentChunk, Evidence
from paperlens.ingestion import DocumentIngestionPipeline, DocumentIngestionResult
from paperlens.provider_failover import ProviderFailoverError
from paperlens.retrieval import SQLiteFTSIndex, reciprocal_rank_fusion
from paperlens.workspaces import (
    ProcessingEvent,
    ProcessingProgress,
    WorkspaceStore,
    utc_now,
)


class WorkspaceKnowledgeBase:
    """Owns page ingestion, versioned vector rebuilds, hybrid search, and inspection."""

    def __init__(
        self,
        *,
        store: WorkspaceStore,
        vector_path: Path,
        embedder: TextEmbedder,
        pipeline: DocumentIngestionPipeline,
    ) -> None:
        self.store = store
        self.vector_path = vector_path.resolve()
        self.embedder = embedder
        self.pipeline = pipeline
        self.lexical_index = SQLiteFTSIndex(self.vector_path / "retrieval.sqlite3")

    def process_document(
        self,
        workspace_id: str,
        document_id: str,
    ) -> DocumentIngestionResult:
        workspace = self.store.get(workspace_id)
        collection_id = workspace.vector_collection_id or self._collection_name(workspace_id)
        self.store.update_document(
            workspace_id,
            document_id,
            status="processing",
            errors=[],
            progress=ProcessingProgress(
                stage="analysis",
                started_at=utc_now(),
            ),
        )

        def record(event: ProcessingEvent) -> None:
            current = self.store.document(workspace_id, document_id).progress
            total = event.total_pages or current.total_units
            processed = current.processed_units
            if event.stage == "page" and event.status == "completed":
                processed = max(processed, event.page_number or 0)
            percent = min(84, round(processed / total * 84)) if total else current.percent
            self.store.record_progress(
                workspace_id,
                document_id,
                event,
                percent=percent,
                processed_units=processed,
                total_units=total,
                current_unit=event.page_number,
            )

        try:
            result = self.pipeline.process(
                self.store.document_path(workspace_id, document_id),
                workspace_id=workspace_id,
                document_id=document_id,
                original_filename=self.store.document(workspace_id, document_id).original_filename,
                artifact_dir=self.store.extraction_path(workspace_id, document_id).parent / "pages",
                on_event=record,
            )
            self.store.record_progress(
                workspace_id,
                document_id,
                ProcessingEvent(
                    stage="embedding",
                    status="running",
                    message=f"Embedding started for {len(result.chunks)} chunks.",
                    total_pages=result.page_count,
                ),
                percent=90,
                processed_units=result.page_count,
                total_units=result.page_count,
            )
            self._index(collection_id).upsert(result.chunks)
            self.lexical_index.replace_document(collection_id, document_id, result.chunks)
            result.write(self.store.extraction_path(workspace_id, document_id))
            updated = self.store.update_document(
                workspace_id,
                document_id,
                title=result.title,
                page_count=result.page_count,
                extraction_methods={
                    str(page.page_number): page.extraction_method for page in result.pages
                },
                language_hints=result.language_hints,
                chunk_count=len(result.chunks),
                embedding_model=self.embedder.model_name,
                vector_collection_id=collection_id,
                status="ready",
                warnings=result.warnings,
                errors=[],
            )
            workspace = self.store.get(workspace_id)
            self.store.save(
                workspace.model_copy(
                    update={
                        "embedding_model": self.embedder.model_name,
                        "vector_collection_id": collection_id,
                        "documents": [
                            updated if item.document_id == document_id else item
                            for item in workspace.documents
                        ],
                    }
                )
            )
            self.store.record_progress(
                workspace_id,
                document_id,
                ProcessingEvent(
                    stage="completed",
                    status="completed",
                    message="Document is ready for retrieval and questions.",
                    total_pages=result.page_count,
                ),
                percent=100,
                processed_units=result.page_count,
                total_units=result.page_count,
                finished=True,
            )
            return result
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            self.store.record_progress(
                workspace_id,
                document_id,
                ProcessingEvent(
                    stage="failed",
                    status="failed",
                    message=f"Processing stopped: {detail}",
                ),
                finished=True,
            )
            self.store.update_document(
                workspace_id,
                document_id,
                status="failed",
                errors=[detail],
            )
            raise

    def rebuild(
        self,
        workspace_id: str,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        workspace = self.store.get(workspace_id)
        collection_id = self._collection_name(workspace_id, versioned=True)
        index = self._index(collection_id)
        processed = 0
        failures: list[dict[str, str]] = []
        total_chunks = 0

        document_total = len(workspace.documents)
        for document_index, document in enumerate(workspace.documents, start=1):
            self.store.update_document(
                workspace_id,
                document.document_id,
                status="processing",
                errors=[],
                progress=ProcessingProgress(
                    stage="analysis",
                    started_at=utc_now(),
                ),
            )

            def record(
                event: ProcessingEvent,
                *,
                current_document_id: str = document.document_id,
                current_filename: str = document.original_filename,
                current_document_index: int = document_index,
            ) -> None:
                current = self.store.document(workspace_id, current_document_id).progress
                total = event.total_pages or current.total_units
                completed_units = current.processed_units
                if event.stage == "page" and event.status == "completed":
                    completed_units = max(
                        completed_units,
                        event.page_number or 0,
                    )
                within_document = completed_units / total if total else 0.0
                overall = round(
                    ((current_document_index - 1) + within_document * 0.85)
                    / max(1, document_total)
                    * 100
                )
                self.store.record_progress(
                    workspace_id,
                    current_document_id,
                    event,
                    percent=min(84, round(within_document * 84)),
                    processed_units=completed_units,
                    total_units=total,
                    current_unit=event.page_number,
                )
                if on_progress:
                    on_progress(
                        {
                            "progress": overall,
                            "current_document": current_filename,
                            "document_index": current_document_index,
                            "document_total": document_total,
                            "stage": event.stage,
                        }
                    )

            try:
                result = self.pipeline.process(
                    self.store.document_path(workspace_id, document.document_id),
                    workspace_id=workspace_id,
                    document_id=document.document_id,
                    original_filename=document.original_filename,
                    artifact_dir=self.store.extraction_path(
                        workspace_id, document.document_id
                    ).parent
                    / "pages",
                    on_event=record,
                )
                self.store.record_progress(
                    workspace_id,
                    document.document_id,
                    ProcessingEvent(
                        stage="embedding",
                        status="running",
                        message=f"Embedding started for {len(result.chunks)} chunks.",
                        total_pages=result.page_count,
                    ),
                    percent=90,
                    processed_units=result.page_count,
                    total_units=result.page_count,
                )
                index.upsert(result.chunks)
                self.lexical_index.replace_document(
                    collection_id, document.document_id, result.chunks
                )
                result.write(self.store.extraction_path(workspace_id, document.document_id))
                self.store.update_document(
                    workspace_id,
                    document.document_id,
                    title=result.title,
                    page_count=result.page_count,
                    extraction_methods={
                        str(page.page_number): page.extraction_method for page in result.pages
                    },
                    language_hints=result.language_hints,
                    chunk_count=len(result.chunks),
                    embedding_model=self.embedder.model_name,
                    vector_collection_id=collection_id,
                    status="ready",
                    warnings=result.warnings,
                    errors=[],
                )
                processed += 1
                total_chunks += len(result.chunks)
                self.store.record_progress(
                    workspace_id,
                    document.document_id,
                    ProcessingEvent(
                        stage="completed",
                        status="completed",
                        message="Source rebuild completed.",
                        total_pages=result.page_count,
                    ),
                    percent=100,
                    processed_units=result.page_count,
                    total_units=result.page_count,
                    finished=True,
                )
            except Exception as error:  # noqa: BLE001 - rebuild continues per document
                message = f"{type(error).__name__}: {error}"
                self.store.record_progress(
                    workspace_id,
                    document.document_id,
                    ProcessingEvent(
                        stage="failed",
                        status="failed",
                        message=f"Rebuild stopped: {message}",
                    ),
                    finished=True,
                )
                self.store.update_document(
                    workspace_id,
                    document.document_id,
                    status="failed",
                    errors=[message],
                )
                failures.append({"document_id": document.document_id, "error": message})
            if on_progress:
                final_stage = (
                    "failed"
                    if failures and failures[-1].get("document_id") == document.document_id
                    else "completed"
                )
                on_progress(
                    {
                        "progress": round(document_index / max(1, document_total) * 100),
                        "current_document": document.original_filename,
                        "document_index": document_index,
                        "document_total": document_total,
                        "stage": final_stage,
                    }
                )

        rebuilt_at = utc_now()
        workspace = self.store.get(workspace_id)
        self.store.save(
            workspace.model_copy(
                update={
                    "embedding_model": self.embedder.model_name,
                    "vector_collection_id": collection_id,
                    "last_rebuild_at": rebuilt_at,
                }
            )
        )
        return {
            "workspace_id": workspace_id,
            "document_count": len(workspace.documents),
            "processed_documents": processed,
            "failed_documents": failures,
            "chunk_count": total_chunks,
            "embedding_model": self.embedder.model_name,
            "dimensions": self._dimensions(),
            "collection_name": collection_id,
            "last_rebuild_at": rebuilt_at,
        }

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int = 6,
        document_ids: set[str] | None = None,
        section_types: set[str] | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        chunk_types: set[str] | None = None,
    ) -> list[Evidence]:
        normalized = query.strip()
        if not normalized:
            raise ValueError("Query cannot be empty")
        workspace = self.store.get(workspace_id)
        if not workspace.vector_collection_id:
            return []
        index = self._index(workspace.vector_collection_id)
        try:
            vector = index.search(
                normalized,
                top_k=max(top_k * 2, 12),
                paper_ids=document_ids,
                section_types=section_types,
            )
        except ProviderFailoverError:
            vector = []
        self._ensure_lexical_index(index, workspace.vector_collection_id)
        lexical = self.lexical_index.search(
            workspace.vector_collection_id,
            normalized,
            limit=max(top_k * 2, 12),
            document_ids=document_ids,
            section_types=section_types,
        )
        merged = reciprocal_rank_fusion(vector, lexical)
        filtered = [
            item
            for item in merged
            if (page_start is None or item.page_end >= page_start)
            and (page_end is None or item.page_start <= page_end)
            and (not chunk_types or item.chunk_type in chunk_types)
        ]
        return filtered[:top_k]

    def search_balanced(
        self,
        workspace_id: str,
        query: str,
        document_ids: Sequence[str],
        *,
        top_k: int = 6,
        per_document: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        buckets = {
            document_id: self.search(
                workspace_id,
                query,
                top_k=per_document,
                document_ids={document_id},
                section_types=section_types,
            )
            for document_id in document_ids
        }
        selected: list[Evidence] = []
        while len(selected) < top_k and any(buckets.values()):
            for document_id in document_ids:
                if buckets[document_id] and len(selected) < top_k:
                    selected.append(buckets[document_id].pop(0))
        return selected

    def inspect(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.store.get(workspace_id)
        base = {
            "workspace_id": workspace_id,
            "document_count": len(workspace.documents),
            "chunk_count": 0,
            "embedding_model": workspace.embedding_model,
            "dimensions": self._dimensions(),
            "collection_name": workspace.vector_collection_id,
            "extraction_method_distribution": {},
            "top_sections": {},
            "top_pages": {},
            "last_rebuild_at": workspace.last_rebuild_at,
        }
        if not workspace.vector_collection_id:
            return base
        collection = self._index(workspace.vector_collection_id).collection
        payload = collection.get(include=["metadatas"])
        metadatas = payload.get("metadatas") or []
        methods = Counter(str(item.get("extraction_method", "text")) for item in metadatas)
        sections = Counter(str(item.get("section", "Unknown")) for item in metadatas)
        pages = Counter(
            f"{item.get('filename') or item.get('paper_title')} · p.{item.get('page_start')}"
            for item in metadatas
        )
        return {
            **base,
            "document_count": len(
                {str(item.get("document_id", item.get("paper_id"))) for item in metadatas}
            ),
            "chunk_count": collection.count(),
            "embedding_model": (collection.metadata or {}).get(
                "embedding_model", workspace.embedding_model
            ),
            "dimensions": int(
                (collection.metadata or {}).get("embedding_dimensions") or self._dimensions() or 0
            ),
            "extraction_method_distribution": dict(methods),
            "top_sections": dict(sections.most_common(10)),
            "top_pages": dict(pages.most_common(10)),
        }

    def reconcile_document_metadata(self, workspace_id: str) -> dict[str, int]:
        """Backfill stale ready-document summaries from the active ordinary index."""
        workspace = self.store.get(workspace_id)
        if not workspace.vector_collection_id:
            return {
                "documents_checked": 0,
                "documents_updated": 0,
                "chunk_count": 0,
            }
        collection = self._index(workspace.vector_collection_id).collection
        payload = collection.get(include=["documents", "metadatas"])
        indexed_text: dict[str, list[str]] = {}
        counts: Counter[str] = Counter()
        for text, metadata in zip(
            payload.get("documents") or [],
            payload.get("metadatas") or [],
            strict=True,
        ):
            document_id = str(metadata.get("document_id", metadata.get("paper_id", "")))
            if not document_id:
                continue
            counts[document_id] += 1
            indexed_text.setdefault(document_id, []).append(str(text))

        updated_documents = []
        updated_count = 0
        checked_count = 0
        for document in workspace.documents:
            count = counts.get(document.document_id, 0)
            if document.status != "ready" or not count:
                updated_documents.append(document)
                continue
            checked_count += 1
            languages = self._indexed_languages(
                workspace_id,
                document.document_id,
                indexed_text.get(document.document_id, []),
            )
            changes: dict[str, object] = {}
            if document.chunk_count != count:
                changes["chunk_count"] = count
            if not document.language_hints and languages:
                changes["language_hints"] = languages
            if changes:
                updated_count += 1
                changes["updated_at"] = utc_now()
                updated_documents.append(document.model_copy(update=changes))
            else:
                updated_documents.append(document)
        if updated_count:
            self.store.save(workspace.model_copy(update={"documents": updated_documents}))
        return {
            "documents_checked": checked_count,
            "documents_updated": updated_count,
            "chunk_count": sum(counts.values()),
        }

    def _indexed_languages(
        self,
        workspace_id: str,
        document_id: str,
        texts: Sequence[str],
    ) -> list[str]:
        with_extraction = self.store.extraction_path(workspace_id, document_id)
        if with_extraction.is_file():
            try:
                result = DocumentIngestionResult.model_validate_json(
                    with_extraction.read_text(encoding="utf-8")
                )
                if result.language_hints:
                    return result.language_hints
            except (OSError, ValueError):
                pass
        combined = "\n".join(texts)
        persian = len(re.findall(r"[\u0600-\u06ff]", combined))
        latin = len(re.findall(r"[A-Za-z]", combined))
        languages = []
        if persian > 3:
            languages.append("fa")
        if latin > 3:
            languages.append("en")
        return languages

    def extraction(self, workspace_id: str, document_id: str) -> DocumentIngestionResult:
        path = self.store.extraction_path(workspace_id, document_id)
        if not path.is_file():
            raise FileNotFoundError("Document has not been processed")
        return DocumentIngestionResult.model_validate_json(path.read_text(encoding="utf-8"))

    def delete_document(self, workspace_id: str, document_id: str) -> None:
        """Remove a source from metadata, artifacts, and the active vector collection."""
        workspace = self.store.get(workspace_id)
        self.store.document(workspace_id, document_id)
        if workspace.vector_collection_id:
            self._index(workspace.vector_collection_id).delete_document(document_id)
            self.lexical_index.delete_document(workspace.vector_collection_id, document_id)
        self.store.delete_document(workspace_id, document_id)

    def delete_workspace(self, workspace_id: str) -> None:
        """Remove workspace data and every collection referenced by its metadata."""
        workspace = self.store.get(workspace_id)
        collection_ids = {
            item
            for item in [
                workspace.vector_collection_id,
                *(document.vector_collection_id for document in workspace.documents),
            ]
            if item
        }
        for collection_id in collection_ids:
            try:
                self._index(collection_id).delete_collection()
                self.lexical_index.delete_collection(collection_id)
            except ValueError:
                continue
        self.store.delete_workspace(workspace_id)

    def _ensure_lexical_index(
        self,
        index: ChromaEmbeddingIndex,
        collection_id: str,
    ) -> None:
        if self.lexical_index.is_current(collection_id, index.count()):
            return
        payload = index.collection.get(include=["documents", "metadatas"])
        chunks = [
            DocumentChunk(
                chunk_id=str(chunk_id),
                paper_id=str(metadata["paper_id"]),
                paper_title=str(metadata["paper_title"]),
                section=str(metadata["section"]),
                section_type=str(metadata["section_type"]),
                page_start=int(metadata["page_start"]),
                page_end=int(metadata["page_end"]),
                chunk_index=int(metadata.get("chunk_index", 0)),
                token_count=int(metadata.get("token_count", 0)),
                text=str(text),
                source_sha256=str(metadata.get("source_sha256", "")),
                workspace_id=str(metadata.get("workspace_id", "default")),
                document_id=str(metadata.get("document_id", metadata["paper_id"])),
                filename=str(metadata.get("filename", "")),
                chunk_type=str(metadata.get("chunk_type", "text")),
                extraction_method=str(metadata.get("extraction_method", "text")),
            )
            for chunk_id, text, metadata in zip(
                payload.get("ids") or [],
                payload.get("documents") or [],
                payload.get("metadatas") or [],
                strict=True,
            )
        ]
        self.lexical_index.replace_collection(collection_id, chunks)

    def _index(self, collection_name: str) -> ChromaEmbeddingIndex:
        return ChromaEmbeddingIndex(
            self.vector_path,
            self.embedder,
            collection_name=collection_name,
        )

    def _collection_name(self, workspace_id: str, *, versioned: bool = False) -> str:
        safe = re.sub(r"[^a-z0-9]+", "-", workspace_id.casefold()).strip("-")[:24]
        model_payload = f"{self.embedder.model_name}:{self._dimensions()}".encode()
        model = hashlib.sha256(model_payload).hexdigest()[:8]
        suffix = f"-{uuid4().hex[:6]}" if versioned else ""
        return f"pl-{safe or 'workspace'}-{model}{suffix}"

    def _dimensions(self) -> int | None:
        value = getattr(self.embedder, "dimensions", None)
        return int(value) if value is not None else None

class WorkspaceCorpus:
    """Assistant-facing adapter bound to one workspace and optional documents."""

    def __init__(
        self,
        knowledge_base: WorkspaceKnowledgeBase,
        workspace_id: str,
        document_ids: set[str] | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.workspace_id = workspace_id
        self.document_ids = document_ids

    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        selected = paper_ids or self.document_ids
        return self.knowledge_base.search(
            self.workspace_id,
            query,
            top_k=top_k,
            document_ids=selected,
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
        selected = [
            item for item in paper_ids if not self.document_ids or item in self.document_ids
        ]
        return self.knowledge_base.search_balanced(
            self.workspace_id,
            query,
            selected,
            top_k=top_k,
            per_document=per_paper,
            section_types=section_types,
        )
