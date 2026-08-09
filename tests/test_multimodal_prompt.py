from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from paperlens import webapp
from paperlens.assistant import ResearchAssistant
from paperlens.domain import ResearchAnswer
from paperlens.ingestion import DocumentIngestionPipeline, PageExtraction
from paperlens.multimodal import prompt_image_context
from paperlens.ocr import OCRResult
from paperlens.vlm import VLMResult
from paperlens.workspaces import WorkspaceStore
from tests.pdf_fixtures import png_bytes


def _png() -> bytes:
    return png_bytes(text="Encoder -> Attention -> Decoder")


class PromptOCR:
    available = True

    def extract(self, _image: bytes, *, language_hint: str | None = None) -> OCRResult:
        return OCRResult(
            text="Figure 1. Encoder architecture with attention and decoder.",
            confidence=0.91,
            provider="mock-ocr",
        )


class PromptVLM:
    available = True

    def analyze(self, _image: bytes, *, page_number: int) -> VLMResult:
        return VLMResult(
            recognized_text="Encoder, Attention, Decoder",
            visual_summary="A left-to-right model architecture diagram.",
            figures=[{"description": "Encoder to decoder flow"}],
            confidence=0.94,
            provider="mock-vlm",
        )


def test_prompt_image_analysis_reuses_smart_ocr_and_vlm_pipeline() -> None:
    pipeline = DocumentIngestionPipeline(ocr=PromptOCR(), vlm=PromptVLM())

    result = pipeline.analyze_image(_png(), original_filename="architecture.png")

    assert result.extraction_method == "hybrid"
    assert "Encoder architecture" in result.normalized_text
    assert "left-to-right" in result.visual_summary
    assert result.figures


def test_prompt_image_evidence_does_not_change_the_question_language() -> None:
    analysis = PageExtraction(
        page_number=1,
        raw_text="رابط پژوهشی",
        normalized_text="رابط پژوهشی",
        extraction_method="ocr",
        confidence=0.9,
        detected_language="fa",
    )

    context = prompt_image_context("Describe the interface.", analysis)

    assert context.endswith("Current question:\nDescribe the interface.")
    assert ResearchAssistant._language_matches("It has three research panels.", context)


def test_multimodal_chat_keeps_image_analysis_with_the_submitted_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Prompt image")
    captured: dict[str, object] = {}

    class Analyzer:
        def analyze(self, content: bytes, *, original_filename: str):
            captured["image_bytes"] = content
            captured["filename"] = original_filename
            time.sleep(0.05)
            return DocumentIngestionPipeline(ocr=PromptOCR(), vlm=PromptVLM()).analyze_image(
                content,
                original_filename=original_filename,
            )

    def answer(question, history, workspace_id, document_ids):
        captured.update(
            question=question,
            history=history,
            workspace_id=workspace_id,
            document_ids=document_ids,
        )
        return ResearchAnswer(mode="rag", text="The image shows an encoder-decoder flow.")

    monkeypatch.setattr(webapp, "workspace_store", lambda: store)
    monkeypatch.setattr(webapp, "prompt_image_analyzer", lambda: Analyzer())
    monkeypatch.setattr(webapp, "_ask_workspace_locked", answer)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: (
            {"active_model": "test", "provider": "test"},
            {"last_backend": "test"},
        ),
    )

    response = TestClient(webapp.app).post(
        "/api/chat/image",
        data={
            "question": "Explain this architecture",
            "workspace_id": workspace.workspace_id,
            "document_ids": "[]",
            "history": "[]",
        },
        files={"image": ("architecture.png", _png(), "image/png")},
    )

    assert response.status_code == 200
    assert "Prompt image evidence" in str(captured["question"])
    assert "left-to-right" in str(captured["question"])
    assert response.json()["prompt_attachment"]["extraction_method"] == "hybrid"
    assert response.json()["prompt_attachment"]["provider_requests"] == []
    trace = TestClient(webapp.app).get(f"/api/traces/{response.json()['trace_id']}").json()
    assert trace["duration_ms"] >= 40


def test_multimodal_chat_defaults_to_the_empty_first_run_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    store.ensure_default_workspace()
    captured: dict[str, object] = {}

    class Analyzer:
        def analyze(self, content: bytes, *, original_filename: str):
            return DocumentIngestionPipeline(ocr=PromptOCR(), vlm=PromptVLM()).analyze_image(
                content,
                original_filename=original_filename,
            )

    def answer(question, history, workspace_id, document_ids):
        captured["workspace_id"] = workspace_id
        return ResearchAnswer(mode="rag", text="Image answer")

    monkeypatch.setattr(webapp, "workspace_store", lambda: store)
    monkeypatch.setattr(webapp, "prompt_image_analyzer", lambda: Analyzer())
    monkeypatch.setattr(webapp, "_ask_workspace_locked", answer)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: ({"active_model": "test"}, {"last_backend": "test"}),
    )

    response = TestClient(webapp.app).post(
        "/api/chat/image",
        data={"question": "Explain", "document_ids": "[]", "history": "[]"},
        files={"image": ("image.png", _png(), "image/png")},
    )

    assert response.status_code == 200
    assert captured["workspace_id"] == "default"
