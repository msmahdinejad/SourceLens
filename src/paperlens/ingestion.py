from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from paperlens.corpus.chunking import SectionAwareChunker
from paperlens.domain import (
    DocumentChunk,
    DocumentManifestRecord,
    ExtractedDocument,
    PageContent,
)
from paperlens.extraction_policy import ExtractionPolicy, PageVisualSignals
from paperlens.ocr import OCRAdapter
from paperlens.pdf_backend import PageGeometry, open_image, open_pdf
from paperlens.vlm import VLMAdapter, VLMResult
from paperlens.voice import SpeechTranscriber
from paperlens.workspaces import ProcessingEvent

ExtractionMethod = Literal["text", "ocr", "vlm", "hybrid", "transcription"]
SourceType = Literal["pdf", "text", "image", "audio"]
_WHITESPACE = re.compile(r"[ \t]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_WORD = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_VISUAL_CUE = re.compile(
    r"\b(?:figure|fig\.?|table|chart|diagram|architecture|equation|algorithm)\b|"
    r"(?:شکل|جدول|نمودار|معماری|معادله|الگوریتم)",
    re.IGNORECASE,
)


class PageExtraction(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    raw_text: str = ""
    normalized_text: str = ""
    extraction_method: ExtractionMethod
    confidence: float = Field(ge=0.0, le=1.0)
    detected_language: str = "unknown"
    embedded_text_usable: bool = False
    warnings: list[str] = Field(default_factory=list)
    visual_summary: str = ""
    tables: list[dict[str, Any]] = Field(default_factory=list)
    figures: list[dict[str, Any]] = Field(default_factory=list)
    source_artifact: str | None = None
    provider_requests: list[dict[str, Any]] = Field(default_factory=list)


class DocumentIngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_id: str
    document_id: str
    original_filename: str
    source_type: SourceType = "pdf"
    title: str
    page_count: int
    sha256: str
    pages: list[PageExtraction]
    chunks: list[DocumentChunk]
    language_hints: list[str]
    extraction_distribution: dict[str, int]
    warnings: list[str] = Field(default_factory=list)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class DocumentIngestionPipeline:
    """Page-aware PDF extraction, OCR/VLM fallback, cleanup, and chunking."""

    def __init__(
        self,
        *,
        ocr: OCRAdapter | None,
        vlm: VLMAdapter | None,
        transcriber: SpeechTranscriber | None = None,
        chunker: SectionAwareChunker | None = None,
        min_text_characters: int = 70,
        extraction_policy: ExtractionPolicy | None = None,
    ) -> None:
        self.ocr = ocr
        self.vlm = vlm
        self.transcriber = transcriber
        self.chunker = chunker or SectionAwareChunker()
        self.min_text_characters = min_text_characters
        self.extraction_policy = extraction_policy or ExtractionPolicy()

    def process(
        self,
        source_path: Path,
        *,
        workspace_id: str,
        document_id: str,
        original_filename: str,
        artifact_dir: Path | None = None,
        on_event: Callable[[ProcessingEvent], None] | None = None,
    ) -> DocumentIngestionResult:
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        source_type = self._source_type(source_path)
        if source_type == "text":
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="analysis",
                    status="running",
                    message="Preparing the text source.",
                    total_pages=1,
                ),
            )
            pages = [self._extract_text(source_path)]
            self._emit_page(on_event, pages[0], total_pages=1)
        elif source_type == "image":
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="analysis",
                    status="running",
                    message="Image ready for text and visual extraction.",
                    total_pages=1,
                ),
            )
            pages = [self._extract_image(source_path, artifact_dir, on_event)]
        elif source_type == "audio":
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="analysis",
                    status="running",
                    message="Audio source ready for transcription.",
                    total_pages=1,
                ),
            )
            pages = [self._extract_audio(source_path, on_event)]
            self._emit_page(on_event, pages[0], total_pages=1)
        else:
            pages = self._extract_pdf(source_path, artifact_dir, on_event)

        self._emit(
            on_event,
            ProcessingEvent(
                stage="chunking",
                status="running",
                message="Converting extracted text into retrievable chunks.",
                total_pages=len(pages),
            ),
        )
        result = self._finalize(
            source_path,
            source_type=source_type,
            pages=pages,
            workspace_id=workspace_id,
            document_id=document_id,
            original_filename=original_filename,
        )
        self._emit(
            on_event,
            ProcessingEvent(
                stage="chunking",
                status="completed",
                message=f"Prepared {len(result.chunks)} chunks for embedding.",
                total_pages=len(pages),
            ),
        )
        return result

    def analyze_image(
        self,
        content: bytes,
        *,
        original_filename: str = "prompt-image.png",
        on_event: Callable[[ProcessingEvent], None] | None = None,
    ) -> PageExtraction:
        """Analyze an in-memory prompt image without persisting or indexing it."""
        if not content:
            raise ValueError("Prompt image is empty")
        try:
            page = open_image(content)
        except Exception as error:
            raise ValueError(f"Unsupported prompt image: {original_filename}") from error
        return self._extract_page(
            page,
            page_number=1,
            artifact_dir=None,
            total_pages=1,
            on_event=on_event,
        )

    def _extract_pdf(
        self,
        source_path: Path,
        artifact_dir: Path | None,
        on_event: Callable[[ProcessingEvent], None] | None,
    ) -> list[PageExtraction]:
        pages: list[PageExtraction] = []
        with open_pdf(source_path) as document:
            if len(document) == 0:
                raise ValueError("PDF has no pages")
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="analysis",
                    status="running",
                    message=f"Detected {len(document)} pages.",
                    total_pages=len(document),
                ),
            )
            for index, page in enumerate(document):
                page_number = index + 1
                try:
                    extracted = self._extract_page(
                        page,
                        page_number=page_number,
                        artifact_dir=artifact_dir,
                        total_pages=len(document),
                        on_event=on_event,
                    )
                except Exception as error:  # noqa: BLE001 - isolate corrupt PDF pages
                    warning = (
                        f"Page processing failed: {type(error).__name__}: {error}"
                    )
                    extracted = PageExtraction(
                        page_number=page_number,
                        extraction_method="text",
                        confidence=0,
                        warnings=[warning],
                    )
                    self._emit(
                        on_event,
                        ProcessingEvent(
                            stage="analysis",
                            status="failed",
                            message=f"Page {page_number} could not be processed; continuing.",
                            page_number=page_number,
                            total_pages=len(document),
                        ),
                    )
                pages.append(extracted)
        return pages

    def _extract_image(
        self,
        source_path: Path,
        artifact_dir: Path | None,
        on_event: Callable[[ProcessingEvent], None] | None,
    ) -> PageExtraction:
        if artifact_dir is None:
            return self.analyze_image(
                source_path.read_bytes(),
                original_filename=source_path.name,
                on_event=on_event,
            )
        return self._extract_page(
            open_image(source_path.read_bytes()),
            page_number=1,
            artifact_dir=artifact_dir,
            total_pages=1,
            on_event=on_event,
        )

    def _extract_text(self, source_path: Path) -> PageExtraction:
        content = source_path.read_bytes()
        text = self._decode_text(content)
        normalized = self._normalize(text)
        if not normalized:
            raise ValueError("Text document is empty")
        return PageExtraction(
            page_number=1,
            raw_text=text,
            normalized_text=normalized,
            extraction_method="text",
            confidence=1.0,
            detected_language=self._detect_language(normalized),
            embedded_text_usable=True,
        )

    def _extract_audio(
        self,
        source_path: Path,
        on_event: Callable[[ProcessingEvent], None] | None,
    ) -> PageExtraction:
        if self.transcriber is None:
            raise RuntimeError(
                "Audio transcription is unavailable; configure Groq or local Whisper"
            )
        self._emit(
            on_event,
            ProcessingEvent(
                stage="transcription",
                status="running",
                message="Audio transcription started.",
                page_number=1,
                total_pages=1,
            ),
        )
        try:
            transcription = self.transcriber.transcribe_bytes(
                source_path.read_bytes(),
                source_path.suffix,
            )
        except Exception as error:
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="transcription",
                    status="failed",
                    message=(
                        f"Audio transcription failed: {type(error).__name__}: {error}"
                    ),
                    page_number=1,
                    total_pages=1,
                ),
            )
            raise
        normalized = self._normalize(transcription.text)
        page = PageExtraction(
            page_number=1,
            raw_text=transcription.text,
            normalized_text=normalized,
            extraction_method="transcription",
            confidence=max(0.65, transcription.language_probability),
            detected_language=transcription.language,
            embedded_text_usable=False,
            warnings=(
                [f"Speech fallback used after {transcription.primary_error}"]
                if transcription.fallback_used and transcription.primary_error
                else []
            ),
            provider_requests=transcription.requests
            or [
                {
                    "slot": 1,
                    "status": "completed",
                    "provider": transcription.provider,
                    "model": transcription.model,
                    "duration_seconds": transcription.duration_seconds,
                }
            ],
        )
        self._emit(
            on_event,
            ProcessingEvent(
                stage="transcription",
                status="completed",
                message="Audio transcription completed.",
                page_number=1,
                total_pages=1,
                provider=transcription.provider,
                text_preview=normalized[:500],
                requests=page.provider_requests,
            ),
        )
        return page

    def _finalize(
        self,
        source_path: Path,
        *,
        source_type: SourceType,
        pages: list[PageExtraction],
        workspace_id: str,
        document_id: str,
        original_filename: str,
    ) -> DocumentIngestionResult:

        pages = self._remove_repeated_noise(pages)
        title = self._detect_title(pages, Path(original_filename).stem)
        sha256 = self._sha256(source_path)
        chunks = self._chunk(
            pages,
            workspace_id=workspace_id,
            document_id=document_id,
            filename=original_filename,
            title=title,
            sha256=sha256,
        )
        languages = sorted(
            {page.detected_language for page in pages if page.detected_language != "unknown"}
        )
        return DocumentIngestionResult(
            workspace_id=workspace_id,
            document_id=document_id,
            original_filename=original_filename,
            source_type=source_type,
            title=title,
            page_count=len(pages),
            sha256=sha256,
            pages=pages,
            chunks=chunks,
            language_hints=languages,
            extraction_distribution=dict(Counter(page.extraction_method for page in pages)),
            warnings=[warning for page in pages for warning in page.warnings],
        )

    def _extract_page(
        self,
        page: PageGeometry,
        *,
        page_number: int,
        artifact_dir: Path | None,
        total_pages: int,
        on_event: Callable[[ProcessingEvent], None] | None,
    ) -> PageExtraction:
        raw_text = page.text()
        normalized = self._normalize(raw_text)
        quality_passed, text_confidence, quality_warnings = self._text_quality(normalized)
        # get_images(full=True) can expose every image resource in a PDF's shared
        # resource dictionary. get_image_info() reports only images actually
        # painted on this page, which avoids treating a plain multipage scan as a
        # page containing many independent figures.
        visual_signals = PageVisualSignals.from_page(page)
        usable = self.extraction_policy.embedded_text_is_sufficient(
            quality_passed=quality_passed,
            text=normalized,
            visuals=visual_signals,
        )
        image: bytes | None = None
        source_artifact: str | None = None
        warnings = list(quality_warnings)
        ocr_raw_text = ""
        ocr_text = ""
        ocr_confidence = 0.0
        ocr_requests: list[dict[str, Any]] = []
        vlm_requests: list[dict[str, Any]] = []
        visual = VLMResult(provider="not-run")

        if not usable:
            image = self._render(page)
            if artifact_dir is not None:
                artifact_dir.mkdir(parents=True, exist_ok=True)
                artifact = artifact_dir / f"page-{page_number:04d}.png"
                artifact.write_bytes(image)
                source_artifact = str(artifact)

        if not usable:
            if self.ocr is not None and self.ocr.available:
                self._emit(
                    on_event,
                    ProcessingEvent(
                        stage="ocr",
                        status="running",
                        message=f"OCR started for page {page_number}.",
                        page_number=page_number,
                        total_pages=total_pages,
                    ),
                )
                try:
                    ocr = self.ocr.extract(
                        image or self._render(page),
                        language_hint=self._detect_language(normalized),
                    )
                    ocr_raw_text = ocr.text
                    ocr_text = self._normalize(ocr_raw_text)
                    ocr_confidence = ocr.confidence
                    ocr_requests = ocr.requests
                    warnings.extend(ocr.warnings)
                    self._emit(
                        on_event,
                        ProcessingEvent(
                            stage="ocr",
                            status="completed",
                            message=f"OCR text received for page {page_number}.",
                            page_number=page_number,
                            total_pages=total_pages,
                            provider=ocr.provider,
                            text_preview=ocr_text[:500],
                            requests=ocr.requests,
                        ),
                    )
                except Exception as error:  # noqa: BLE001 - a page must not fail its PDF
                    ocr_requests = self._provider_attempts(self.ocr)
                    warnings.append(
                        f"OCR failed on page {page_number}: {type(error).__name__}: {error}"
                    )
                    self._emit(
                        on_event,
                        ProcessingEvent(
                            stage="ocr",
                            status="failed",
                            message=f"OCR failed for page {page_number}.",
                            page_number=page_number,
                            total_pages=total_pages,
                            requests=ocr_requests,
                        ),
                    )
            else:
                warnings.append(
                    "OCR skipped: configure OCR_SPACE_API_KEY or install local Tesseract"
                )
                self._emit(
                    on_event,
                    ProcessingEvent(
                        stage="ocr",
                        status="skipped",
                        message="OCR is not configured in this environment.",
                        page_number=page_number,
                        total_pages=total_pages,
                    ),
                )

        needs_vlm = self.extraction_policy.needs_vlm(
            embedded_text_usable=usable,
            extracted_text=normalized if usable else ocr_text,
            ocr_confidence=ocr_confidence,
            visuals=visual_signals,
        )
        if needs_vlm and self.vlm is not None and self.vlm.available:
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="vlm",
                    status="running",
                    message=f"Visual analysis started for page {page_number}.",
                    page_number=page_number,
                    total_pages=total_pages,
                ),
            )
            try:
                visual = self.vlm.analyze(image or self._render(page), page_number=page_number)
                vlm_requests = visual.requests
                warnings.extend(visual.warnings)
                self._emit(
                    on_event,
                    ProcessingEvent(
                        stage="vlm",
                        status="completed",
                        message=f"Visual analysis completed for page {page_number}.",
                        page_number=page_number,
                        total_pages=total_pages,
                        provider=visual.provider,
                        text_preview=visual.recognized_text[:500],
                        visual_preview=visual.visual_summary[:500],
                        requests=visual.requests,
                    ),
                )
            except Exception as error:  # noqa: BLE001 - visual enrichment is optional
                vlm_requests = self._provider_attempts(self.vlm)
                warnings.append(
                    f"VLM failed on page {page_number}: {type(error).__name__}: {error}"
                )
                self._emit(
                    on_event,
                    ProcessingEvent(
                        stage="vlm",
                        status="failed",
                        message=f"Visual analysis failed for page {page_number}.",
                        page_number=page_number,
                        total_pages=total_pages,
                        requests=vlm_requests,
                    ),
                )
        elif needs_vlm:
            warnings.append("VLM skipped: no VLM-capable provider is configured")
            self._emit(
                on_event,
                ProcessingEvent(
                    stage="vlm",
                    status="skipped",
                    message="No visual model is available in this environment.",
                    page_number=page_number,
                    total_pages=total_pages,
                ),
            )

        vlm_text = self._normalize(visual.recognized_text)
        final_text = normalized if usable else (ocr_text or vlm_text)
        if usable and visual.visual_summary:
            method: ExtractionMethod = "hybrid"
        elif usable:
            method = "text"
        elif ocr_text and visual.visual_summary:
            method = "hybrid"
        elif ocr_text:
            method = "ocr"
        elif visual.visual_summary or vlm_text:
            method = "vlm"
        else:
            method = "ocr"
            warnings.append("Page has no usable extracted text")

        confidence = max(text_confidence if usable else 0.0, ocr_confidence)
        if visual.visual_summary or vlm_text:
            confidence = max(confidence, visual.confidence)
        result = PageExtraction(
            page_number=page_number,
            raw_text=(raw_text if usable else (ocr_raw_text or visual.recognized_text or raw_text)),
            normalized_text=final_text,
            extraction_method=method,
            confidence=confidence,
            detected_language=self._detect_language(final_text),
            embedded_text_usable=usable,
            warnings=list(dict.fromkeys(warnings)),
            visual_summary=visual.visual_summary,
            tables=visual.tables,
            figures=visual.figures,
            source_artifact=source_artifact,
            provider_requests=[*ocr_requests, *vlm_requests],
        )
        self._emit_page(on_event, result, total_pages=total_pages)
        return result

    @staticmethod
    def _provider_attempts(adapter: Any) -> list[dict[str, Any]]:
        status_method = getattr(adapter, "status", None)
        if not callable(status_method):
            return []
        try:
            status = status_method()
        except Exception:  # noqa: BLE001 - diagnostics must not mask extraction
            return []
        attempts = status.get("last_attempts", []) if isinstance(status, dict) else []
        return [dict(item) for item in attempts if isinstance(item, dict)]

    @staticmethod
    def _source_type(path: Path) -> SourceType:
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".txt", ".md", ".csv", ".json"}:
            return "text"
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            return "image"
        if suffix in {".wav", ".mp3", ".ogg", ".m4a", ".mp4", ".webm"}:
            return "audio"
        raise ValueError(f"Unsupported source type: {suffix or 'unknown'}")

    @staticmethod
    def _emit(
        callback: Callable[[ProcessingEvent], None] | None,
        event: ProcessingEvent,
    ) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            # Progress reporting must never abort document extraction.
            return

    def _emit_page(
        self,
        callback: Callable[[ProcessingEvent], None] | None,
        page: PageExtraction,
        *,
        total_pages: int,
    ) -> None:
        self._emit(
            callback,
            ProcessingEvent(
                stage="page",
                status="completed",
                message=f"Processed page {page.page_number}.",
                page_number=page.page_number,
                total_pages=total_pages,
                extraction_method=page.extraction_method,
                text_preview=page.normalized_text[:500],
                visual_preview=page.visual_summary[:500],
            ),
        )

    @staticmethod
    def _decode_text(content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-16"):
            try:
                return content.decode(encoding)
            except UnicodeError:
                continue
        raise ValueError("Text document encoding is not supported")

    @staticmethod
    def _needs_vlm(
        *,
        embedded_text_usable: bool,
        extracted_text: str,
        image_count: int,
        drawing_count: int,
    ) -> bool:
        """Compatibility helper for callers that do not have page geometry."""
        visuals = PageVisualSignals(
            image_count=image_count,
            max_image_ratio=0.0,
            image_coverage_ratio=0.0,
            drawing_count=drawing_count,
            semantic_drawing_count=0,
            max_drawing_ratio=0.0,
            drawing_coverage_ratio=0.0,
        )
        return ExtractionPolicy().needs_vlm(
            embedded_text_usable=embedded_text_usable,
            extracted_text=extracted_text,
            ocr_confidence=0.0,
            visuals=visuals,
        )

    def _chunk(
        self,
        pages: list[PageExtraction],
        *,
        workspace_id: str,
        document_id: str,
        filename: str,
        title: str,
        sha256: str,
    ) -> list[DocumentChunk]:
        record = DocumentManifestRecord(
            paper_id=document_id,
            file_name=filename,
            title=title,
            authors=[],
            year=0,
            venue="Uploaded PDF",
            source_url="",
        )
        extracted = ExtractedDocument(
            record=record,
            pages=[
                PageContent(page_number=page.page_number, markdown=page.normalized_text)
                for page in pages
                if page.normalized_text
            ],
            sha256=sha256,
        )
        methods = {page.page_number: page.extraction_method for page in pages}
        chunks = []
        for chunk in self.chunker.chunk(extracted):
            span_methods = {
                methods.get(number, "text")
                for number in range(chunk.page_start, chunk.page_end + 1)
            }
            extraction_method: ExtractionMethod = (
                next(iter(span_methods)) if len(span_methods) == 1 else "hybrid"
            )
            source_pages = [
                page for page in pages if chunk.page_start <= page.page_number <= chunk.page_end
            ]
            embedded_text_usable = any(page.embedded_text_usable for page in source_pages)
            chunk_type = (
                "ocr"
                if extraction_method == "ocr"
                or (extraction_method == "hybrid" and not embedded_text_usable)
                else "text"
            )
            chunks.append(
                chunk.model_copy(
                    update={
                        "workspace_id": workspace_id,
                        "document_id": document_id,
                        "filename": filename,
                        "chunk_type": chunk_type,
                        "extraction_method": extraction_method,
                    }
                )
            )

        next_index = len(chunks)
        for page in pages:
            additions = [
                ("vlm_summary", page.visual_summary),
                *[
                    (
                        "table",
                        self._structured_summary("Table", table),
                    )
                    for table in page.tables
                ],
                *[
                    (
                        "figure",
                        self._structured_summary("Figure", figure),
                    )
                    for figure in page.figures
                ],
            ]
            for chunk_type, text in additions:
                if not text.strip():
                    continue
                chunk_id = self._chunk_id(document_id, next_index, text)
                chunks.append(
                    DocumentChunk(
                        chunk_id=chunk_id,
                        paper_id=document_id,
                        paper_title=title,
                        section="Visual evidence",
                        section_type="other",
                        page_start=page.page_number,
                        page_end=page.page_number,
                        chunk_index=next_index,
                        token_count=len(_WORD.findall(text)),
                        text=text,
                        source_sha256=sha256,
                        workspace_id=workspace_id,
                        document_id=document_id,
                        filename=filename,
                        chunk_type=chunk_type,
                        extraction_method=page.extraction_method,
                    )
                )
                next_index += 1
        return chunks

    def _text_quality(self, text: str) -> tuple[bool, float, list[str]]:
        warnings = []
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 18:
            warnings.append("Embedded text is too sparse")
            return False, min(0.45, len(compact) / max(1, self.min_text_characters)), warnings
        replacement_ratio = text.count("\ufffd") / max(1, len(text))
        visible_characters = [character for character in text if not character.isspace()]
        printable_ratio = sum(character.isprintable() for character in visible_characters) / max(
            1, len(visible_characters)
        )
        presentation_form_ratio = sum(
            "\ufb50" <= character <= "\ufdff" or "\ufe70" <= character <= "\ufeff"
            for character in compact
        ) / max(1, len(compact))
        if presentation_form_ratio > 0.08:
            warnings.append("Embedded Persian text uses garbled Arabic presentation forms")
            return False, 0.3, warnings
        words = _WORD.findall(text)
        word_count = len(words)
        single_token_ratio = sum(len(word) == 1 for word in words) / max(1, word_count)
        punctuation_ratio = sum(
            not character.isalnum() and character != "_" for character in compact
        ) / max(1, len(compact))
        minimum_words = 3 if len(compact) < self.min_text_characters else 10
        usable = (
            replacement_ratio < 0.02
            and printable_ratio > 0.92
            and word_count >= minimum_words
            and punctuation_ratio < 0.35
            and (word_count < 20 or single_token_ratio < 0.72)
        )
        if not usable:
            warnings.append("Embedded text appears garbled")
        elif len(compact) < self.min_text_characters:
            warnings.append("Embedded text is sparse but clean")
        confidence = min(0.99, 0.80 + min(len(compact), 1000) / 5000)
        return usable, confidence if usable else 0.35, warnings

    @staticmethod
    def _normalize(text: str) -> str:
        lines = [
            _WHITESPACE.sub(" ", line.replace("\x00", "")).strip() for line in text.splitlines()
        ]
        return _MANY_NEWLINES.sub("\n\n", "\n".join(lines)).strip()

    @staticmethod
    def _detect_language(text: str) -> str:
        persian = len(re.findall(r"[\u0600-\u06ff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        if persian > latin * 0.25 and persian > 3:
            return "fa"
        if latin > 3:
            return "en"
        return "unknown"

    @staticmethod
    def _render(page: PageGeometry) -> bytes:
        return page.render_png(scale=2)

    @staticmethod
    def _detect_title(pages: list[PageExtraction], fallback: str) -> str:
        if not pages:
            return fallback
        candidates = [
            line.strip(" #")
            for line in pages[0].normalized_text.splitlines()[:12]
            if 8 <= len(line.strip()) <= 180
        ]
        return candidates[0] if candidates else fallback

    @staticmethod
    def _remove_repeated_noise(pages: list[PageExtraction]) -> list[PageExtraction]:
        if len(pages) < 3:
            return pages
        margins: Counter[str] = Counter()
        for page in pages:
            lines = [line.strip() for line in page.normalized_text.splitlines() if line.strip()]
            margins.update(lines[:1] + lines[-1:])
        repeated = {
            line
            for line, count in margins.items()
            if count >= max(3, round(len(pages) * 0.6)) and len(line) <= 120
        }
        if not repeated:
            return pages
        cleaned = []
        for page in pages:
            lines = [
                line for line in page.normalized_text.splitlines() if line.strip() not in repeated
            ]
            cleaned.append(
                page.model_copy(
                    update={
                        "normalized_text": "\n".join(lines).strip(),
                        "warnings": [*page.warnings, "Repeated header/footer removed"],
                    }
                )
            )
        return cleaned

    @staticmethod
    def _structured_summary(label: str, value: dict[str, Any]) -> str:
        detail = json.dumps(value, ensure_ascii=False)
        return f"{label}: {detail}" if detail != "{}" else ""

    @staticmethod
    def _chunk_id(document_id: str, index: int, text: str) -> str:
        digest = hashlib.sha256(f"{document_id}\0{index}\0{text}".encode()).hexdigest()
        return f"{document_id}-{digest[:16]}"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
