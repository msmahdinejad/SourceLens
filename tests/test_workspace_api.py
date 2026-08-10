from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from paperlens import webapp
from paperlens.domain import Evidence, ResearchAnswer
from paperlens.ingestion import DocumentIngestionPipeline
from paperlens.knowledge_base import WorkspaceKnowledgeBase
from paperlens.web_sources import (
    WebSearchOptions,
    WebSearchSession,
    WebSourceImportResult,
    WebSourceResult,
)
from paperlens.workspaces import WorkspaceStore
from tests.pdf_fixtures import pdf_bytes, png_bytes


class ApiEmbedder:
    model_name = "api-test"
    dimensions = 3

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
            [1.0, float("evidence" in text.casefold()), 0.25],
            dtype=np.float32,
        )
        return vector / np.linalg.norm(vector)


def _pdf() -> bytes:
    return pdf_bytes(
        [
            "Workspace Evidence Document\n\n1 Introduction\n"
            "This page contains enough evidence for page-aware retrieval and preview."
        ]
    )


@pytest.fixture
def workspace_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = WorkspaceStore(tmp_path / "workspaces")
    store.ensure_default_workspace()
    knowledge_base = WorkspaceKnowledgeBase(
        store=store,
        vector_path=tmp_path / "vectors",
        embedder=ApiEmbedder(),
        pipeline=DocumentIngestionPipeline(ocr=None, vlm=None),
    )
    monkeypatch.setattr(webapp, "workspace_store", lambda: store)
    monkeypatch.setattr(webapp, "knowledge_base", lambda: knowledge_base)
    webapp._workspace_rebuild_states.clear()
    webapp._processing_jobs.clear()
    return TestClient(webapp.app), store, knowledge_base


def test_workspace_upload_process_and_page_preview(workspace_runtime) -> None:
    client, store, knowledge_base = workspace_runtime
    created = client.post("/api/workspaces", json={"name": "My PDFs"})
    workspace_id = created.json()["workspace_id"]

    upload = client.post(
        f"/api/workspaces/{workspace_id}/documents?process=false",
        files=[("files", ("evidence.pdf", _pdf(), "application/pdf"))],
    )
    document_id = upload.json()["documents"][0]["document_id"]
    knowledge_base.process_document(workspace_id, document_id)

    documents = client.get(f"/api/workspaces/{workspace_id}/documents")
    page = client.get(f"/api/workspaces/{workspace_id}/documents/{document_id}/pages/1")
    image = client.get(f"/api/workspaces/{workspace_id}/documents/{document_id}/pages/1/image")
    vector = client.get(f"/api/workspaces/{workspace_id}/vector-store")

    assert upload.status_code == 202
    assert documents.json()[0]["status"] == "ready"
    assert documents.json()[0]["extraction_report"]["text_pages"] == 1
    assert page.json()["page_number"] == 1
    assert "enough evidence" in page.json()["normalized_text"]
    assert image.headers["content-type"] == "image/png"
    assert vector.json()["chunk_count"] >= 1
    assert vector.json()["dimensions"] == 3


def test_vector_inspection_does_not_report_runtime_failures_as_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Broken vector store")
    store.save(
        workspace.model_copy(update={"vector_collection_id": "broken-collection"})
    )
    monkeypatch.setattr(webapp, "workspace_store", lambda: store)
    monkeypatch.setattr(webapp, "migrate_legacy_chroma_configurations", lambda _path: 0)

    def broken_client(*_args, **_kwargs):
        raise RuntimeError("database schema is incompatible")

    monkeypatch.setattr(webapp.chromadb, "PersistentClient", broken_client)

    with pytest.raises(RuntimeError, match="schema is incompatible"):
        webapp._inspect_workspace_vector(workspace.workspace_id)


def test_demo_install_reprocesses_ready_documents_missing_extraction(
    workspace_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _ = workspace_runtime
    store.ensure_workspace("demo", "Demo")
    document = store.add_document("demo", _pdf(), "demo.pdf", "application/pdf")
    store.update_document("demo", document.document_id, status="ready")
    submitted = []
    monkeypatch.setattr(webapp, "seed_demo_workspace", lambda *args: store)
    monkeypatch.setattr(
        webapp,
        "_submit_document_job",
        lambda workspace_id, document_id: submitted.append((workspace_id, document_id)),
    )

    response = client.post("/api/demo")

    assert response.status_code == 202
    assert submitted == [("demo", document.document_id)]


def test_ready_document_metadata_is_migrated_explicitly_at_startup(
    workspace_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, knowledge_base = workspace_runtime
    workspace = store.create_workspace("Migrated demo")
    document = store.add_document(
        workspace.workspace_id,
        _pdf(),
        "demo.pdf",
        "application/pdf",
    )
    knowledge_base.process_document(workspace.workspace_id, document.document_id)
    expected = store.document(workspace.workspace_id, document.document_id)
    store.update_document(
        workspace.workspace_id,
        document.document_id,
        chunk_count=0,
        language_hints=[],
    )
    monkeypatch.setattr(webapp, "recover_studio_jobs", lambda: None)

    webapp.startup_application()

    response = client.get(f"/api/workspaces/{workspace.workspace_id}")

    migrated = response.json()["documents"][0]
    assert response.status_code == 200
    assert migrated["chunk_count"] == expected.chunk_count
    assert migrated["language_hints"] == ["en"]


def test_web_source_api_searches_then_imports_only_selected_results(
    workspace_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _ = workspace_runtime
    workspace = store.create_workspace("Web research")

    class FakeWebSources:
        def discover(self, workspace_id: str, options: WebSearchOptions) -> WebSearchSession:
            assert options.exa_search_type == "deep"
            return WebSearchSession(
                search_id="search-one",
                workspace_id=workspace_id,
                options=options,
                status="ready",
                results=[
                    WebSourceResult(
                        source_id="source-one",
                        title="Selected research",
                        url="https://example.com/research",
                        provider="exa",
                        providers=["exa", "tavily"],
                        highlights=["Relevant preview"],
                    ),
                    WebSourceResult(
                        source_id="source-two",
                        title="Not selected",
                        url="https://example.com/other",
                        provider="tavily",
                    ),
                ],
            )

        def import_selected(self, workspace_id: str, search_id: str, source_ids: list[str]):
            assert search_id == "search-one"
            assert source_ids == ["source-one"]
            document = store.add_document(
                workspace_id,
                b"Complete selected source text",
                "Selected research.txt",
                content_type="text/plain",
            )
            document = store.update_document(
                workspace_id,
                document.document_id,
                origin_url="https://example.com/research",
                origin_provider="exa",
            )
            return WebSourceImportResult(documents=[document])

    monkeypatch.setattr(webapp, "web_source_service", lambda: FakeWebSources())

    discovery = client.post(
        f"/api/workspaces/{workspace.workspace_id}/source-searches",
        json={
            "query": "high quality retrieval",
            "providers": ["exa", "tavily"],
            "max_results": 12,
            "exa_search_type": "deep",
        },
    )
    imported = client.post(
        f"/api/workspaces/{workspace.workspace_id}/source-searches/search-one/import",
        json={"source_ids": ["source-one"], "process": False},
    )

    assert discovery.status_code == 201
    assert discovery.json()["results"][0]["providers"] == ["exa", "tavily"]
    assert imported.status_code == 202
    assert len(imported.json()["documents"]) == 1
    assert imported.json()["documents"][0]["origin_url"] == "https://example.com/research"


def test_upload_rejects_non_pdf_without_persisting_document(workspace_runtime) -> None:
    client, store, _ = workspace_runtime
    workspace = store.create_workspace("Safety")

    response = client.post(
        f"/api/workspaces/{workspace.workspace_id}/documents?process=false",
        files=[("files", ("../../secret.pdf", b"not a pdf", "application/pdf"))],
    )

    assert response.status_code == 422
    assert store.get(workspace.workspace_id).documents == []


def test_scoped_chat_forwards_workspace_and_document_filters(
    workspace_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _ = workspace_runtime
    workspace = store.create_workspace("Scoped")
    document = store.add_document(workspace.workspace_id, _pdf(), "scope.pdf")
    captured: dict[str, Any] = {}

    def answer(question, history, workspace_id, document_ids):
        captured.update(
            {
                "question": question,
                "workspace_id": workspace_id,
                "document_ids": document_ids,
            }
        )
        return ResearchAnswer(
            mode="rag",
            text="Scoped answer [E1].",
            evidence=[
                Evidence(
                    chunk_id="chunk",
                    paper_id=document.document_id,
                    paper_title="Scope",
                    section="Introduction",
                    section_type="introduction",
                    page_start=1,
                    page_end=1,
                    text="Scoped evidence",
                    similarity=0.9,
                    workspace_id=workspace.workspace_id,
                    document_id=document.document_id,
                    filename="scope.pdf",
                )
            ],
        )

    monkeypatch.setattr(webapp, "_ask_workspace_locked", answer)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: (
            {"active_model": "test", "provider": "test"},
            {"last_backend": "test"},
        ),
    )
    response = client.post(
        "/api/chat",
        json={
            "question": "Use the selected document",
            "workspace_id": workspace.workspace_id,
            "document_ids": [document.document_id],
        },
    )

    assert response.status_code == 200
    assert captured["workspace_id"] == workspace.workspace_id
    assert captured["document_ids"] == (document.document_id,)
    assert response.json()["evidence"][0]["page_preview_url"].endswith("/pages/1")


def test_runtime_status_supports_workspace_corpus_without_legacy_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_status = {"active_model": "test-model", "active_slot": 2}
    embedding_status = {"last_backend": "configured", "active_slot": 3}
    model = SimpleNamespace(failover_status=lambda: model_status)
    embedder = SimpleNamespace(failover_status=lambda: embedding_status)
    current = SimpleNamespace(
        language_model=model,
        corpus=SimpleNamespace(
            knowledge_base=SimpleNamespace(embedder=embedder),
        ),
    )
    monkeypatch.setattr(webapp, "assistant", lambda: current)

    assert webapp._runtime_failover_status() == (model_status, embedding_status)


def test_mixed_media_upload_text_entry_and_live_progress(workspace_runtime) -> None:
    client, store, knowledge_base = workspace_runtime
    workspace = store.create_workspace("Mixed media")
    png = png_bytes(140, 90)
    wav = b"RIFF" + (b"\x00" * 4) + b"WAVEfmt "

    upload = client.post(
        f"/api/workspaces/{workspace.workspace_id}/documents?process=false",
        files=[
            ("files", ("notes.txt", b"Plain text evidence for retrieval.", "text/plain")),
            ("files", ("scan.png", png, "image/png")),
            ("files", ("lecture.wav", wav, "audio/wav")),
        ],
    )
    pasted = client.post(
        f"/api/workspaces/{workspace.workspace_id}/documents/text?process=false",
        json={"title": "Pasted research note", "text": "متن عادی برای جست‌وجو"},
    )

    assert upload.status_code == 202
    assert [item["source_type"] for item in upload.json()["documents"]] == [
        "text",
        "image",
        "audio",
    ]
    assert pasted.status_code == 202
    text_document = upload.json()["documents"][0]
    knowledge_base.process_document(
        workspace.workspace_id,
        text_document["document_id"],
    )
    progress = client.get(
        f"/api/workspaces/{workspace.workspace_id}/documents/"
        f"{text_document['document_id']}/progress?after=0"
    )
    source = client.get(
        f"/api/workspaces/{workspace.workspace_id}/documents/{text_document['document_id']}/source"
    )

    assert progress.status_code == 200
    assert progress.json()["stage"] == "completed"
    assert progress.json()["percent"] == 100
    assert progress.json()["events"]
    assert source.headers["content-type"].startswith("text/plain")


def test_duplicate_document_processing_is_rejected(
    workspace_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _knowledge_base = workspace_runtime
    workspace = store.create_workspace("Queue guard")
    document = store.add_document(workspace.workspace_id, _pdf(), "queued.pdf")

    class DeferredExecutor:
        @staticmethod
        def submit(*_args, **_kwargs):
            return None

    monkeypatch.setattr(webapp, "processing_executor", lambda: DeferredExecutor())
    route = f"/api/workspaces/{workspace.workspace_id}/documents/{document.document_id}/process"
    first = client.post(route)
    second = client.post(route)

    assert first.status_code == 202
    assert first.json()["progress"]["stage"] == "queued"
    assert second.status_code == 409
    webapp._processing_jobs.discard((workspace.workspace_id, document.document_id))


def test_documents_and_workspaces_can_be_deleted_safely(workspace_runtime) -> None:
    client, store, _knowledge_base = workspace_runtime
    workspace = store.create_workspace("Disposable research")
    document = store.add_document(workspace.workspace_id, _pdf(), "remove-me.pdf")
    source_path = Path(document.source_path)
    workspace_path = store.root / workspace.workspace_id

    removed_document = client.delete(
        f"/api/workspaces/{workspace.workspace_id}/documents/{document.document_id}"
    )

    assert removed_document.status_code == 204
    assert not source_path.exists()
    assert store.get(workspace.workspace_id).documents == []
    assert client.get(
        f"/api/workspaces/{workspace.workspace_id}/documents/{document.document_id}"
    ).status_code == 404

    removed_workspace = client.delete(f"/api/workspaces/{workspace.workspace_id}")

    assert removed_workspace.status_code == 204
    assert not workspace_path.exists()
    assert client.get(f"/api/workspaces/{workspace.workspace_id}").status_code == 404


def test_trace_error_details_are_complete_redacted_and_persistent(workspace_runtime) -> None:
    client, store, _knowledge_base = workspace_runtime
    workspace = store.create_workspace("Trace retention")
    started = time.perf_counter()
    trace = webapp._new_trace("ingestion", "Detailed failure", workspace_id=workspace.workspace_id)
    long_context = "page-analysis-context-" * 30

    try:
        raise RuntimeError(f"Provider failed while processing {long_context}")
    except RuntimeError as error:
        webapp._finish_trace(trace, started=started, error=error)

    with webapp._trace_lock:
        webapp._traces.clear()

    response = client.get(f"/api/traces/{trace['id']}")

    assert response.status_code == 200
    payload = response.json()
    assert long_context in payload["error"]
    assert payload["error_detail"]["type"] == "RuntimeError"
    assert long_context in payload["error_detail"]["message"]
    assert "test_trace_error_details_are_complete_redacted_and_persistent" in payload[
        "error_detail"
    ]["traceback"]
