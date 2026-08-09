from pathlib import Path

import pytest

from paperlens.domain import Evidence
from paperlens.evaluation import evaluate_retrieval


class FakeCorpus:
    def search(self, query: str, **kwargs) -> list[Evidence]:
        if query == "known":
            paper_id, similarity = "homecharge", 0.9
        else:
            paper_id, similarity = "smartcharge", 0.4
        return [
            Evidence(
                chunk_id=f"{paper_id}-1",
                paper_id=paper_id,
                paper_title=paper_id,
                section="Test",
                section_type="other",
                page_start=1,
                page_end=1,
                text="evidence",
                similarity=similarity,
            )
        ]


def test_evaluation_calibrates_gate_and_writes_artifacts(tmp_path: Path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        """
        [
          {
            "case_id": "in-calibration",
            "query": "known",
            "scope": "in_scope",
            "relevant_paper_ids": ["homecharge"],
            "split": "calibration"
          },
          {
            "case_id": "out-calibration",
            "query": "unknown",
            "scope": "out_of_scope",
            "relevant_paper_ids": [],
            "split": "calibration"
          },
          {
            "case_id": "in-test",
            "query": "known",
            "scope": "in_scope",
            "relevant_paper_ids": ["homecharge"],
            "split": "test"
          },
          {
            "case_id": "out-test",
            "query": "unknown",
            "scope": "out_of_scope",
            "relevant_paper_ids": [],
            "split": "test"
          }
        ]
        """,
        encoding="utf-8",
    )

    report = evaluate_retrieval(FakeCorpus(), cases, tmp_path / "outputs")

    assert report.hit_at_1 == 1.0
    assert report.gate_metrics.balanced_accuracy == 1.0
    assert report.calibration_case_count == report.test_case_count == 2
    assert 0.4 < report.recommended_threshold < 0.9
    assert report.markdown_path.is_file()
    assert report.chart_path.is_file()


def test_evaluation_rejects_a_calibration_split_without_out_of_scope_cases(
    tmp_path: Path,
) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        """
        [
          {
            "case_id": "in-calibration",
            "query": "known",
            "scope": "in_scope",
            "relevant_paper_ids": ["homecharge"],
            "split": "calibration"
          },
          {
            "case_id": "in-test",
            "query": "known",
            "scope": "in_scope",
            "relevant_paper_ids": ["homecharge"],
            "split": "test"
          },
          {
            "case_id": "out-test",
            "query": "unknown",
            "scope": "out_of_scope",
            "relevant_paper_ids": [],
            "split": "test"
          }
        ]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="both splits"):
        evaluate_retrieval(FakeCorpus(), cases, tmp_path / "outputs")
