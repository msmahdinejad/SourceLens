from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import matplotlib
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from paperlens.assistant import ANSWERABLE_SECTION_TYPES, mentioned_document_ids
from paperlens.domain import Evidence

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BenchmarkCategory = Literal[
    "abstention",
    "citation",
    "contradiction",
    "entity_alias",
    "equation",
    "exact_identifier",
    "multi_hop",
    "temporal",
]


class BenchmarkPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    page: int = Field(ge=1)
    text: str = Field(min_length=1)


class BenchmarkDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    title: str
    language: Literal["en", "fa"]
    pages: list[BenchmarkPage]


class ExpectedCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    pages: list[int]


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    query: str
    language: Literal["en", "fa"]
    category: BenchmarkCategory
    scope: Literal["in_scope", "out_of_scope"]
    relevant_document_ids: list[str]
    expected_citations: list[ExpectedCitation]
    split: Literal["calibration", "test"]


class BenchmarkThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    recall_at_6: float = Field(ge=0.0, le=1.0)
    citation_precision: float = Field(ge=0.0, le=1.0)
    abstention_accuracy: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: float = Field(gt=0.0)


class RetrievalBenchmark(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_id: str
    version: int
    license: str
    authored_for_sourcelens: bool
    source_kind: Literal["synthetic", "wikidata"] = "synthetic"
    source_count: int = Field(default=0, ge=0)
    documents: list[BenchmarkDocument]
    cases: list[BenchmarkCase]
    thresholds: BenchmarkThresholds


def load_benchmark(path: Path) -> RetrievalBenchmark:
    documents_payload = json.loads((path / "documents.json").read_text(encoding="utf-8"))
    questions_payload = json.loads((path / "questions.json").read_text(encoding="utf-8"))
    benchmark = RetrievalBenchmark.model_validate(
        {
            "benchmark_id": questions_payload["benchmark_id"],
            "version": questions_payload["version"],
            "license": documents_payload["license"],
            "authored_for_sourcelens": documents_payload["authored_for_sourcelens"],
            "source_kind": documents_payload.get("source_kind", "synthetic"),
            "source_count": documents_payload.get(
                "source_count", len(documents_payload["documents"])
            ),
            "documents": documents_payload["documents"],
            "cases": questions_payload["cases"],
            "thresholds": questions_payload["thresholds"],
        }
    )
    if benchmark.version != documents_payload["version"]:
        raise ValueError("Benchmark document and question versions must match")
    if benchmark.source_count != len(benchmark.documents):
        raise ValueError("Benchmark source count must match its documents")
    if benchmark.source_kind == "wikidata":
        provenance_path = path / "provenance.json"
        if not provenance_path.is_file():
            raise ValueError("Wikidata benchmark requires pinned provenance")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        source_ids = {source["entity_id"] for source in provenance.get("sources", [])}
        if provenance.get("license") != "CC0-1.0" or source_ids != {
            document.document_id for document in benchmark.documents
        }:
            raise ValueError("Wikidata benchmark provenance does not match its documents")
        if any(
            len(str(source.get("sha256", ""))) != 64
            or f"revision={source.get('revision_id')}" not in str(source.get("url", ""))
            for source in provenance["sources"]
        ):
            raise ValueError("Wikidata benchmark provenance must pin revisions and checksums")
        if benchmark.authored_for_sourcelens:
            raise ValueError("Wikidata-derived documents cannot be marked as authored fixtures")
    if not 30 <= len(benchmark.cases) <= 50:
        raise ValueError("Benchmark must contain 30 to 50 cases")
    documents = {document.document_id: document for document in benchmark.documents}
    if len(documents) != len(benchmark.documents):
        raise ValueError("Benchmark document ids must be unique")
    if len({case.case_id for case in benchmark.cases}) != len(benchmark.cases):
        raise ValueError("Benchmark case ids must be unique")
    pages = {
        document.document_id: {page.page for page in document.pages}
        for document in benchmark.documents
    }
    for case in benchmark.cases:
        if set(case.relevant_document_ids) - documents.keys():
            raise ValueError(f"Unknown relevant document in benchmark case {case.case_id}")
        if case.scope == "out_of_scope" and (
            case.relevant_document_ids or case.expected_citations
        ):
            raise ValueError(f"Out-of-scope case {case.case_id} cannot cite documents")
        for citation in case.expected_citations:
            if citation.document_id not in documents or not set(citation.pages) <= pages[
                citation.document_id
            ]:
                raise ValueError(f"Invalid citation truth in benchmark case {case.case_id}")
    return benchmark


def run_general_benchmark(
    path: Path,
    outputs_path: Path,
    *,
    repetitions: int = 3,
    scale_chunk_count: int = 10_000,
):
    """Run the generalized benchmark without loading its heavier dependencies at import."""
    from paperlens.retrieval_benchmark import run_general_benchmark as run

    return run(
        path,
        outputs_path,
        repetitions=repetitions,
        scale_chunk_count=scale_chunk_count,
    )


class EvaluationCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    query: str
    scope: Literal["in_scope", "out_of_scope"]
    relevant_paper_ids: list[str]
    split: Literal["calibration", "test"] | None = None


class RetrievalCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    query: str
    scope: Literal["in_scope", "out_of_scope"]
    relevant_paper_ids: list[str]
    split: Literal["calibration", "test"]
    mentioned_paper_ids: list[str]
    retrieved_paper_ids: list[str]
    top_similarity: float
    first_relevant_rank: int | None
    relevant_paper_coverage: float


class GateMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    threshold: float
    sensitivity: float
    specificity: float
    precision: float
    f1: float
    balanced_accuracy: float


class RetrievalEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    cases: list[RetrievalCaseResult]
    hit_at_1: float
    hit_at_k: float
    mean_reciprocal_rank: float
    cross_paper_coverage_at_k: float
    recommended_threshold: float
    gate_metrics: GateMetrics
    json_path: Path
    csv_path: Path
    markdown_path: Path
    chart_path: Path
    evaluated_at: str
    benchmark_sha256: str
    calibration_case_count: int
    test_case_count: int
    embedding_model: str
    methodology_note: str


class SearchableCorpus(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]: ...


def evaluate_retrieval(
    corpus: SearchableCorpus,
    cases_path: Path,
    outputs_path: Path,
    *,
    top_k: int = 6,
) -> RetrievalEvaluation:
    cases = TypeAdapter(list[EvaluationCase]).validate_python(
        json.loads(cases_path.read_text(encoding="utf-8"))
    )
    document_ids = tuple(
        dict.fromkeys(
            document_id for case in cases for document_id in case.relevant_paper_ids
        )
    )
    assigned_splits = _assign_splits(cases)
    results = [
        _evaluate_case(corpus, case, top_k, split, document_ids)
        for case, split in zip(cases, assigned_splits, strict=True)
    ]
    in_scope = [
        result for result in results if result.scope == "in_scope" and result.split == "test"
    ]
    test_out_of_scope = [
        result for result in results if result.scope == "out_of_scope" and result.split == "test"
    ]
    calibration = [result for result in results if result.split == "calibration"]
    calibration_in_scope = [result for result in calibration if result.scope == "in_scope"]
    calibration_out_of_scope = [result for result in calibration if result.scope == "out_of_scope"]
    test = [result for result in results if result.split == "test"]
    if (
        not in_scope
        or not test_out_of_scope
        or not calibration_in_scope
        or not calibration_out_of_scope
    ):
        raise ValueError("Evaluation requires in-scope and out-of-scope cases in both splits")

    single_target = [result for result in in_scope if len(result.relevant_paper_ids) == 1]
    cross_paper = [result for result in in_scope if len(result.relevant_paper_ids) > 1]
    hit_at_1 = _mean(1.0 if result.first_relevant_rank == 1 else 0.0 for result in single_target)
    hit_at_k = _mean(
        1.0 if result.first_relevant_rank is not None else 0.0 for result in single_target
    )
    mrr = _mean(
        1.0 / result.first_relevant_rank if result.first_relevant_rank else 0.0
        for result in single_target
    )
    cross_coverage = _mean(result.relevant_paper_coverage for result in cross_paper)
    selected = _select_threshold(calibration)
    gate = _gate_metrics(test, selected.threshold)

    target = outputs_path / "evaluation"
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "retrieval_evaluation.json"
    csv_path = target / "retrieval_cases.csv"
    markdown_path = target / "retrieval_evaluation.md"
    chart_path = target / "similarity_gate.png"

    evaluation = RetrievalEvaluation(
        cases=results,
        hit_at_1=hit_at_1,
        hit_at_k=hit_at_k,
        mean_reciprocal_rank=mrr,
        cross_paper_coverage_at_k=cross_coverage,
        recommended_threshold=gate.threshold,
        gate_metrics=gate,
        json_path=json_path,
        csv_path=csv_path,
        markdown_path=markdown_path,
        chart_path=chart_path,
        evaluated_at=datetime.now(UTC).isoformat(),
        benchmark_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        calibration_case_count=len(calibration),
        test_case_count=len(test),
        embedding_model=_embedding_model(corpus),
        methodology_note=(
            "Single-paper retrieval is unfiltered; multi-paper questions use the "
            "production balanced-retrieval path. The gate threshold is selected on "
            "the calibration split and reported only on the held-out test split."
        ),
    )
    _write_csv(results, csv_path)
    _write_chart(results, gate.threshold, chart_path)
    markdown_path.write_text(_markdown(evaluation), encoding="utf-8")
    json_path.write_text(
        evaluation.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return evaluation


def _evaluate_case(
    corpus: SearchableCorpus,
    case: EvaluationCase,
    top_k: int,
    split: Literal["calibration", "test"],
    document_ids: tuple[str, ...],
) -> RetrievalCaseResult:
    if len(case.relevant_paper_ids) > 1 and hasattr(corpus, "search_balanced"):
        evidence = corpus.search_balanced(
            case.query,
            case.relevant_paper_ids,
            top_k=top_k,
            per_paper=max(
                1,
                (top_k + len(case.relevant_paper_ids) - 1) // len(case.relevant_paper_ids),
            ),
            section_types=ANSWERABLE_SECTION_TYPES,
        )
    else:
        # Deliberately unfiltered: naming a paper must not make Hit@k automatic.
        evidence = corpus.search(
            case.query,
            top_k=top_k,
            paper_ids=None,
            section_types=ANSWERABLE_SECTION_TYPES,
        )
    retrieved = [item.paper_id for item in evidence]
    relevant = set(case.relevant_paper_ids)
    first_rank = next(
        (rank for rank, paper_id in enumerate(retrieved, start=1) if paper_id in relevant),
        None,
    )
    return RetrievalCaseResult(
        case_id=case.case_id,
        query=case.query,
        scope=case.scope,
        relevant_paper_ids=case.relevant_paper_ids,
        split=split,
        mentioned_paper_ids=sorted(mentioned_document_ids(case.query, document_ids)),
        retrieved_paper_ids=retrieved,
        top_similarity=evidence[0].similarity if evidence else -1.0,
        first_relevant_rank=first_rank,
        relevant_paper_coverage=(
            len(relevant.intersection(retrieved)) / len(relevant) if relevant else 0.0
        ),
    )


def _assign_splits(
    cases: list[EvaluationCase],
) -> list[Literal["calibration", "test"]]:
    counters = {"in_scope": 0, "out_of_scope": 0}
    assigned: list[Literal["calibration", "test"]] = []
    for case in cases:
        if case.split is not None:
            assigned.append(case.split)
            continue
        index = counters[case.scope]
        counters[case.scope] += 1
        assigned.append("calibration" if index % 2 == 0 else "test")
    return assigned


def _embedding_model(corpus: SearchableCorpus) -> str:
    index = getattr(corpus, "index", None)
    index = getattr(index, "primary", index)
    embedder = getattr(index, "embedder", None)
    return str(getattr(embedder, "model_name", "unknown"))


def _select_threshold(results: list[RetrievalCaseResult]) -> GateMetrics:
    scores = sorted({result.top_similarity for result in results})
    candidates = [scores[0] - 1e-6, scores[-1] + 1e-6]
    candidates.extend((left + right) / 2 for left, right in zip(scores, scores[1:], strict=False))
    metrics = [_gate_metrics(results, threshold) for threshold in candidates]
    return max(
        metrics,
        key=lambda item: (
            item.balanced_accuracy,
            item.specificity,
            item.f1,
            item.threshold,
        ),
    )


def _gate_metrics(
    results: list[RetrievalCaseResult],
    threshold: float,
) -> GateMetrics:
    true_positive = false_positive = true_negative = false_negative = 0
    for result in results:
        predicted_in_scope = bool(result.mentioned_paper_ids or result.top_similarity >= threshold)
        actually_in_scope = result.scope == "in_scope"
        if predicted_in_scope and actually_in_scope:
            true_positive += 1
        elif predicted_in_scope:
            false_positive += 1
        elif actually_in_scope:
            false_negative += 1
        else:
            true_negative += 1

    sensitivity = _ratio(true_positive, true_positive + false_negative)
    specificity = _ratio(true_negative, true_negative + false_positive)
    precision = _ratio(true_positive, true_positive + false_positive)
    f1 = _ratio(2 * precision * sensitivity, precision + sensitivity)
    return GateMetrics(
        threshold=round(threshold, 6),
        sensitivity=sensitivity,
        specificity=specificity,
        precision=precision,
        f1=f1,
        balanced_accuracy=(sensitivity + specificity) / 2,
    )


def _write_csv(results: list[RetrievalCaseResult], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "scope",
                "split",
                "query",
                "relevant_paper_ids",
                "mentioned_paper_ids",
                "retrieved_paper_ids",
                "top_similarity",
                "first_relevant_rank",
                "relevant_paper_coverage",
            ],
        )
        writer.writeheader()
        for result in results:
            row = result.model_dump(mode="json")
            row["relevant_paper_ids"] = ", ".join(row["relevant_paper_ids"])
            row["mentioned_paper_ids"] = ", ".join(row["mentioned_paper_ids"])
            row["retrieved_paper_ids"] = ", ".join(row["retrieved_paper_ids"])
            writer.writerow(row)


def _write_chart(
    results: list[RetrievalCaseResult],
    threshold: float,
    path: Path,
) -> None:
    fig, axis = plt.subplots(figsize=(10, 5.5), dpi=150)
    colors = {"in_scope": "#2563EB", "out_of_scope": "#DC2626"}
    positions = {"in_scope": 1, "out_of_scope": 0}
    for scope in ("in_scope", "out_of_scope"):
        subset = [result for result in results if result.scope == scope]
        axis.scatter(
            [result.top_similarity for result in subset],
            [positions[scope]] * len(subset),
            s=55,
            alpha=0.8,
            color=colors[scope],
            label=scope.replace("_", " "),
        )
    axis.axvline(
        threshold,
        color="#111827",
        linestyle="--",
        label=f"recommended threshold = {threshold:.3f}",
    )
    axis.set_xlim(0, 1)
    axis.set_yticks([0, 1], ["Out of scope", "In scope"])
    axis.set_xlabel("Top cosine similarity")
    axis.set_title("Retrieval sufficiency-gate calibration")
    axis.grid(axis="x", alpha=0.2)
    axis.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _markdown(evaluation: RetrievalEvaluation) -> str:
    gate = evaluation.gate_metrics
    lines = [
        "# Retrieval evaluation",
        "",
        f"- In-scope Hit@1: **{evaluation.hit_at_1:.1%}**",
        f"- In-scope Hit@6: **{evaluation.hit_at_k:.1%}**",
        f"- Mean Reciprocal Rank: **{evaluation.mean_reciprocal_rank:.3f}**",
        f"- Cross-paper coverage@6: **{evaluation.cross_paper_coverage_at_k:.1%}**",
        f"- Recommended similarity threshold: **{evaluation.recommended_threshold:.3f}**",
        f"- Gate sensitivity: **{gate.sensitivity:.1%}**",
        f"- Gate specificity: **{gate.specificity:.1%}**",
        f"- Gate balanced accuracy: **{gate.balanced_accuracy:.1%}**",
        "- Calibration / held-out cases: "
        f"**{evaluation.calibration_case_count} / {evaluation.test_case_count}**",
        f"- Embedding model: **{evaluation.embedding_model}**",
        "",
        f"> {evaluation.methodology_note}",
        "",
        "| Case | Split | Scope | Top similarity | Relevant rank | Coverage | Top paper | Query |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for result in evaluation.cases:
        query = result.query.replace("|", "\\|")
        rank = str(result.first_relevant_rank or "—")
        top_paper = result.retrieved_paper_ids[0] if result.retrieved_paper_ids else "—"
        lines.append(
            f"| {result.case_id} | {result.split} | {result.scope} | "
            f"{result.top_similarity:.3f} | {rank} | "
            f"{result.relevant_paper_coverage:.1%} | {top_paper} | {query} |"
        )
    return "\n".join(lines) + "\n"


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
