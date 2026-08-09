from pathlib import Path

import pytest

from paperlens.domain import Evidence
from paperlens.language_models.base import GenerationResult
from paperlens.tools.analysis_reports import AnalysisReportGenerator
from paperlens.tools.survey import SurveyGenerator

DOCUMENT_IDS = ("homecharge", "retailcharge", "smartcharge", "solarhub")


class FakeCorpus:
    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        paper_id = next(iter(paper_ids or {"homecharge"}))
        section_type = sorted(section_types or {"other"})[0]
        return [
            Evidence(
                chunk_id=f"{paper_id}-{section_type}-{query[:4]}",
                paper_id=paper_id,
                paper_title=paper_id,
                section=section_type,
                section_type=section_type,
                page_start=1,
                page_end=1,
                text=f"Evidence about {paper_id}.",
                similarity=0.9,
            )
        ]

    def search_balanced(
        self,
        query: str,
        paper_ids: tuple[str, ...],
        *,
        top_k: int = 6,
        per_paper: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]:
        return [
            self.search(
                query,
                paper_ids={paper_id},
                section_types=section_types,
            )[0]
            for paper_id in paper_ids
        ][:top_k]


class CitedModel:
    def run(self, **kwargs) -> GenerationResult:
        labels = []
        for line in kwargs["user_input"].splitlines():
            if line.startswith("[E"):
                label = line.split("]", 1)[0] + "]"
                if label not in labels:
                    labels.append(label)
        if "Persian" in kwargs["instructions"]:
            prose = "این گزارش پژوهشی برای هر چهار مقاله با شواهد معتبر نوشته شده است"
        else:
            prose = "This research report covers all four papers with valid evidence"
        return GenerationResult(text=f"# Generated report\n\n{prose} {' '.join(labels)}.")


def test_survey_and_analysis_reports_write_cited_markdown(tmp_path: Path) -> None:
    corpus = FakeCorpus()
    model = CitedModel()

    survey = SurveyGenerator(corpus, model, tmp_path, DOCUMENT_IDS).generate("fa")
    gap = AnalysisReportGenerator(corpus, model, tmp_path, DOCUMENT_IDS).generate(
        "research_gap",
        "fa",
    )
    limitations = AnalysisReportGenerator(corpus, model, tmp_path, DOCUMENT_IDS).generate(
        "limitations",
        "en",
    )

    assert survey.path.is_file()
    assert gap.path.is_file()
    assert limitations.path.is_file()
    assert all("[E1]" in artifact.text for artifact in (survey, gap, limitations))


def test_report_rejects_out_of_range_citations(tmp_path: Path) -> None:
    class InvalidCitationModel:
        def run(self, **_kwargs) -> GenerationResult:
            return GenerationResult(text="Unsupported citation [E999].")

    with pytest.raises(RuntimeError, match="out of range"):
        SurveyGenerator(
            FakeCorpus(), InvalidCitationModel(), tmp_path, DOCUMENT_IDS
        ).generate("en")


def test_survey_context_is_bounded_for_provider_token_limits(tmp_path: Path) -> None:
    class RecordingCorpus(FakeCorpus):
        def __init__(self) -> None:
            self.requests = []

        def search_balanced(self, query, paper_ids, **kwargs):
            self.requests.append(kwargs)
            return super().search_balanced(query, paper_ids, **kwargs)

    corpus = RecordingCorpus()

    artifact = SurveyGenerator(
        corpus, CitedModel(), tmp_path, DOCUMENT_IDS
    ).generate("en")

    assert len(corpus.requests) == 2
    assert all(request["top_k"] == 8 for request in corpus.requests)
    assert all(request["per_paper"] == 2 for request in corpus.requests)
    assert len(artifact.evidence) <= 16


def test_analysis_context_is_bounded_for_interactive_latency(tmp_path: Path) -> None:
    class RecordingCorpus(FakeCorpus):
        def __init__(self) -> None:
            self.requests = []

        def search_balanced(self, query, paper_ids, **kwargs):
            self.requests.append(kwargs)
            return super().search_balanced(query, paper_ids, **kwargs)

    corpus = RecordingCorpus()

    artifact = AnalysisReportGenerator(
        corpus, CitedModel(), tmp_path, DOCUMENT_IDS
    ).generate(
        "limitations",
        "fa",
    )

    assert corpus.requests == [
        {
            "top_k": 12,
            "per_paper": 3,
            "section_types": {"conclusion", "limitations", "experiments", "other"},
        }
    ]
    assert len(artifact.evidence) <= 12


def test_static_analysis_report_reuses_a_valid_content_addressed_cache(
    tmp_path: Path,
) -> None:
    class CountingModel(CitedModel):
        def __init__(self) -> None:
            self.calls = 0

        def run(self, **kwargs) -> GenerationResult:
            self.calls += 1
            return super().run(**kwargs)

    model = CountingModel()
    generator = AnalysisReportGenerator(FakeCorpus(), model, tmp_path, DOCUMENT_IDS)

    first = generator.generate("limitations", "fa")
    second = generator.generate("limitations", "fa")

    assert model.calls == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.text == first.text
