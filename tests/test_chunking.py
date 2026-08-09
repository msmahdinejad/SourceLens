from paperlens.corpus.chunking import (
    SectionAwareChunker,
    SectionParser,
    canonical_section,
    estimate_tokens,
)
from paperlens.domain import ExtractedPaper, PageContent, PaperRecord


def make_paper(pages: list[str]) -> ExtractedPaper:
    record = PaperRecord(
        paper_id="sample",
        file_name="sample.pdf",
        title="Sample Paper",
        authors=["A. Author"],
        year=2026,
        venue="TestConf",
        source_url="https://example.com/sample.pdf",
    )
    return ExtractedPaper(
        record=record,
        pages=[
            PageContent(page_number=index, markdown=text)
            for index, text in enumerate(pages, start=1)
        ],
        sha256="a" * 64,
    )


def test_section_parser_tracks_headings_across_pages() -> None:
    paper = make_paper(
        [
            "# Abstract\n\nShort summary.\n\n# 1 Introduction\n\nOpening paragraph.",
            "Continuation on the next page.\n\n# 2 Related Work\n\nPrior studies.",
        ]
    )

    fragments = SectionParser().parse(paper)

    assert [fragment.section_type for fragment in fragments] == [
        "abstract",
        "introduction",
        "introduction",
        "related_work",
    ]
    assert fragments[2].page_number == 2


def test_chunker_never_crosses_section_boundary_and_keeps_page_range() -> None:
    intro_a = " ".join(f"alpha{i}" for i in range(65))
    intro_b = " ".join(f"beta{i}" for i in range(65))
    method = " ".join(f"method{i}" for i in range(45))
    paper = make_paper(
        [
            f"# 1 Introduction\n\n{intro_a}",
            f"{intro_b}\n\n# 2 Methodology\n\n{method}",
        ]
    )

    chunks = SectionAwareChunker(max_tokens=100, overlap_tokens=20).chunk(paper)

    intro_chunks = [chunk for chunk in chunks if chunk.section_type == "introduction"]
    method_chunks = [chunk for chunk in chunks if chunk.section_type == "methodology"]
    assert len(intro_chunks) == 2
    assert len(method_chunks) == 1
    assert intro_chunks[0].page_start == 1
    assert intro_chunks[-1].page_end == 2
    assert all("method0" not in chunk.text for chunk in intro_chunks)
    assert all(chunk.token_count <= 100 for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_section_aliases_and_token_estimate() -> None:
    assert canonical_section("6.1 Coastal Protection Experiments") == "experiments"
    assert canonical_section("Conclusions and Future Work") == "conclusion"
    assert canonical_section("۲ روش‌شناسی پژوهش") == "methodology"
    assert canonical_section("نتیجه‌گیری و کارهای آینده") == "conclusion"
    assert estimate_tokens("top-1 accuracy = 84.4%.") >= 6


def test_long_punctuation_dense_text_respects_token_limit() -> None:
    dense_table = "|".join(f"value_{index}=84.4%" for index in range(200))
    paper = make_paper([f"# Results\n\n{dense_table}"])

    chunks = SectionAwareChunker(max_tokens=100, overlap_tokens=20).chunk(paper)

    assert len(chunks) > 1
    assert all(chunk.token_count <= 100 for chunk in chunks)
