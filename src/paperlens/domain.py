from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class DocumentManifestRecord(FrozenModel):
    paper_id: str
    file_name: str
    title: str
    authors: list[str]
    year: int
    venue: str
    source_url: str
    license_spdx: str = ""
    license_url: str = ""
    sha256: str = ""

    def pdf_path(self, papers_path: Path) -> Path:
        return papers_path / self.file_name


PaperRecord = DocumentManifestRecord


class PageContent(FrozenModel):
    page_number: int = Field(ge=1)
    markdown: str


class ExtractedDocument(FrozenModel):
    record: DocumentManifestRecord
    pages: list[PageContent]
    sha256: str


ExtractedPaper = ExtractedDocument


class SectionFragment(FrozenModel):
    section: str
    section_type: str
    page_number: int = Field(ge=1)
    text: str


class DocumentChunk(FrozenModel):
    chunk_id: str
    paper_id: str
    paper_title: str
    section: str
    section_type: str
    page_start: int
    page_end: int
    chunk_index: int
    token_count: int
    text: str
    source_sha256: str
    workspace_id: str = "default"
    document_id: str = ""
    filename: str = ""
    chunk_type: Literal["text", "table", "figure", "ocr", "vlm_summary"] = "text"
    extraction_method: Literal["text", "ocr", "vlm", "hybrid", "transcription"] = "text"

    def chroma_metadata(self) -> dict[str, str | int]:
        return {
            "paper_id": self.paper_id,
            "paper_title": self.paper_title,
            "workspace_id": self.workspace_id,
            "document_id": self.document_id or self.paper_id,
            "filename": self.filename,
            "section": self.section,
            "section_type": self.section_type,
            "chunk_type": self.chunk_type,
            "extraction_method": self.extraction_method,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_index": self.chunk_index,
            "token_count": self.token_count,
            "source_sha256": self.source_sha256,
        }


PaperChunk = DocumentChunk


class Evidence(FrozenModel):
    chunk_id: str
    paper_id: str
    paper_title: str
    section: str
    section_type: str
    page_start: int
    page_end: int
    text: str
    similarity: float = Field(ge=-1.0, le=1.0)
    workspace_id: str = "default"
    document_id: str = ""
    filename: str = ""
    chunk_type: Literal["text", "table", "figure", "ocr", "vlm_summary"] = "text"
    extraction_method: Literal["text", "ocr", "vlm", "hybrid", "transcription"] = "text"

    @property
    def citation_label(self) -> str:
        page = (
            f"p. {self.page_start}"
            if self.page_start == self.page_end
            else f"pp. {self.page_start}-{self.page_end}"
        )
        return f"{self.paper_title}, {self.section}, {page}"


class DocumentIngestionSummary(FrozenModel):
    paper_id: str
    page_count: int
    chunk_count: int
    sha256: str


PaperIngestionResult = DocumentIngestionSummary


class IngestionReport(FrozenModel):
    papers: list[DocumentIngestionSummary]

    @property
    def total_chunks(self) -> int:
        return sum(paper.chunk_count for paper in self.papers)


class ToolCallRecord(FrozenModel):
    name: str
    arguments: dict[str, Any]
    # Kept server-side for artifact provenance; excluded from the public chat payload.
    output: str | None = Field(default=None, exclude=True, repr=False)


class ResearchAnswer(FrozenModel):
    mode: Literal["rag", "general_fallback"]
    execution_path: Literal["rag", "agent_tool", "hybrid", "general_fallback"] = "rag"
    text: str
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
