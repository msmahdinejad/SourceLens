from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from chromadb.api.client import SharedSystemClient

from paperlens.corpus.index import ChromaEmbeddingIndex
from paperlens.domain import PaperChunk


class DeterministicEmbedder:
    model_name = "deterministic-test-embedder"

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        lowered = text.casefold()
        if "residual" in lowered or "homecharge" in lowered:
            return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
        if "compound" in lowered or "efficient" in lowered:
            return np.asarray([0.0, 1.0, 0.0], dtype=np.float32)
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float32)

    def embed_passages(
        self,
        texts: Sequence[str],
        titles: Sequence[str] | None = None,
    ) -> np.ndarray:
        return np.stack([self._vector(text) for text in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


def _chunk(
    chunk_id: str,
    paper_id: str,
    text: str,
    *,
    section_type: str = "methodology",
) -> PaperChunk:
    return PaperChunk(
        chunk_id=chunk_id,
        paper_id=paper_id,
        paper_title=paper_id.title(),
        section=section_type.title(),
        section_type=section_type,
        page_start=1,
        page_end=1,
        chunk_index=0,
        token_count=len(text.split()),
        text=text,
        source_sha256="a" * 64,
    )


def _index(path: Path) -> ChromaEmbeddingIndex:
    return ChromaEmbeddingIndex(
        path,
        DeterministicEmbedder(),
        collection_name="integration_chunks",
    )


def test_chroma_round_trip_preserves_metadata_and_similarity(tmp_path: Path) -> None:
    index = _index(tmp_path / "chroma")
    index.upsert(
        [
            _chunk("homecharge-1", "homecharge", "Residual learning uses shortcut connections."),
            _chunk("solarhub-1", "solarhub", "Compound scaling balances dimensions."),
        ]
    )

    evidence = index.search("How does residual learning work?", top_k=1)

    assert index.count() == 2
    assert len(evidence) == 1
    assert evidence[0].chunk_id == "homecharge-1"
    assert evidence[0].paper_id == "homecharge"
    assert evidence[0].similarity == 1.0


def test_chroma_filters_by_paper_and_section(tmp_path: Path) -> None:
    index = _index(tmp_path / "chroma")
    index.upsert(
        [
            _chunk("homecharge-method", "homecharge", "Residual architecture."),
            _chunk(
                "homecharge-result",
                "homecharge",
                "Residual classification result.",
                section_type="experiments",
            ),
            _chunk("solarhub-method", "solarhub", "Compound scaling."),
        ]
    )

    evidence = index.search(
        "residual",
        top_k=5,
        paper_ids={"homecharge"},
        section_types={"experiments"},
    )

    assert [item.chunk_id for item in evidence] == ["homecharge-result"]


def test_balanced_retrieval_returns_requested_paper_order(tmp_path: Path) -> None:
    index = _index(tmp_path / "chroma")
    index.upsert(
        [
            _chunk("homecharge-1", "homecharge", "Residual block."),
            _chunk("retailcharge-1", "retailcharge", "Grouped residual block."),
            _chunk("smartcharge-1", "smartcharge", "Channel recalibration."),
            _chunk("solarhub-1", "solarhub", "Compound scaling."),
        ]
    )

    evidence = index.search_balanced(
        "compare architectures",
        ("homecharge", "retailcharge", "smartcharge", "solarhub"),
        top_k=4,
        per_paper=1,
    )

    assert [item.paper_id for item in evidence] == [
        "homecharge",
        "retailcharge",
        "smartcharge",
        "solarhub",
    ]


def test_reingesting_one_paper_replaces_only_that_papers_chunks(tmp_path: Path) -> None:
    index = _index(tmp_path / "chroma")
    index.upsert(
        [
            _chunk("homecharge-old-1", "homecharge", "Residual old one."),
            _chunk("homecharge-old-2", "homecharge", "Residual old two."),
            _chunk("solarhub-1", "solarhub", "Compound scaling."),
        ]
    )

    index.upsert([_chunk("homecharge-new", "homecharge", "Residual replacement.")])

    assert index.count() == 2
    homecharge = index.search("residual", top_k=5, paper_ids={"homecharge"})
    solarhub = index.search("compound", top_k=5, paper_ids={"solarhub"})
    assert [item.chunk_id for item in homecharge] == ["homecharge-new"]
    assert [item.chunk_id for item in solarhub] == ["solarhub-1"]


def test_collection_rejects_embedding_dimension_mismatch(tmp_path: Path) -> None:
    class SizedEmbedder(DeterministicEmbedder):
        def __init__(self, dimensions: int) -> None:
            self.dimensions = dimensions

    path = tmp_path / "chroma"
    ChromaEmbeddingIndex(path, SizedEmbedder(3), collection_name="sized")

    with pytest.raises(ValueError, match="dimensions"):
        ChromaEmbeddingIndex(path, SizedEmbedder(4), collection_name="sized")


def test_legacy_collection_configuration_is_migrated_without_reembedding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chroma"
    index = _index(path)
    index.upsert([_chunk("homecharge-1", "homecharge", "Residual architecture.")])
    del index
    SharedSystemClient.clear_system_cache()
    with sqlite3.connect(path / "chroma.sqlite3") as connection:
        connection.execute(
            "UPDATE collections SET config_json_str = '{}' WHERE name = ?",
            ("integration_chunks",),
        )
        connection.commit()

    migrated = _index(path)

    assert migrated.count() == 1
    with sqlite3.connect(path / "chroma.sqlite3") as connection:
        raw = connection.execute(
            "SELECT config_json_str FROM collections WHERE name = ?",
            ("integration_chunks",),
        ).fetchone()[0]
    configuration = json.loads(raw)
    assert configuration["_type"] == "CollectionConfigurationInternal"
    assert configuration["hnsw_configuration"]["space"] == "cosine"
    assert (path / "chroma.sqlite3.pre-0.6-config.bak").is_file()
