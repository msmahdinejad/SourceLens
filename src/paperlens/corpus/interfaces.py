from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from paperlens.domain import DocumentChunk, Evidence


class EmbeddingIndex(Protocol):
    def upsert(self, chunks: Sequence[DocumentChunk]) -> None: ...

    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]: ...

    def search_balanced(
        self,
        query: str,
        paper_ids: Sequence[str],
        *,
        top_k: int = 6,
        per_paper: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]: ...

    def count(self) -> int: ...
