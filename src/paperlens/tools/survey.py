from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from paperlens.domain import Evidence
from paperlens.language_models.base import LanguageModel
from paperlens.tools.grounding import has_grounded_report_citations
from paperlens.tools.report_cache import (
    load_cached_report,
    report_cache_key,
    store_cached_report,
)


class SearchableCorpus(Protocol):
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


class SurveyArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    language: Literal["fa", "en"]
    text: str
    path: Path
    evidence: list[Evidence]
    generated_by_llm: bool = True
    cache_hit: bool = False


class SurveyGenerator:
    """Create a cited mini-survey from balanced evidence across selected documents."""

    def __init__(
        self,
        corpus: SearchableCorpus,
        language_model: LanguageModel,
        outputs_path: Path,
        document_ids: Sequence[str],
    ) -> None:
        self.corpus = corpus
        self.language_model = language_model
        self.outputs_path = outputs_path
        self.document_ids = tuple(dict.fromkeys(document_ids))
        if not self.document_ids:
            raise ValueError("At least one document is required")

    def generate(self, language: Literal["fa", "en"] = "fa") -> SurveyArtifact:
        evidence = self._collect_balanced_evidence()
        if {item.paper_id for item in evidence} != set(self.document_ids):
            raise RuntimeError("Mini-survey evidence does not cover every selected document")
        context = self._context(evidence)
        instructions = self._instructions(language)
        target = self.outputs_path / "surveys" / f"mini_survey_{language}.md"
        cache_key = report_cache_key(
            report_type="survey",
            language=language,
            instructions=instructions,
            evidence=evidence,
        )

        def validate_text(text: str) -> bool:
            return has_grounded_report_citations(
                text,
                evidence,
                required_papers=self.document_ids,
                language=language,
            )

        cached = load_cached_report(
            target,
            cache_key=cache_key,
            validator=validate_text,
        )
        if cached is not None:
            return SurveyArtifact(
                language=language,
                text=cached,
                path=target,
                evidence=evidence,
                cache_hit=True,
            )
        request = {
            "instructions": instructions,
            "user_input": (
                "Write the requested mini-survey using only the evidence below. "
                "Every factual claim must cite one or more evidence labels.\n\n"
                f"{context}"
            ),
        }

        def result_validator(candidate) -> bool:
            return validate_text(candidate.text)

        if hasattr(self.language_model, "run_validated"):
            result = self.language_model.run_validated(
                result_validator,
                validation_profile=f"survey:{language}",
                **request,
            )
        else:
            result = self.language_model.run(**request)
        if not result_validator(result):
            raise RuntimeError(
                "The mini-survey citations are missing, out of range, or omit a paper"
            )
        store_cached_report(target, text=result.text, cache_key=cache_key)
        return SurveyArtifact(
            language=language,
            text=result.text,
            path=target,
            evidence=evidence,
        )

    def _collect_balanced_evidence(self) -> list[Evidence]:
        collected: list[Evidence] = []
        seen: set[str] = set()
        queries = (
            (
                "main problem proposed method architecture contribution",
                {"abstract", "introduction", "methodology"},
            ),
            (
                "benchmark experiments results conclusion limitations future work",
                {"experiments", "conclusion", "limitations"},
            ),
        )
        for query, sections in queries:
            for item in self.corpus.search_balanced(
                query,
                self.document_ids,
                top_k=8,
                per_paper=2,
                section_types=sections,
            ):
                if item.chunk_id not in seen:
                    seen.add(item.chunk_id)
                    collected.append(item)
        return collected

    @staticmethod
    def _context(evidence: list[Evidence]) -> str:
        blocks = []
        for index, item in enumerate(evidence, start=1):
            blocks.append(f"[E{index}] {item.citation_label}\n{item.text}")
        return "\n\n".join(blocks)

    @staticmethod
    def _instructions(language: Literal["fa", "en"]) -> str:
        language_instruction = (
            "Write in polished Persian." if language == "fa" else "Write in academic English."
        )
        return f"""
You are a rigorous research assistant. {language_instruction}
Create a mini-survey with these sections:
1. problem introduction,
2. one subsection for each selected document,
3. method comparison,
4. chronological progress trend,
5. final conclusion.

Use only supplied evidence. Cite claims inline as [E1], [E2], etc.
Do not invent experimental values, limitations, or claims.
Explicitly warn that as-reported metrics use non-identical evaluation protocols.
When writing Persian, use Persian throughout except for model names and standard
technical terms; never mix Arabic, Chinese, Thai, Spanish, or other languages.
""".strip()
