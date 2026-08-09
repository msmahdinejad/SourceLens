from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from paperlens.corpus.extraction import PDFExtractor
from paperlens.domain import PaperRecord
from tests.pdf_fixtures import write_text_pdf


def _write_pdf(path: Path) -> None:
    write_text_pdf(
        path,
        [
            "Abstract\nA compact summary.\n1 Introduction\n"
            "Residual learning helps optimization.",
            "2 Experiments\nRetail charging evaluation reports accessibility results.",
        ],
    )


def _record() -> PaperRecord:
    return PaperRecord(
        paper_id="sample",
        file_name="sample.pdf",
        title="Sample Residual Paper",
        authors=["A. Researcher"],
        year=2026,
        venue="Test Conference",
        source_url="https://example.com/sample.pdf",
    )


def test_real_pdf_extraction_keeps_physical_pages_and_source_hash(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _write_pdf(pdf_path)

    extracted = PDFExtractor().extract(_record(), pdf_path)

    assert [page.page_number for page in extracted.pages] == [1, 2]
    assert "Residual learning" in extracted.pages[0].markdown
    assert "Retail charging evaluation" in extracted.pages[1].markdown
    assert extracted.sha256 == hashlib.sha256(pdf_path.read_bytes()).hexdigest()


def test_extractor_rejects_missing_pdf(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="PDF not found"):
        PDFExtractor().extract(_record(), tmp_path / "missing.pdf")
