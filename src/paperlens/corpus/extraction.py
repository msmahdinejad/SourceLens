from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from paperlens.domain import ExtractedPaper, PageContent, PaperRecord
from paperlens.pdf_backend import open_pdf

_REPEATED_PAGE_MARKER = re.compile(r"^\s*(?:page\s+)?\d+\s*$", re.IGNORECASE)


class PDFExtractor:
    """Extract sorted embedded text while retaining one-based physical page numbers."""

    def extract(self, record: PaperRecord, pdf_path: Path) -> ExtractedPaper:
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        pages: list[PageContent] = []
        with open_pdf(pdf_path) as document:
            if len(document) == 0:
                raise ValueError(f"No extractable pages found in {pdf_path.name}")
            for index, page in enumerate(document):
                markdown = self._clean_markdown(page.text())
                # Physical PDF page numbers are intentionally used for reproducible citations.
                pages.append(PageContent(page_number=index + 1, markdown=markdown))

        return ExtractedPaper(
            record=record,
            pages=pages,
            sha256=self._sha256(pdf_path),
        )

    @staticmethod
    def _clean_markdown(markdown: str) -> str:
        lines = []
        for line in markdown.replace("\x00", "").splitlines():
            stripped = line.strip()
            if _REPEATED_PAGE_MARKER.fullmatch(stripped):
                continue
            lines.append(line.rstrip())
        text = "\n".join(lines)
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def write_cache(extracted: ExtractedPaper, cache_path: Path) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(extracted.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
