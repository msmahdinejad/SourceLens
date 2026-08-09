import hashlib
import json
from pathlib import Path

import pytest

from paperlens.domain import ExtractedPaper, PageContent, PaperRecord
from paperlens.tools.results import (
    COMPARISON_WARNING,
    ResultsRepository,
    compare_results,
    extract_experimental_results,
)


@pytest.fixture
def repository(tmp_path: Path) -> ResultsRepository:
    identities = (
        ("homecharge", "Residual model", 77.15, 93.29),
        ("retailcharge", "Aggregated model", 78.80, 94.40),
        ("smartcharge", "Attention model", 83.12, 96.42),
        ("solarhub", "Scaled model", 84.40, 97.10),
    )
    rows = [
        {
            "profile": profile,
            "paper_id": paper_id,
            "paper_title": title,
            "model": title,
            "dataset": "Benchmark-1K",
            "split": "validation",
            "top1_accuracy": top1,
            "top5_accuracy": top5,
            "top1_reported": top1,
            "top5_reported": top5,
            "reported_as": "accuracy",
            "crop_count": 1,
            "crop_size": "224",
            "inference_protocol": "single model/crop",
            "single_model": True,
            "ensemble": False,
            "parameters_million": None,
            "flops_billion": None,
            "source_pdf": f"{paper_id}.pdf",
            "source_pages": "1",
            "source_location": "Table 1",
            "reported_by": paper_id,
            "strictly_comparable": False,
        }
        for profile in ("representative_models", "best_single_model_validation")
        for paper_id, title, top1, top5 in identities
    ]
    data_path = tmp_path / "data"
    cache_path = data_path / "cache" / "extracted"
    papers_path = tmp_path / "papers"
    cache_path.mkdir(parents=True)
    papers_path.mkdir()
    verified_path = data_path / "verified_results.json"
    verified_path.write_text(json.dumps(rows), encoding="utf-8")
    for paper_id in {row["paper_id"] for row in rows}:
        paper_rows = [row for row in rows if row["paper_id"] == paper_id]
        source_pdf = paper_rows[0]["source_pdf"]
        pdf_bytes = f"%PDF-1.4\n{paper_id} fixture\n".encode()
        (papers_path / source_pdf).write_bytes(pdf_bytes)
        reported_values = " ".join(
            str(value)
            for row in paper_rows
            for value in (row["top1_reported"], row["top5_reported"])
        )
        paper = ExtractedPaper(
            record=PaperRecord(
                paper_id=paper_id,
                file_name=source_pdf,
                title=paper_rows[0]["paper_title"],
                authors=["Fixture Author"],
                year=2026,
                venue="Fixture",
                source_url="https://example.test/paper.pdf",
            ),
            pages=[
                PageContent(page_number=page, markdown=reported_values) for page in range(1, 51)
            ],
            sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        )
        (cache_path / f"{paper_id}.json").write_text(
            paper.model_dump_json(), encoding="utf-8"
        )
    return ResultsRepository(verified_path)


def test_verified_results_cover_two_metrics_and_four_papers(
    repository: ResultsRepository,
) -> None:
    rows = repository.load("best_single_model_validation")

    assert len(rows) == 4
    assert {row.paper_id for row in rows} == {
        "homecharge",
        "retailcharge",
        "smartcharge",
        "solarhub",
    }
    assert all(0 < row.top1_accuracy <= row.top5_accuracy <= 100 for row in rows)
    assert max(rows, key=lambda row: row.top1_accuracy).paper_id == "solarhub"


def test_extract_exports_machine_and_human_readable_artifacts(
    tmp_path: Path, repository: ResultsRepository
) -> None:
    artifacts = extract_experimental_results(repository, tmp_path)

    assert artifacts.json_path.is_file()
    assert artifacts.csv_path.is_file()
    assert artifacts.markdown_path.is_file()
    assert len(json.loads(artifacts.json_path.read_text(encoding="utf-8"))) == 4
    assert COMPARISON_WARNING in artifacts.markdown_path.read_text(encoding="utf-8")


def test_compare_results_creates_nonempty_png(
    tmp_path: Path, repository: ResultsRepository
) -> None:
    artifacts = compare_results(repository, tmp_path)

    assert artifacts.chart_path is not None
    assert artifacts.chart_path.stat().st_size > 10_000
