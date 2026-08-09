from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from paperlens.domain import DocumentChunk
from paperlens.ingestion import (
    DocumentIngestionPipeline,
    DocumentIngestionResult,
    PageExtraction,
)
from paperlens.knowledge_base import WorkspaceKnowledgeBase
from paperlens.provider_failover import ProviderFailoverError
from paperlens.workspaces import WorkspaceStore
from tests.pdf_fixtures import write_text_pdf


class SmallEmbedder:
    model_name = "small-test"
    dimensions = 4

    def embed_passages(
        self,
        texts: Sequence[str],
        titles: Sequence[str] | None = None,
    ) -> np.ndarray:
        return np.stack([self._vector(text) for text in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        vector = np.asarray(
            [
                float("retrieval" in text.casefold()),
                float("transformer" in text.casefold()),
                float("persian" in text.casefold()),
                0.25,
            ],
            dtype=np.float32,
        )
        return vector / np.linalg.norm(vector)


class ConstantEmbedder:
    model_name = "constant-test"
    dimensions = 2

    def embed_passages(self, texts, titles=None) -> np.ndarray:
        return np.tile(np.asarray([[1.0, 0.0]], dtype=np.float32), (len(texts), 1))

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray([1.0, 0.0], dtype=np.float32)


class UnavailableQueryEmbedder(SmallEmbedder):
    def embed_query(self, text: str) -> np.ndarray:
        raise ProviderFailoverError("embedding service unavailable")


class ManyChunkPipeline:
    def process(self, source_path: Path, **kwargs) -> DocumentIngestionResult:
        chunks = [
            DocumentChunk(
                chunk_id=f"decoy-{index:04d}",
                paper_id=kwargs["document_id"],
                paper_title="Large fixture",
                section="Body",
                section_type="other",
                page_start=1,
                page_end=1,
                chunk_index=index,
                token_count=3,
                text=f"common filler chunk {index}",
                source_sha256="fixture",
                workspace_id=kwargs["workspace_id"],
                document_id=kwargs["document_id"],
                filename=kwargs["original_filename"],
            )
            for index in range(2000)
        ]
        chunks.append(
            chunks[-1].model_copy(
                update={
                    "chunk_id": "exact-target",
                    "chunk_index": 2000,
                    "text": "The exact identifier ZXQ-4097 appears only in this chunk.",
                }
            )
        )
        return DocumentIngestionResult(
            workspace_id=kwargs["workspace_id"],
            document_id=kwargs["document_id"],
            original_filename=kwargs["original_filename"],
            source_type="text",
            title="Large fixture",
            page_count=1,
            sha256="fixture",
            pages=[
                PageExtraction(
                    page_number=1,
                    normalized_text="Large fixture",
                    extraction_method="text",
                    confidence=1.0,
                )
            ],
            chunks=chunks,
            language_hints=["en"],
            extraction_distribution={"text": 1},
        )


def _pdf(path: Path, text: str) -> None:
    write_text_pdf(path, text)


def test_workspace_rebuild_records_collection_and_retrieves_page_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    _pdf(
        source,
        "Transformer Retrieval Study\n\n1 Introduction\n"
        "Hybrid retrieval combines vector similarity and lexical matching for documents.",
    )
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Research")
    document = store.add_document(
        workspace.workspace_id,
        source.read_bytes(),
        "retrieval.pdf",
    )
    knowledge_base = WorkspaceKnowledgeBase(
        store=store,
        vector_path=tmp_path / "vectors",
        embedder=SmallEmbedder(),
        pipeline=DocumentIngestionPipeline(ocr=None, vlm=None),
    )

    progress_updates: list[dict] = []
    report = knowledge_base.rebuild(
        workspace.workspace_id,
        on_progress=progress_updates.append,
    )
    evidence = knowledge_base.search(
        workspace.workspace_id,
        "lexical matching retrieval",
        document_ids={document.document_id},
        top_k=3,
    )
    inspection = knowledge_base.inspect(workspace.workspace_id)

    assert report["document_count"] == 1
    assert evidence
    assert evidence[0].document_id == document.document_id
    assert evidence[0].page_start == 1
    assert evidence[0].filename == "retrieval.pdf"
    assert inspection["embedding_model"] == "small-test"
    assert inspection["dimensions"] == 4
    assert inspection["document_count"] == 1
    assert inspection["chunk_count"] >= 1
    assert inspection["last_rebuild_at"]
    rebuilt_document = store.document(workspace.workspace_id, document.document_id)
    assert rebuilt_document.progress.percent == 100
    assert {event.stage for event in rebuilt_document.progress.events} >= {
        "analysis",
        "page",
        "embedding",
        "completed",
    }
    assert progress_updates[-1]["progress"] == 100


def test_workspace_search_falls_back_to_lexical_when_query_embedding_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    _pdf(
        source,
        "Retail Charging Evidence\n\nSmart charging aligns site demand with grid capacity.",
    )
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Resilient retrieval")
    document = store.add_document(workspace.workspace_id, source.read_bytes(), source.name)
    knowledge_base = WorkspaceKnowledgeBase(
        store=store,
        vector_path=tmp_path / "vectors",
        embedder=UnavailableQueryEmbedder(),
        pipeline=DocumentIngestionPipeline(ocr=None, vlm=None),
    )
    knowledge_base.process_document(workspace.workspace_id, document.document_id)

    evidence = knowledge_base.search(
        workspace.workspace_id,
        "grid capacity charging",
        document_ids={document.document_id},
    )

    assert evidence
    assert evidence[0].document_id == document.document_id


def test_document_processing_persists_live_progress_and_page_events(
    tmp_path: Path,
) -> None:
    source = tmp_path / "progress.pdf"
    _pdf(
        source,
        "Progress Report\n\nThis document contains enough text to track extraction, "
        "chunking, embedding, and completion as observable processing stages.",
    )
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Progress")
    document = store.add_document(workspace.workspace_id, source.read_bytes(), source.name)
    knowledge_base = WorkspaceKnowledgeBase(
        store=store,
        vector_path=tmp_path / "vectors",
        embedder=SmallEmbedder(),
        pipeline=DocumentIngestionPipeline(ocr=None, vlm=None),
    )

    knowledge_base.process_document(workspace.workspace_id, document.document_id)
    completed = store.document(workspace.workspace_id, document.document_id)

    assert completed.progress.stage == "completed"
    assert completed.progress.percent == 100
    assert completed.progress.processed_units == 1
    assert completed.progress.total_units == 1
    assert completed.progress.extraction_counts == {"text": 1}
    assert {event.stage for event in completed.progress.events} >= {
        "analysis",
        "page",
        "embedding",
        "completed",
    }
    assert any(event.text_preview for event in completed.progress.events)


def test_exact_identifier_retrieval_is_not_limited_to_a_2000_chunk_scan(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Large")
    store.add_document(workspace.workspace_id, b"fixture", "large.txt")
    knowledge_base = WorkspaceKnowledgeBase(
        store=store,
        vector_path=tmp_path / "vectors",
        embedder=ConstantEmbedder(),
        pipeline=ManyChunkPipeline(),
    )
    knowledge_base.rebuild(workspace.workspace_id)

    evidence = knowledge_base.search(workspace.workspace_id, "ZXQ-4097", top_k=1)

    assert evidence[0].chunk_id == "exact-target"
    assert evidence[0].page_start == 1


def test_reconcile_metadata_backfills_ready_documents_from_active_index(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Legacy metadata")
    document = store.add_document(workspace.workspace_id, b"fixture", "legacy.txt")
    knowledge_base = WorkspaceKnowledgeBase(
        store=store,
        vector_path=tmp_path / "vectors",
        embedder=ConstantEmbedder(),
        pipeline=ManyChunkPipeline(),
    )
    knowledge_base.process_document(workspace.workspace_id, document.document_id)
    store.update_document(
        workspace.workspace_id,
        document.document_id,
        chunk_count=0,
        language_hints=[],
    )

    first = knowledge_base.reconcile_document_metadata(workspace.workspace_id)
    second = knowledge_base.reconcile_document_metadata(workspace.workspace_id)
    reconciled = store.document(workspace.workspace_id, document.document_id)

    assert first == {"documents_checked": 1, "documents_updated": 1, "chunk_count": 2001}
    assert second == {"documents_checked": 1, "documents_updated": 0, "chunk_count": 2001}
    assert reconciled.chunk_count == 2001
    assert reconciled.language_hints == ["en"]
