from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from paperlens.domain import DocumentChunk, Evidence

_TERM = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "آن",
    "آیا",
    "از",
    "است",
    "این",
    "با",
    "بر",
    "برای",
    "به",
    "بود",
    "تا",
    "چه",
    "چرا",
    "چگونه",
    "چیست",
    "در",
    "را",
    "شده",
    "شود",
    "که",
    "کجا",
    "کی",
    "می",
    "و",
    "یا",
}


class SQLiteFTSIndex:
    """Derived FTS5 chunk index; source files and Chroma remain authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                    collection_id UNINDEXED,
                    chunk_id UNINDEXED,
                    workspace_id UNINDEXED,
                    document_id UNINDEXED,
                    paper_id UNINDEXED,
                    paper_title,
                    filename UNINDEXED,
                    section,
                    section_type UNINDEXED,
                    page_start UNINDEXED,
                    page_end UNINDEXED,
                    chunk_type UNINDEXED,
                    extraction_method UNINDEXED,
                    text,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS fts_collections(
                    collection_id TEXT PRIMARY KEY,
                    chunk_count INTEGER NOT NULL
                );
                """
            )

    def replace_collection(
        self,
        collection_id: str,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM chunk_fts WHERE collection_id = ?", (collection_id,))
            self._insert(connection, collection_id, chunks)
            self._mark(connection, collection_id)

    def replace_document(
        self,
        collection_id: str,
        document_id: str,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM chunk_fts WHERE collection_id = ? AND document_id = ?",
                (collection_id, document_id),
            )
            self._insert(connection, collection_id, chunks)
            self._mark(connection, collection_id)

    def search(
        self,
        collection_id: str,
        query: str,
        *,
        limit: int,
        document_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        match = self._match_query(query)
        if not match:
            return []
        clauses = ["chunk_fts MATCH ?", "collection_id = ?"]
        parameters: list[str | int] = [match, collection_id]
        if document_ids:
            clauses.append(f"document_id IN ({','.join('?' for _ in document_ids)})")
            parameters.extend(sorted(document_ids))
        if section_types:
            clauses.append(f"section_type IN ({','.join('?' for _ in section_types)})")
            parameters.extend(sorted(section_types))
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT chunk_id, workspace_id, document_id, paper_id, paper_title,
                       filename, section, section_type, page_start, page_end,
                       chunk_type, extraction_method, text, bm25(chunk_fts) AS score
                FROM chunk_fts
                WHERE {' AND '.join(clauses)}
                ORDER BY score, chunk_id
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            Evidence(
                chunk_id=str(row["chunk_id"]),
                workspace_id=str(row["workspace_id"]),
                document_id=str(row["document_id"]),
                paper_id=str(row["paper_id"]),
                paper_title=str(row["paper_title"]),
                filename=str(row["filename"]),
                section=str(row["section"]),
                section_type=str(row["section_type"]),
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
                chunk_type=str(row["chunk_type"]),
                extraction_method=str(row["extraction_method"]),
                text=str(row["text"]),
                similarity=1.0 / (1.0 + abs(float(row["score"]))),
            )
            for row in rows
        ]

    def is_current(self, collection_id: str, chunk_count: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT chunk_count FROM fts_collections WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return row is not None and int(row[0]) == chunk_count

    def count(self, collection_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT count(*) FROM chunk_fts WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        return int(row[0])

    def delete_document(self, collection_id: str, document_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM chunk_fts WHERE collection_id = ? AND document_id = ?",
                (collection_id, document_id),
            )
            self._mark(connection, collection_id)

    def delete_collection(self, collection_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM chunk_fts WHERE collection_id = ?", (collection_id,))
            connection.execute(
                "DELETE FROM fts_collections WHERE collection_id = ?", (collection_id,)
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        collection_id: str,
        chunks: Sequence[DocumentChunk],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO chunk_fts(
                collection_id, chunk_id, workspace_id, document_id, paper_id,
                paper_title, filename, section, section_type, page_start, page_end,
                chunk_type, extraction_method, text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    collection_id,
                    chunk.chunk_id,
                    chunk.workspace_id,
                    chunk.document_id or chunk.paper_id,
                    chunk.paper_id,
                    chunk.paper_title,
                    chunk.filename,
                    chunk.section,
                    chunk.section_type,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.chunk_type,
                    chunk.extraction_method,
                    chunk.text,
                )
                for chunk in chunks
            ],
        )

    @staticmethod
    def _mark(connection: sqlite3.Connection, collection_id: str) -> None:
        count = int(
            connection.execute(
                "SELECT count(*) FROM chunk_fts WHERE collection_id = ?", (collection_id,)
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO fts_collections(collection_id, chunk_count) VALUES (?, ?)
            ON CONFLICT(collection_id) DO UPDATE SET chunk_count = excluded.chunk_count
            """,
            (collection_id, count),
        )

    @staticmethod
    def _match_query(query: str) -> str:
        terms = list(
            dict.fromkeys(
                normalized
                for term in _TERM.findall(query)
                if (normalized := term.casefold().translate(str.maketrans("يىك", "ییک")))
                not in _STOP_WORDS
            )
        )
        return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def reciprocal_rank_fusion(
    vector: list[Evidence],
    lexical: list[Evidence],
) -> list[Evidence]:
    vector_by_id = {item.chunk_id: item for item in vector}
    by_id = {item.chunk_id: item for item in lexical}
    by_id.update(vector_by_id)
    scores: Counter[str] = Counter()
    for results, weight in ((vector, 1.0), (lexical, 2.0)):
        for rank, item in enumerate(results, start=1):
            scores[item.chunk_id] += weight / (60 + rank)
    return [
        by_id[chunk_id].model_copy(
            update={
                # RRF changes rank, not semantic confidence. BM25 scores are not
                # calibrated probabilities and must not bypass the abstention gate.
                "similarity": (
                    vector_by_id[chunk_id].similarity
                    if chunk_id in vector_by_id
                    else min(0.54, by_id[chunk_id].similarity)
                ),
            }
        )
        for chunk_id, score in scores.most_common()
    ]
