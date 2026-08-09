from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import matplotlib
from pydantic import BaseModel, ConfigDict

from paperlens.corpus.chunking import SectionParser
from paperlens.domain import Evidence, ExtractedPaper

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_REFERENCE_START = re.compile(r"(?m)(?=^\s*-\s+)")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


class ReferenceComparisonArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    counts: dict[str, dict[int, int]]
    csv_path: Path
    markdown_path: Path
    chart_path: Path
    warning: str
    evidence: list[Evidence]


class ReferenceAnalyzer:
    """Count publication years from cached References sections."""

    warning = (
        "Reference years were parsed automatically from PDF text and should be "
        "manually audited before publication."
    )

    def __init__(self, extracted_cache_path: Path, outputs_path: Path) -> None:
        self.extracted_cache_path = extracted_cache_path
        self.outputs_path = outputs_path

    def compare(self) -> ReferenceComparisonArtifact:
        counts = {}
        evidence: list[Evidence] = []
        for cache_file in sorted(self.extracted_cache_path.glob("*.json")):
            paper = ExtractedPaper.model_validate_json(cache_file.read_text(encoding="utf-8"))
            fragments = [
                fragment
                for fragment in SectionParser().parse(paper)
                if fragment.section_type == "references"
            ]
            reference_text = "\n\n".join(fragment.text for fragment in fragments)
            counts[paper.record.paper_id] = dict(
                sorted(self.count_reference_years(reference_text).items())
            )
            if fragments:
                parsed_entries = sum(counts[paper.record.paper_id].values())
                evidence.append(
                    Evidence(
                        chunk_id=f"references-{paper.record.paper_id}",
                        paper_id=paper.record.paper_id,
                        paper_title=paper.record.title,
                        section="References",
                        section_type="references",
                        page_start=min(item.page_number for item in fragments),
                        page_end=max(item.page_number for item in fragments),
                        text=(
                            f"Parsed {parsed_entries} reference entries with publication "
                            f"years from the References section; counts by year: "
                            f"{counts[paper.record.paper_id]}."
                        ),
                        similarity=1.0,
                    )
                )

        if len(counts) < 2:
            raise FileNotFoundError(
                "Reference comparison requires at least two extracted documents. "
                "Run `paperlens ingest` first."
            )

        target = self.outputs_path / "references"
        target.mkdir(parents=True, exist_ok=True)
        csv_path = target / "references_by_year.csv"
        markdown_path = target / "references_by_year.md"
        chart_path = target / "references_by_year.png"
        self._write_csv(counts, csv_path)
        markdown_path.write_text(self._markdown(counts), encoding="utf-8")
        self._chart(counts, chart_path)
        return ReferenceComparisonArtifact(
            counts=counts,
            csv_path=csv_path,
            markdown_path=markdown_path,
            chart_path=chart_path,
            warning=self.warning,
            evidence=evidence,
        )

    @staticmethod
    def count_reference_years(text: str) -> Counter[int]:
        counts: Counter[int] = Counter()
        entries = [entry.strip() for entry in _REFERENCE_START.split(text) if entry.strip()]
        for entry in entries:
            years = [int(year) for year in _YEAR.findall(entry)]
            if years:
                counts[years[-1]] += 1
        return counts

    @staticmethod
    def _years(counts: dict[str, dict[int, int]]) -> list[int]:
        return sorted({year for paper_counts in counts.values() for year in paper_counts})

    def _write_csv(self, counts: dict[str, dict[int, int]], path: Path) -> None:
        paper_ids = sorted(counts)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Year", *paper_ids])
            for year in self._years(counts):
                writer.writerow([year, *(counts[paper].get(year, 0) for paper in paper_ids)])

    def _markdown(self, counts: dict[str, dict[int, int]]) -> str:
        paper_ids = sorted(counts)
        lines = [
            f"> {self.warning}",
            "",
            "| Year | " + " | ".join(paper_ids) + " |",
            "|---:" + "|---:" * len(paper_ids) + "|",
        ]
        for year in self._years(counts):
            values = " | ".join(str(counts[paper].get(year, 0)) for paper in paper_ids)
            lines.append(f"| {year} | {values} |")
        return "\n".join(lines) + "\n"

    def _chart(self, counts: dict[str, dict[int, int]], path: Path) -> None:
        years = self._years(counts)
        fig, axis = plt.subplots(figsize=(12, 6.75), dpi=150)
        for paper_id in sorted(counts):
            axis.plot(
                years,
                [counts[paper_id].get(year, 0) for year in years],
                marker="o",
                linewidth=2,
                label=paper_id,
            )
        axis.set_title("Reference publication years by paper")
        axis.set_xlabel("Publication year")
        axis.set_ylabel("Number of references")
        axis.grid(alpha=0.2)
        axis.legend()
        fig.text(0.01, 0.01, self.warning, fontsize=8, color="#4B5563")
        fig.tight_layout(rect=(0, 0.05, 1, 1))
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
