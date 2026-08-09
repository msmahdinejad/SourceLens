from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from paperlens.corpus.index import ChromaEmbeddingIndex
from paperlens.domain import DocumentChunk, Evidence
from paperlens.evaluation import BenchmarkCase, RetrievalBenchmark, load_benchmark
from paperlens.language_models.base import execute_generated_tool
from paperlens.retrieval import SQLiteFTSIndex, reciprocal_rank_fusion
from paperlens.tools.registry import ResearchTools

Strategy = Literal["vector", "legacy_hybrid", "fts_hybrid"]
_TERM = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)


class BenchmarkStrategyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy: Strategy
    case_count: int
    test_case_count: int
    threshold: float
    recall_at_6: float
    citation_precision: float
    citation_recall: float
    abstention_accuracy: float
    p50_latency_ms: float
    p95_latency_ms: float
    category_recall: dict[str, float]
    passes_thresholds: bool


class AgentBenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int
    planned_tool_call_count: int
    initial_tool_call_validity: float
    repaired_tool_call_validity: float
    multi_search_case_count: int
    multi_search_contract_success: float
    citation_precision: float


class ScalingBenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_count: int
    document_count: int
    query_count: int
    repetitions: int
    recall_at_6: float
    p50_latency_ms: float
    p95_latency_ms: float
    index_build_ms: float


class GeneralBenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    benchmark_id: str
    benchmark_sha256: str
    repetitions: int
    document_count: int
    chunk_count: int
    case_count: int
    results: list[BenchmarkStrategyResult]
    before: BenchmarkStrategyResult
    after: BenchmarkStrategyResult
    agent: AgentBenchmarkResult
    scaling: ScalingBenchmarkResult
    graph_experiment_recommended: bool
    graph_gate_reason: str
    json_path: Path
    markdown_path: Path


class _CaseRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    case: BenchmarkCase
    evidence: list[Evidence]
    top_score: float
    latency_ms: float


class _HashingEmbedder:
    """Deterministic benchmark embedder; avoids network and model-cache effects."""

    model_name = "benchmark-hashing-v1"
    dimensions = 384

    def embed_passages(self, texts: list[str], titles: list[str]) -> np.ndarray:
        return np.vstack(
            [self._embed(f"{title} {text}") for text, title in zip(texts, titles, strict=True)]
        )

    def embed_query(self, query: str) -> np.ndarray:
        return self._embed(query)

    def _embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimensions, dtype=np.float32)
        terms = _terms(text)
        features = [*terms, *(f"{a}_{b}" for a, b in zip(terms, terms[1:], strict=False))]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "little") % self.dimensions
            vector[index] += 1.0
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector


def run_general_benchmark(
    fixture_path: Path,
    outputs_path: Path,
    *,
    repetitions: int = 3,
    scale_chunk_count: int = 10_000,
) -> GeneralBenchmarkReport:
    if repetitions < 1:
        raise ValueError("Benchmark repetitions must be positive")
    benchmark = load_benchmark(fixture_path)
    fixture_sha256 = _fixture_sha256(fixture_path)
    target = outputs_path / "retrieval_benchmark"
    target.mkdir(parents=True, exist_ok=True)
    chunks = _chunks(benchmark)
    collection_id = f"general_retrieval_{fixture_sha256[:16]}"
    vector = ChromaEmbeddingIndex(
        target / "chroma",
        _HashingEmbedder(),
        collection_name=collection_id,
    )
    vector.upsert(chunks)
    fts = SQLiteFTSIndex(target / "retrieval.sqlite3")
    fts.replace_collection(collection_id, chunks)

    results = [
        _measure_strategy(
            strategy,
            benchmark,
            vector,
            fts,
            collection_id,
            repetitions,
        )
        for strategy in ("vector", "legacy_hybrid", "fts_hybrid")
    ]
    before = next(result for result in results if result.strategy == "legacy_hybrid")
    after = next(result for result in results if result.strategy == "fts_hybrid")
    agent = _measure_agent_contract(benchmark, vector, fts, collection_id)
    scaling = _measure_scaling(
        benchmark,
        chunks,
        target,
        fixture_sha256=fixture_sha256,
        chunk_count=scale_chunk_count,
        repetitions=max(3, repetitions),
    )
    graph_recommended, graph_reason = _graph_gate(after)
    json_path = target / "retrieval_benchmark.json"
    markdown_path = target / "retrieval_benchmark.md"
    report = GeneralBenchmarkReport(
        benchmark_id=benchmark.benchmark_id,
        benchmark_sha256=fixture_sha256,
        repetitions=repetitions,
        document_count=len(benchmark.documents),
        chunk_count=len(chunks),
        case_count=len(benchmark.cases),
        results=results,
        before=before,
        after=after,
        agent=agent,
        scaling=scaling,
        graph_experiment_recommended=graph_recommended,
        graph_gate_reason=graph_reason,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return report


def _measure_strategy(
    strategy: Strategy,
    benchmark: RetrievalBenchmark,
    vector: ChromaEmbeddingIndex,
    fts: SQLiteFTSIndex,
    collection_id: str,
    repetitions: int,
) -> BenchmarkStrategyResult:
    runs: list[_CaseRun] = []
    for case in benchmark.cases:
        evidence: list[Evidence] = []
        timings: list[float] = []
        for _ in range(repetitions):
            started = perf_counter()
            evidence = _search(strategy, case.query, vector, fts, collection_id)
            timings.append((perf_counter() - started) * 1000)
        runs.append(
            _CaseRun(
                case=case,
                evidence=evidence,
                top_score=max((item.similarity for item in evidence), default=-1.0),
                latency_ms=statistics.median(timings),
            )
        )

    calibration = [run for run in runs if run.case.split == "calibration"]
    test = [run for run in runs if run.case.split == "test"]
    threshold = _select_threshold(calibration)
    in_scope = [run for run in test if run.case.scope == "in_scope"]
    recall = statistics.fmean(_document_recall(run) for run in in_scope)
    precision = statistics.fmean(_citation_precision(run) for run in in_scope)
    citation_recall = statistics.fmean(_citation_recall(run) for run in in_scope)
    abstention = statistics.fmean(
        float((run.top_score >= threshold) == (run.case.scope == "in_scope")) for run in test
    )
    category_recall = {
        category: statistics.fmean(
            _document_recall(run) for run in in_scope if run.case.category == category
        )
        for category in sorted({run.case.category for run in in_scope})
    }
    latencies = sorted(run.latency_ms for run in runs)
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    thresholds = benchmark.thresholds
    return BenchmarkStrategyResult(
        strategy=strategy,
        case_count=len(runs),
        test_case_count=len(test),
        threshold=threshold,
        recall_at_6=recall,
        citation_precision=precision,
        citation_recall=citation_recall,
        abstention_accuracy=abstention,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        category_recall=category_recall,
        passes_thresholds=(
            recall >= thresholds.recall_at_6
            and precision >= thresholds.citation_precision
            and abstention >= thresholds.abstention_accuracy
            and p95 <= thresholds.p95_latency_ms
        ),
    )


def _measure_agent_contract(
    benchmark: RetrievalBenchmark,
    vector: ChromaEmbeddingIndex,
    fts: SQLiteFTSIndex,
    collection_id: str,
) -> AgentBenchmarkResult:
    """Exercise provider dialect normalization and the multi-search tool contract."""
    spec = ResearchTools.document_specs()[0]
    specs = {spec.name: spec}
    planned_calls = initial_valid = repaired_valid = 0
    multi_cases = multi_successes = 0
    citation_scores: list[float] = []

    def execute_tool(name: str, arguments: dict[str, Any]) -> str:
        filters = arguments["filters"] or {}
        document_ids = set(filters.get("document_ids") or []) or None
        top_k = int(filters.get("top_k") or 6)
        evidence = _filtered_hybrid_search(
            arguments["query"],
            vector,
            fts,
            collection_id,
            document_ids=document_ids,
            top_k=top_k,
        )
        return json.dumps(
            [
                {
                    "document_id": item.document_id or item.paper_id,
                    "page": item.page_start,
                }
                for item in evidence
            ]
        )

    call_position = 0
    for case in benchmark.cases:
        is_multi_search = case.category in {"multi_hop", "contradiction"}
        planned_queries = (
            [case.query, f"Independent corroboration: {case.query}"]
            if is_multi_search
            else [case.query]
        )
        if is_multi_search:
            multi_cases += 1
        successful_queries: set[str] = set()
        cited: list[tuple[str, int]] = []
        for planned_query in planned_queries:
            filters = {
                "document_ids": None,
                "section_types": None,
                "chunk_types": None,
                "page_start": None,
                "page_end": None,
                "top_k": 6,
            }
            provider_filters: dict[str, Any] | str = (
                json.dumps(filters, ensure_ascii=False)
                if call_position % 2
                else filters
            )
            raw_arguments = json.dumps(
                {"query": planned_query, "filters": provider_filters},
                ensure_ascii=False,
            )
            inject_malformed = call_position % 17 == 0
            if inject_malformed:
                raw_arguments = json.dumps(
                    {"query": case.query, "filters": "{malformed-json"},
                    ensure_ascii=False,
                )
            planned_calls += 1
            call, valid = execute_generated_tool(
                "search_documents", raw_arguments, specs, execute_tool
            )
            initial_valid += int(valid)
            if not valid:
                call, valid = execute_generated_tool(
                    "search_documents",
                    json.dumps(
                        {"query": planned_query, "filters": provider_filters},
                        ensure_ascii=False,
                    ),
                    specs,
                    execute_tool,
                )
            repaired_valid += int(valid)
            if valid:
                successful_queries.add(planned_query)
                results = json.loads(call.output)
                if results:
                    cited.append((str(results[0]["document_id"]), int(results[0]["page"])))
            call_position += 1

        if is_multi_search and successful_queries == set(planned_queries):
            multi_successes += 1
        if case.scope == "in_scope":
            expected = {
                (citation.document_id, page)
                for citation in case.expected_citations
                for page in citation.pages
            }
            citation_scores.append(
                sum(candidate in expected for candidate in cited) / max(1, len(cited))
            )

    return AgentBenchmarkResult(
        case_count=len(benchmark.cases),
        planned_tool_call_count=planned_calls,
        initial_tool_call_validity=initial_valid / planned_calls,
        repaired_tool_call_validity=repaired_valid / planned_calls,
        multi_search_case_count=multi_cases,
        multi_search_contract_success=multi_successes / max(1, multi_cases),
        citation_precision=statistics.fmean(citation_scores),
    )


def _filtered_hybrid_search(
    query: str,
    vector: ChromaEmbeddingIndex,
    fts: SQLiteFTSIndex,
    collection_id: str,
    *,
    document_ids: set[str] | None,
    top_k: int,
) -> list[Evidence]:
    vector_results = vector.search(query, top_k=max(18, top_k), paper_ids=document_ids)
    lexical_results = fts.search(
        collection_id,
        query,
        limit=max(18, top_k),
        document_ids=document_ids,
    )
    return reciprocal_rank_fusion(vector_results, lexical_results)[:top_k]


def _measure_scaling(
    benchmark: RetrievalBenchmark,
    base_chunks: list[DocumentChunk],
    target: Path,
    *,
    fixture_sha256: str,
    chunk_count: int,
    repetitions: int,
) -> ScalingBenchmarkResult:
    if chunk_count < len(base_chunks):
        raise ValueError("Scale chunk count cannot be smaller than the fixture")
    chunks = [*base_chunks, *_noise_chunks(chunk_count - len(base_chunks))]
    collection_id = f"long_workspace_{fixture_sha256[:12]}_{chunk_count}"
    started = perf_counter()
    vector = ChromaEmbeddingIndex(
        target / "scale_chroma",
        _HashingEmbedder(),
        collection_name=collection_id,
    )
    vector.upsert(chunks)
    fts = SQLiteFTSIndex(target / "scale_retrieval.sqlite3")
    fts.replace_collection(collection_id, chunks)
    build_ms = (perf_counter() - started) * 1000

    cases = [
        case
        for case in benchmark.cases
        if case.scope == "in_scope" and case.split == "test"
    ][:12]
    latencies: list[float] = []
    recalls: list[float] = []
    for case in cases:
        evidence: list[Evidence] = []
        case_timings = []
        for _ in range(repetitions):
            started = perf_counter()
            evidence = _search("fts_hybrid", case.query, vector, fts, collection_id)
            case_timings.append((perf_counter() - started) * 1000)
        latencies.append(statistics.median(case_timings))
        found = {item.document_id or item.paper_id for item in evidence}
        expected = set(case.relevant_document_ids)
        recalls.append(len(found & expected) / len(expected))

    ordered = sorted(latencies)
    return ScalingBenchmarkResult(
        chunk_count=chunk_count,
        document_count=len({chunk.document_id for chunk in chunks}),
        query_count=len(cases),
        repetitions=repetitions,
        recall_at_6=statistics.fmean(recalls),
        p50_latency_ms=_percentile(ordered, 0.50),
        p95_latency_ms=_percentile(ordered, 0.95),
        index_build_ms=build_ms,
    )


def _noise_chunks(count: int) -> list[DocumentChunk]:
    source_hash = hashlib.sha256(b"deterministic long-workspace distractors").hexdigest()
    return [
        DocumentChunk(
            chunk_id=f"scale-noise:{index}",
            paper_id=f"scale-noise-{index // 200}",
            document_id=f"scale-noise-{index // 200}",
            workspace_id="retrieval-benchmark-scale",
            paper_title=f"Background archive volume {index // 200}",
            filename=f"background-{index // 200}.fixture.json",
            section=f"Record {index}",
            section_type="body",
            page_start=index % 200 + 1,
            page_end=index % 200 + 1,
            chunk_index=index,
            token_count=12,
            text=(
                f"Background archive record {index} contains neutral inventory ledger "
                f"token n{index:06d} and no benchmark entity evidence."
            ),
            source_sha256=source_hash,
        )
        for index in range(count)
    ]


def _search(
    strategy: Strategy,
    query: str,
    vector: ChromaEmbeddingIndex,
    fts: SQLiteFTSIndex,
    collection_id: str,
) -> list[Evidence]:
    vector_results = vector.search(query, top_k=18)
    if strategy == "vector":
        return vector_results[:6]
    if strategy == "legacy_hybrid":
        lexical_results = _legacy_lexical_search(vector, query, limit=18)
    else:
        lexical_results = fts.search(collection_id, query, limit=18)
    return reciprocal_rank_fusion(vector_results, lexical_results)[:6]


def _legacy_lexical_search(
    vector: ChromaEmbeddingIndex,
    query: str,
    *,
    limit: int,
) -> list[Evidence]:
    """Reproduce the pre-FTS bounded Chroma metadata scan for comparison only."""
    query_terms = Counter(_terms(query))
    payload = vector.collection.get(limit=2000, include=["documents", "metadatas"])
    ranked: list[tuple[float, Evidence]] = []
    for chunk_id, text, metadata in zip(
        payload.get("ids", []),
        payload.get("documents", []),
        payload.get("metadatas", []),
        strict=True,
    ):
        score = sum((Counter(_terms(str(text))) & query_terms).values()) / max(
            1, sum(query_terms.values())
        )
        if score <= 0:
            continue
        ranked.append((score, _evidence(str(chunk_id), str(text), metadata, score)))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
    return [item for _, item in ranked[:limit]]


def _evidence(chunk_id: str, text: str, metadata: dict, score: float) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        paper_id=str(metadata["paper_id"]),
        paper_title=str(metadata["paper_title"]),
        section=str(metadata["section"]),
        section_type=str(metadata["section_type"]),
        page_start=int(metadata["page_start"]),
        page_end=int(metadata["page_end"]),
        text=text,
        similarity=max(-1.0, min(1.0, score)),
        workspace_id=str(metadata.get("workspace_id", "benchmark")),
        document_id=str(metadata.get("document_id", metadata["paper_id"])),
        filename=str(metadata.get("filename", "")),
        chunk_type=str(metadata.get("chunk_type", "text")),
        extraction_method=str(metadata.get("extraction_method", "text")),
    )


def _chunks(benchmark: RetrievalBenchmark) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    for document in benchmark.documents:
        source_hash = hashlib.sha256(
            "\n".join(page.text for page in document.pages).encode("utf-8")
        ).hexdigest()
        for index, page in enumerate(document.pages):
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{document.document_id}:p{page.page}",
                    paper_id=document.document_id,
                    document_id=document.document_id,
                    workspace_id="retrieval-benchmark",
                    paper_title=document.title,
                    filename=f"{document.document_id}.fixture.json",
                    section=f"Page {page.page}",
                    section_type="body",
                    page_start=page.page,
                    page_end=page.page,
                    chunk_index=index,
                    token_count=len(_terms(page.text)),
                    text=page.text,
                    source_sha256=source_hash,
                )
            )
    return chunks


def _terms(text: str) -> list[str]:
    normalized = text.casefold().translate(str.maketrans("يىك", "ییک"))
    return _TERM.findall(normalized)


def _document_recall(run: _CaseRun) -> float:
    expected = set(run.case.relevant_document_ids)
    found = {item.document_id or item.paper_id for item in run.evidence}
    return len(expected & found) / len(expected) if expected else 0.0


def _citation_precision(run: _CaseRun) -> float:
    expected = {
        (citation.document_id, page)
        for citation in run.case.expected_citations
        for page in citation.pages
    }
    cited = [
        (item.document_id or item.paper_id, item.page_start)
        for item in run.evidence[: max(1, len(run.case.expected_citations))]
    ]
    return sum(citation in expected for citation in cited) / len(cited)


def _citation_recall(run: _CaseRun) -> float:
    found = {
        (item.document_id or item.paper_id, page)
        for item in run.evidence
        for page in range(item.page_start, item.page_end + 1)
    }
    expected = [
        {(citation.document_id, page) for page in citation.pages}
        for citation in run.case.expected_citations
    ]
    return sum(bool(group & found) for group in expected) / len(expected)


def _select_threshold(runs: list[_CaseRun]) -> float:
    scores = sorted({run.top_score for run in runs})
    candidates = [scores[0] - 1e-6, scores[-1] + 1e-6]
    candidates.extend((left + right) / 2 for left, right in zip(scores, scores[1:], strict=False))

    def quality(threshold: float) -> tuple[float, float, float, float]:
        in_scope = [run for run in runs if run.case.scope == "in_scope"]
        out_scope = [run for run in runs if run.case.scope == "out_of_scope"]
        sensitivity = statistics.fmean(run.top_score >= threshold for run in in_scope)
        specificity = statistics.fmean(run.top_score < threshold for run in out_scope)
        return ((sensitivity + specificity) / 2, sensitivity, specificity, -threshold)

    return round(max(candidates, key=quality), 6)


def _graph_gate(result: BenchmarkStrategyResult) -> tuple[bool, str]:
    targeted = [
        result.category_recall.get(category, 0.0)
        for category in ("entity_alias", "multi_hop", "contradiction")
    ]
    targeted_recall = statistics.fmean(targeted)
    recommended = targeted_recall < 0.80 or result.citation_recall < 0.75
    reason = (
        f"Graph gate {'opened' if recommended else 'closed'}: targeted entity/multi-hop/"
        f"contradiction recall={targeted_recall:.1%}, citation recall="
        f"{result.citation_recall:.1%}; minimums are 80% and 75%."
    )
    return recommended, reason


def _percentile(values: list[float], quantile: float) -> float:
    index = (len(values) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def _fixture_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for name in ("documents.json", "questions.json", "provenance.json"):
        if not (path / name).is_file():
            continue
        digest.update((path / name).read_bytes())
    return digest.hexdigest()


def _markdown(report: GeneralBenchmarkReport) -> str:
    lines = [
        "# General retrieval benchmark",
        "",
        f"- Benchmark: `{report.benchmark_id}` (`{report.benchmark_sha256}`)",
        f"- Corpus: **{report.document_count} documents / {report.chunk_count} chunks / "
        f"{report.case_count} questions**",
        f"- Repetitions per query: **{report.repetitions}**",
        f"- {report.graph_gate_reason}",
        "",
        "| Strategy | Recall@6 | Citation precision | Citation recall | Abstention "
        "| p50 ms | p95 ms | Pass |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for result in report.results:
        lines.append(
            f"| {result.strategy} | {result.recall_at_6:.1%} | "
            f"{result.citation_precision:.1%} | {result.citation_recall:.1%} | "
            f"{result.abstention_accuracy:.1%} | {result.p50_latency_ms:.2f} | "
            f"{result.p95_latency_ms:.2f} | {'yes' if result.passes_thresholds else 'no'} |"
        )
    agent = report.agent
    scaling = report.scaling
    lines.extend(
        [
            "",
            "## Agent/tool contract",
            "",
            f"- Initial provider-dialect tool-call validity: "
            f"**{agent.initial_tool_call_validity:.1%}**",
            f"- Validity after recoverable repair: **{agent.repaired_tool_call_validity:.1%}**",
            f"- Deterministic multi-search contract success: "
            f"**{agent.multi_search_contract_success:.1%}** "
            f"across {agent.multi_search_case_count} cases",
            f"- Tool-selected citation precision: **{agent.citation_precision:.1%}**",
            "",
            "## Long-workspace scaling",
            "",
            f"- **{scaling.chunk_count:,} chunks / {scaling.document_count} documents**",
            f"- Recall@6: **{scaling.recall_at_6:.1%}**",
            f"- p50 / p95: **{scaling.p50_latency_ms:.2f} / "
            f"{scaling.p95_latency_ms:.2f} ms**",
            f"- Deterministic local index build: **{scaling.index_build_ms:.2f} ms**",
        ]
    )
    return "\n".join(lines) + "\n"
