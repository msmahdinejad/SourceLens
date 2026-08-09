from pathlib import Path

from paperlens.domain import DocumentChunk, Evidence
from paperlens.retrieval import SQLiteFTSIndex, reciprocal_rank_fusion


def _evidence(chunk_id: str, similarity: float) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        paper_id="doc",
        document_id="doc",
        paper_title="Document",
        section="Body",
        section_type="body",
        page_start=1,
        page_end=1,
        text="evidence",
        similarity=similarity,
    )


def test_rank_fusion_does_not_turn_a_lexical_hit_into_semantic_certainty() -> None:
    shared_vector = _evidence("shared", 0.31)
    shared_lexical = _evidence("shared", 0.999)
    lexical_only = _evidence("lexical-only", 0.999)

    fused = reciprocal_rank_fusion(
        [shared_vector],
        [shared_lexical, lexical_only],
    )
    by_id = {item.chunk_id: item for item in fused}

    assert by_id["shared"].similarity == 0.31
    assert by_id["lexical-only"].similarity < 0.55


def test_fts_ignores_query_boilerplate_in_english_and_persian(tmp_path: Path) -> None:
    index = SQLiteFTSIndex(tmp_path / "retrieval.sqlite3")
    chunk = DocumentChunk(
        chunk_id="target",
        paper_id="doc",
        document_id="doc",
        paper_title="Document",
        section="Body",
        section_type="body",
        page_start=1,
        page_end=1,
        chunk_index=0,
        token_count=4,
        text="The exact needle is here",
        source_sha256="fixture",
    )
    index.replace_collection("workspace", [chunk])

    assert index.search("workspace", "what is the", limit=6) == []
    assert index.search("workspace", "چیست و در چه", limit=6) == []
    assert index.search("workspace", "what is the exact needle", limit=6)[0].chunk_id == "target"
