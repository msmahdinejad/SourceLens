from pathlib import Path

from paperlens.evaluation import load_benchmark, run_general_benchmark

REAL_FIXTURE = Path("fixtures/retrieval_wikidata")


def test_general_benchmark_has_lawful_versioned_real_world_truth() -> None:
    fixture = load_benchmark(REAL_FIXTURE)

    assert fixture.license == "CC0-1.0"
    assert fixture.authored_for_sourcelens is False
    assert fixture.source_kind == "wikidata"
    assert fixture.source_count == len(fixture.documents)
    assert len(fixture.documents) >= 12
    assert sum(len(document.pages) for document in fixture.documents) >= 48
    assert 40 <= len(fixture.cases) <= 50
    assert {case.language for case in fixture.cases} == {"en", "fa"}
    assert {case.category for case in fixture.cases} == {
        "abstention",
        "citation",
        "contradiction",
        "entity_alias",
        "equation",
        "exact_identifier",
        "multi_hop",
        "temporal",
    }
    assert all(case.expected_citations for case in fixture.cases if case.scope == "in_scope")


def test_general_benchmark_compares_vector_legacy_and_fts_retrieval(tmp_path: Path) -> None:
    report = run_general_benchmark(
        REAL_FIXTURE,
        tmp_path,
        repetitions=1,
        scale_chunk_count=1_000,
    )

    assert {result.strategy for result in report.results} == {
        "fts_hybrid",
        "legacy_hybrid",
        "vector",
    }
    assert all(result.case_count == report.case_count for result in report.results)
    assert report.document_count >= 12
    assert report.chunk_count >= 48
    assert report.after.strategy == "fts_hybrid"
    assert report.before.strategy == "legacy_hybrid"
    assert report.after.passes_thresholds is True
    assert report.agent.initial_tool_call_validity >= 0.90
    assert report.agent.repaired_tool_call_validity == 1.0
    assert report.agent.multi_search_contract_success == 1.0
    assert report.scaling.chunk_count == 1_000
    assert report.scaling.p95_latency_ms <= 250
    assert report.graph_experiment_recommended is False
    assert report.json_path.is_file()
    assert report.markdown_path.is_file()
