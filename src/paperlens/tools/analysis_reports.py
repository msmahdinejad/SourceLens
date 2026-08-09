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


ReportKind = Literal["research_gap", "limitations"]


class AnalysisReportArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ReportKind
    language: Literal["fa", "en"]
    text: str
    path: Path
    evidence: list[Evidence]
    generated_by_llm: bool = True
    cache_hit: bool = False


class AnalysisReportGenerator:
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

    def generate(
        self,
        kind: ReportKind,
        language: Literal["fa", "en"] = "fa",
    ) -> AnalysisReportArtifact:
        evidence = self._collect_evidence(kind)
        if {item.paper_id for item in evidence} != set(self.document_ids):
            raise RuntimeError("Analysis evidence does not cover every selected document")
        instructions = self._instructions(kind, language)
        target = self.outputs_path / "analysis" / f"{kind}_{language}.md"
        cache_key = report_cache_key(
            report_type=f"analysis:{kind}",
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
            return AnalysisReportArtifact(
                kind=kind,
                language=language,
                text=cached,
                path=target,
                evidence=evidence,
                cache_hit=True,
            )
        request = {
            "instructions": instructions,
            "user_input": self._context(evidence),
        }

        def result_validator(candidate) -> bool:
            return validate_text(candidate.text)

        if hasattr(self.language_model, "run_validated"):
            result = self.language_model.run_validated(
                result_validator,
                validation_profile=f"analysis:{kind}:{language}",
                **request,
            )
        else:
            result = self.language_model.run(**request)
        if not result_validator(result):
            raise RuntimeError("The analysis citations are missing, out of range, or omit a paper")

        store_cached_report(target, text=result.text, cache_key=cache_key)
        return AnalysisReportArtifact(
            kind=kind,
            language=language,
            text=result.text,
            path=target,
            evidence=evidence,
        )

    def _collect_evidence(self, kind: ReportKind) -> list[Evidence]:
        if kind == "research_gap":
            query = (
                "conclusion limitations unresolved problems future work remaining "
                "challenges computational cost"
            )
        else:
            query = (
                "limitations weaknesses failure cases computational cost memory "
                "training inference constraints"
            )
        sections = {"conclusion", "limitations", "experiments", "other"}
        collected = []
        seen = set()
        for item in self.corpus.search_balanced(
            query,
            self.document_ids,
            top_k=12,
            per_paper=3,
            section_types=sections,
        ):
            if item.chunk_id not in seen:
                seen.add(item.chunk_id)
                collected.append(item)
        return collected

    @staticmethod
    def _context(evidence: list[Evidence]) -> str:
        return "\n\n".join(
            f"[E{index}] {item.citation_label}\n{item.text}"
            for index, item in enumerate(evidence, start=1)
        )

    @staticmethod
    def _instructions(kind: ReportKind, language: Literal["fa", "en"]) -> str:
        language_text = "Persian" if language == "fa" else "English"
        if kind == "research_gap":
            task = """
For each paper, extract explicitly supported limitations, unresolved problems,
and future-work statements. Then identify cross-paper research gaps and propose
future directions. Clearly label a direction as your inference when it is not
explicitly stated by a paper.
"""
        else:
            task = """
Compare the weaknesses and limitations of the selected methods. Include a compact
comparison table covering optimization, accuracy/efficiency trade-offs,
training or inference cost, and protocol limitations. Do not turn missing
information into a claimed limitation.
"""
        return f"""
Write a rigorous research analysis in {language_text}.
{task.strip()}
Keep the report focused and presentation-ready, with at most 650 words.
Use only supplied evidence and cite every factual claim with [E#].
Preserve uncertainty and distinguish explicit author statements from inference.
When writing Persian, use Persian throughout except for model names and standard
technical terms; never mix Arabic, Chinese, Thai, Spanish, or other languages.
""".strip()
