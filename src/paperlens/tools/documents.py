from __future__ import annotations

import json
from typing import Any, Protocol

from paperlens.domain import Evidence


class DocumentContext(Protocol):
    store: Any

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        top_k: int = 6,
        document_ids: set[str] | None = None,
        section_types: set[str] | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        chunk_types: set[str] | None = None,
    ) -> list[Evidence]: ...

    def search_balanced(
        self,
        workspace_id: str,
        query: str,
        document_ids: list[str],
        *,
        top_k: int = 6,
        per_document: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]: ...

    def inspect(self, workspace_id: str) -> dict[str, Any]: ...

    def extraction(self, workspace_id: str, document_id: str): ...

    def rebuild(self, workspace_id: str) -> dict[str, Any]: ...


class DocumentTools:
    """Deterministic workspace operations whose results are synthesized by the LLM."""

    def __init__(
        self,
        context: DocumentContext,
        workspace_id: str,
        *,
        allowed_document_ids: set[str] | None = None,
    ) -> None:
        self.context = context
        self.workspace_id = workspace_id
        self.allowed_document_ids = (
            frozenset(allowed_document_ids) if allowed_document_ids is not None else None
        )

    def search_documents(
        self,
        query: str,
        filters: dict[str, Any] | None,
    ) -> str:
        filters = filters or {}
        document_ids = self._scoped_ids(self._set(filters.get("document_ids")))
        if document_ids == set():
            return self._evidence_json([], query=query)
        top_k = self._retrieval_budget(query, filters.get("top_k"))
        evidence = self.context.search(
            self.workspace_id,
            query,
            top_k=top_k,
            document_ids=document_ids,
            section_types=self._set(filters.get("section_types")),
            page_start=filters.get("page_start"),
            page_end=filters.get("page_end"),
            chunk_types=self._set(filters.get("chunk_types")),
        )
        return self._evidence_json(evidence, query=query, retrieval_limit=top_k)

    def summarize_document(self, document_id: str) -> str:
        self._require_allowed(document_id)
        queries = [
            "document title central topic thesis purpose abstract introduction",
            "main concepts definitions background and scope",
            "methods approach methodology algorithm architecture",
            "experiments results findings evidence examples",
            "conclusions implications limitations future work",
        ]
        evidence: list[Evidence] = []
        seen: set[str] = set()
        for query in queries:
            for item in self.context.search(
                self.workspace_id,
                query,
                top_k=5,
                document_ids={document_id},
            ):
                if item.chunk_id in seen:
                    continue
                seen.add(item.chunk_id)
                evidence.append(item)
        return self._evidence_json(
            evidence[:18],
            document_id=document_id,
            retrieval_queries=queries,
        )

    def compare_documents(
        self,
        document_ids: list[str],
        aspects: list[str] | None,
    ) -> str:
        selected_ids = [
            document_id for document_id in document_ids if self._is_allowed(document_id)
        ]
        if not selected_ids:
            return self._evidence_json(
                [],
                document_ids=[],
                aspects=aspects or [],
            )
        query = "compare purpose methods findings evidence limitations"
        if aspects:
            query += " " + " ".join(aspects)
        evidence = self.context.search_balanced(
            self.workspace_id,
            query,
            selected_ids,
            top_k=max(8, len(selected_ids) * 3),
            per_document=3,
        )
        return self._evidence_json(
            evidence,
            document_ids=selected_ids,
            aspects=aspects or [],
        )

    def extract_visuals(self, kind: str, document_id: str | None) -> str:
        workspace = self.context.store.get(self.workspace_id)
        selected = [
            item
            for item in workspace.documents
            if self._is_allowed(item.document_id)
            and (document_id is None or item.document_id == document_id)
        ]
        if document_id is not None:
            self._require_allowed(document_id)
        values = []
        evidence = []
        for document in selected:
            try:
                extraction = self.context.extraction(self.workspace_id, document.document_id)
            except FileNotFoundError:
                continue
            for page in extraction.pages:
                for index, value in enumerate(getattr(page, kind), start=1):
                    item = {
                        "document_id": document.document_id,
                        "filename": document.original_filename,
                        "page_number": page.page_number,
                        **value,
                    }
                    values.append(item)
                    evidence.append(
                        Evidence(
                            chunk_id=(f"{kind}-{document.document_id}-{page.page_number}-{index}"),
                            paper_id=document.document_id,
                            paper_title=document.title or document.original_filename,
                            section=kind.title(),
                            section_type="other",
                            page_start=page.page_number,
                            page_end=page.page_number,
                            text=json.dumps(value, ensure_ascii=False),
                            similarity=1.0,
                            workspace_id=self.workspace_id,
                            document_id=document.document_id,
                            filename=document.original_filename,
                            chunk_type="table" if kind == "tables" else "figure",
                            extraction_method=page.extraction_method,
                        )
                    )
        return json.dumps(
            {
                kind: values,
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            ensure_ascii=False,
        )

    def inspect_workspace(self) -> str:
        workspace = self.context.store.get(self.workspace_id)
        return json.dumps(
            {
                **self.context.inspect(self.workspace_id),
                "name": workspace.name,
                "selected_document_ids": (
                    sorted(self.allowed_document_ids)
                    if self.allowed_document_ids is not None
                    else None
                ),
                "documents": [
                    document.model_dump(mode="json")
                    for document in workspace.documents
                    if self._is_allowed(document.document_id)
                ],
            },
            ensure_ascii=False,
        )

    def rebuild(self, workspace_id: str) -> str:
        if workspace_id != self.workspace_id:
            raise ValueError("Tool cannot rebuild a different active workspace")
        return json.dumps(self.context.rebuild(workspace_id), ensure_ascii=False)

    def research_evidence(self, kind: str, language: str) -> str:
        query = (
            "research gaps unresolved problems limitations future work conclusions"
            if kind == "research_gap"
            else "limitations weaknesses failure cases constraints tradeoffs"
        )
        evidence = self.context.search(
            self.workspace_id,
            query,
            top_k=16,
            document_ids=self._scoped_ids(None),
        )
        return self._evidence_json(evidence, kind=kind, language=language)

    def survey_evidence(self, language: str) -> str:
        workspace = self.context.store.get(self.workspace_id)
        ids = [
            item.document_id for item in workspace.documents if self._is_allowed(item.document_id)
        ]
        evidence = self.context.search_balanced(
            self.workspace_id,
            "purpose method contributions results limitations conclusion",
            ids,
            top_k=max(12, len(ids) * 3),
            per_document=3,
        )
        return self._evidence_json(evidence, kind="survey", language=language)

    @staticmethod
    def _set(value: Any) -> set[str] | None:
        if value is None:
            return None
        return {str(item) for item in value}

    @staticmethod
    def _retrieval_budget(query: str, requested: Any) -> int:
        if requested is not None:
            return min(30, max(1, int(requested)))
        term_count = len(query.split())
        return min(24, max(4, 4 + max(0, term_count - 3 + 1) // 2))

    def _is_allowed(self, document_id: str) -> bool:
        return self.allowed_document_ids is None or document_id in self.allowed_document_ids

    def _require_allowed(self, document_id: str) -> None:
        if not self._is_allowed(document_id):
            raise ValueError(f"Document {document_id!r} is outside the selected document scope")

    def _scoped_ids(self, requested: set[str] | None) -> set[str] | None:
        if self.allowed_document_ids is None:
            return requested
        if requested is None:
            return set(self.allowed_document_ids)
        return requested & set(self.allowed_document_ids)

    @staticmethod
    def _evidence_json(evidence: list[Evidence], **metadata: Any) -> str:
        return json.dumps(
            {
                **metadata,
                "evidence": [item.model_dump(mode="json") for item in evidence],
            },
            ensure_ascii=False,
        )
