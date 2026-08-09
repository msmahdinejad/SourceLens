from __future__ import annotations

from paperlens.ingestion import DocumentIngestionPipeline, PageExtraction


class PromptImageAnalyzer:
    """Analyze transient chat images through the configured OCR and vision pools."""

    def __init__(
        self,
        pipeline: DocumentIngestionPipeline,
        *,
        maximum_bytes: int = 15 * 1024 * 1024,
    ) -> None:
        self.pipeline = pipeline
        self.maximum_bytes = maximum_bytes

    def analyze(self, content: bytes, *, original_filename: str) -> PageExtraction:
        if len(content) > self.maximum_bytes:
            raise ValueError("Prompt image exceeds the 15 MB limit")
        return self.pipeline.analyze_image(
            content,
            original_filename=original_filename,
        )


def prompt_image_context(question: str, analysis: PageExtraction) -> str:
    """Attach extracted visual evidence to a chat turn without changing its scope."""
    sections = [
        "[Prompt image evidence]",
        f"Extraction method: {analysis.extraction_method}",
        f"Confidence: {analysis.confidence:.2f}",
    ]
    if analysis.normalized_text:
        sections.append(f"Recognized text:\n{analysis.normalized_text}")
    if analysis.visual_summary:
        sections.append(f"Visual summary:\n{analysis.visual_summary}")
    if analysis.tables:
        sections.append(f"Tables: {analysis.tables}")
    if analysis.figures:
        sections.append(f"Figures: {analysis.figures}")
    sections.append(
        "Use this transient image evidence together with the selected workspace sources. "
        "Do not invent visual details that are absent from this analysis. The language of "
        "the evidence must not change the language of the user's actual question."
    )
    sections.append(f"Current question:\n{question.strip()}")
    return "\n\n".join(sections)
