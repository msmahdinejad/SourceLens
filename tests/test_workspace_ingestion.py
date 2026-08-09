from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from paperlens.domain import DocumentChunk, PaperChunk
from paperlens.ingestion import DocumentIngestionPipeline
from paperlens.ocr import OCRResult
from paperlens.vlm import VLMResult
from paperlens.voice import Transcription
from paperlens.workspace_fixtures import install_demo_fixture
from paperlens.workspaces import WorkspaceRecord, WorkspaceStore
from tests.pdf_fixtures import png_bytes, write_scanned_pdf, write_text_pdf


class FakeOCR:
    available = True
    label = "fake-ocr"

    def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
        assert image.startswith(b"\x89PNG")
        return OCRResult(
            text=(
                "Scanned Persian and English page content extracted by OCR. "
                "Figure 1 shows an architecture diagram."
            ),
            confidence=0.91,
            provider=self.label,
        )


def test_document_chunk_is_the_canonical_name_with_a_legacy_alias() -> None:
    assert PaperChunk is DocumentChunk


class FakeVLM:
    available = True
    label = "fake-vlm"

    def analyze(self, image: bytes, *, page_number: int) -> VLMResult:
        assert image.startswith(b"\x89PNG")
        return VLMResult(
            visual_summary="Architecture diagram with encoder and decoder blocks.",
            figures=[{"description": "Encoder-decoder architecture"}],
            tables=[],
            confidence=0.88,
            provider=self.label,
        )


class TextReadingVLM:
    available = True
    label = "text-reading-vlm"

    def analyze(self, image: bytes, *, page_number: int) -> VLMResult:
        return VLMResult(
            recognized_text=(
                "A scanned page recovered by the vision model with enough text "
                "to create retrievable evidence when OCR is unavailable."
            ),
            visual_summary="A text-only scanned page.",
            confidence=0.82,
            provider=self.label,
        )


def _write_text_pdf(path: Path) -> None:
    write_text_pdf(
        path,
        "A Production RAG Paper\n\n1 Introduction\nThis document explains retrieval "
        "augmented generation with enough embedded text for extraction.",
    )


def _write_scanned_pdf(path: Path) -> None:
    write_scanned_pdf(path, width=600, height=800)


def test_curated_fixture_migrates_legacy_demo_to_an_ordinary_workspace(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    demo_pdf = papers / "demo.pdf"
    _write_text_pdf(demo_pdf)
    (papers / "manifest.json").write_text(
        """[{
          "paper_id": "demo-paper",
          "file_name": "demo.pdf",
          "title": "Demo Paper",
          "authors": ["A. Author"],
          "year": 2026,
          "venue": "Demo",
          "source_url": "https://example.test/demo"
        }]""",
        encoding="utf-8",
    )

    store = WorkspaceStore(tmp_path / "workspaces", papers_path=papers)
    legacy = WorkspaceRecord(
        workspace_id="demo",
        name="Legacy papers demo",
        is_demo=True,
        documents=[],
    )
    store.save(legacy)
    demo = install_demo_fixture(store, papers)
    uploaded = store.create_workspace("Uploaded documents")
    document = store.add_document(uploaded.workspace_id, demo_pdf.read_bytes(), "notes.pdf")

    assert demo.is_demo is False
    assert demo.documents[0].title == "Demo Paper"
    assert Path(demo.documents[0].source_path).is_relative_to(store.root / "demo")
    assert (store.root / "demo" / "legacy-workspace.json").is_file()
    assert document.original_filename == "notes.pdf"
    assert document.status == "uploaded"
    assert store.document_path(uploaded.workspace_id, document.document_id).is_file()


def test_first_run_workspace_creation_is_safe_across_runtime_instances(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"

    def initialize() -> str:
        return WorkspaceStore(root).ensure_default_workspace().workspace_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: initialize(), range(12)))

    assert results == ["default"] * 12
    assert WorkspaceStore(root).get("default").documents == []


def test_workspace_store_accepts_text_and_image_but_rejects_spoofed_pdf(
    tmp_path: Path,
) -> None:
    store = WorkspaceStore(tmp_path / "workspaces")
    workspace = store.create_workspace("Mixed media")
    png = png_bytes(120, 80)

    text = store.add_document(
        workspace.workspace_id,
        "متن فارسی و English text".encode(),
        "notes.txt",
        content_type="text/plain",
    )
    image = store.add_document(
        workspace.workspace_id,
        png,
        "scan.png",
        content_type="image/png",
    )

    assert (text.source_type, text.media_type) == ("text", "text/plain")
    assert (image.source_type, image.media_type) == ("image", "image/png")
    with pytest.raises(ValueError, match="does not match"):
        store.add_document(
            workspace.workspace_id,
            b"not a pdf",
            "spoofed.pdf",
            content_type="application/pdf",
        )


def test_text_pdf_uses_embedded_text_and_preserves_chunk_metadata(tmp_path: Path) -> None:
    pdf = tmp_path / "text.pdf"
    _write_text_pdf(pdf)
    pipeline = DocumentIngestionPipeline(ocr=None, vlm=None)

    result = pipeline.process(
        pdf,
        workspace_id="workspace-a",
        document_id="document-a",
        original_filename="text.pdf",
    )

    assert result.page_count == 1
    assert result.pages[0].extraction_method == "text"
    assert result.pages[0].confidence >= 0.8
    assert result.chunks
    metadata = result.chunks[0].chroma_metadata()
    assert metadata["workspace_id"] == "workspace-a"
    assert metadata["document_id"] == "document-a"
    assert metadata["filename"] == "text.pdf"
    assert metadata["chunk_type"] == "text"
    assert metadata["page_start"] == 1


def test_scanned_pdf_uses_mocked_ocr_and_vlm_without_failing_document(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "scan.pdf"
    _write_scanned_pdf(pdf)
    pipeline = DocumentIngestionPipeline(ocr=FakeOCR(), vlm=FakeVLM())

    result = pipeline.process(
        pdf,
        workspace_id="workspace-a",
        document_id="scan-a",
        original_filename="scan.pdf",
    )

    page = result.pages[0]
    assert page.extraction_method == "hybrid"
    assert "extracted by OCR" in page.raw_text
    assert "extracted by OCR" in page.normalized_text
    assert "encoder and decoder" in page.visual_summary
    assert any(chunk.chunk_type == "ocr" for chunk in result.chunks)
    assert any(chunk.chunk_type == "vlm_summary" for chunk in result.chunks)


def test_scanned_pdf_can_build_chunks_from_vlm_when_ocr_is_unavailable(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "scan.pdf"
    _write_scanned_pdf(pdf)

    result = DocumentIngestionPipeline(ocr=None, vlm=TextReadingVLM()).process(
        pdf,
        workspace_id="workspace-a",
        document_id="scan-vlm",
        original_filename="scan.pdf",
    )

    assert result.pages[0].extraction_method == "vlm"
    assert "recovered by the vision model" in result.pages[0].normalized_text
    assert result.chunks


def test_dense_ocr_text_avoids_unnecessary_vlm_request(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    _write_scanned_pdf(pdf)

    class DenseOCR:
        available = True
        label = "dense-ocr"

        def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
            return OCRResult(
                text=(
                    "This is an ordinary scanned prose page with complete readable text. "
                    "It contains paragraphs about retrieval, indexing, evaluation, and "
                    "document processing. The prose continues with enough words and "
                    "characters to be confidently indexed without visual interpretation. "
                    "It is plain continuous prose with no special layout."
                ),
                confidence=0.94,
                provider=self.label,
            )

    class CountingVLM(FakeVLM):
        def __init__(self) -> None:
            self.calls = 0

        def analyze(self, image: bytes, *, page_number: int) -> VLMResult:
            self.calls += 1
            return super().analyze(image, page_number=page_number)

    vlm = CountingVLM()
    result = DocumentIngestionPipeline(ocr=DenseOCR(), vlm=vlm).process(
        pdf,
        workspace_id="workspace-a",
        document_id="dense-scan",
        original_filename="scan.pdf",
    )

    assert result.pages[0].extraction_method == "ocr"
    assert vlm.calls == 0


def test_multipage_scan_counts_only_images_drawn_on_each_page(tmp_path: Path) -> None:
    pdf = tmp_path / "multipage-scan.pdf"
    write_scanned_pdf(pdf, page_count=3)

    class DenseOCR:
        available = True
        label = "dense-ocr"

        def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
            return OCRResult(
                text=(
                    "This scanned page contains ordinary continuous prose with enough "
                    "readable words for retrieval, indexing, evaluation, citations, and "
                    "document processing without any extra visual interpretation. The "
                    "remaining sentences provide complete context and clear terminology."
                ),
                confidence=0.94,
                provider=self.label,
            )

    class CountingVLM(FakeVLM):
        def __init__(self) -> None:
            self.calls = 0

        def analyze(self, image: bytes, *, page_number: int) -> VLMResult:
            self.calls += 1
            return super().analyze(image, page_number=page_number)

    vlm = CountingVLM()
    result = DocumentIngestionPipeline(ocr=DenseOCR(), vlm=vlm).process(
        pdf,
        workspace_id="workspace-a",
        document_id="multipage-scan",
        original_filename=pdf.name,
    )

    assert result.extraction_distribution == {"ocr": 3}
    assert vlm.calls == 0


def test_pdf_page_failure_is_isolated_and_later_pages_continue(
    tmp_path: Path, monkeypatch
) -> None:
    class Page:
        width = 612.0
        height = 792.0

        def __init__(self, *, broken: bool) -> None:
            self.broken = broken

        def text(self) -> str:
            if self.broken:
                raise RuntimeError("corrupt page stream")
            return " ".join(["recoverable embedded document text"] * 20)

        def image_boxes(self):
            return []

        def drawing_boxes(self):
            return []

        def render_png(self, *, scale: float = 2.0) -> bytes:
            raise AssertionError("text page should not be rendered")

    class Document:
        pages = [Page(broken=True), Page(broken=False)]

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def __len__(self) -> int:
            return len(self.pages)

        def __iter__(self):
            return iter(self.pages)

    monkeypatch.setattr("paperlens.ingestion.open_pdf", lambda _path: Document())
    source = tmp_path / "partly-corrupt.pdf"
    source.write_bytes(b"%PDF-1.7")

    pages = DocumentIngestionPipeline(ocr=None, vlm=None)._extract_pdf(
        source, None, None
    )

    assert len(pages) == 2
    assert pages[0].confidence == 0
    assert "corrupt page stream" in pages[0].warnings[0]
    assert pages[1].confidence > 0
    assert pages[1].normalized_text


def test_punctuation_heavy_embedded_text_is_treated_as_garbled() -> None:
    pipeline = DocumentIngestionPipeline(ocr=FakeOCR(), vlm=None)
    garbled = " ".join(["!", "?", ".", ":"] * 80 + ["الف"] * 20)

    usable, _confidence, warnings = pipeline._text_quality(garbled)

    assert usable is False
    assert "garbled" in " ".join(warnings).casefold()
    assert pipeline._needs_vlm(
        embedded_text_usable=False,
        extracted_text="",
        image_count=0,
        drawing_count=1,
    )


def test_persian_presentation_form_text_is_sent_to_ocr_instead_of_indexed() -> None:
    pipeline = DocumentIngestionPipeline(ocr=FakeOCR(), vlm=None)
    visual_order_text = " ".join(
        ["\ufee3\ufeec\ufee8\ufeaa\ufeb3\ufbfd\ufb90\ufe8e\ufee3\ufb59\ufbff\ufeee\ufe97\ufeae"]
        * 30
    )

    usable, _confidence, warnings = pipeline._text_quality(visual_order_text)

    assert usable is False
    assert any("presentation forms" in warning for warning in warnings)


def test_text_image_and_audio_sources_become_retrievable_chunks(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text(
        "عنوان فارسی\nاین متن درباره بازیابی ترکیبی و شواهد قابل استناد است.",
        encoding="utf-8",
    )
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(png_bytes(400, 240, "Scanned image content"))
    audio_path = tmp_path / "lecture.wav"
    audio_path.write_bytes(b"RIFF" + (b"\x00" * 4) + b"WAVEfmt ")

    class FakeTranscriber:
        def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
            return Transcription(
                text=(
                    "This lecture explains semantic retrieval and document evidence "
                    "with enough transcription text for indexing."
                ),
                language="en",
                language_probability=0.98,
                duration_seconds=12.0,
                provider="fake-speech",
                model="fake-whisper",
            )

    pipeline = DocumentIngestionPipeline(
        ocr=FakeOCR(),
        vlm=None,
        transcriber=FakeTranscriber(),
    )
    text = pipeline.process(
        text_path,
        workspace_id="ws",
        document_id="text",
        original_filename=text_path.name,
    )
    image = pipeline.process(
        image_path,
        workspace_id="ws",
        document_id="image",
        original_filename=image_path.name,
    )
    audio = pipeline.process(
        audio_path,
        workspace_id="ws",
        document_id="audio",
        original_filename=audio_path.name,
    )

    assert text.source_type == "text" and text.chunks
    assert image.source_type == "image" and image.chunks
    assert audio.source_type == "audio" and audio.chunks
    assert audio.pages[0].extraction_method == "transcription"
