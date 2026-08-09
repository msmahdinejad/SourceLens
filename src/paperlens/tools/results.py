from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import matplotlib
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from paperlens.domain import ExtractedPaper

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ResultProfile = Literal["representative_models", "best_single_model_validation"]

COMPARISON_WARNING = (
    "These are as-reported results, not a fully controlled comparison. "
    "Crop protocol, input size, and training recipe differ across papers."
)


class ExperimentalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: ResultProfile
    paper_id: str
    paper_title: str
    model: str
    dataset: str
    split: str
    top1_accuracy: float
    top5_accuracy: float
    top1_reported: float
    top5_reported: float
    reported_as: Literal["accuracy", "error"]
    crop_count: int | None
    crop_size: str
    inference_protocol: str
    single_model: bool
    ensemble: bool
    parameters_million: float | None
    flops_billion: float | None
    source_pdf: str
    source_pages: str
    source_location: str
    reported_by: str
    strictly_comparable: bool

    @model_validator(mode="after")
    def validate_normalization(self) -> ExperimentalResult:
        if self.reported_as == "error":
            expected_top1 = 100.0 - self.top1_reported
            expected_top5 = 100.0 - self.top5_reported
        else:
            expected_top1 = self.top1_reported
            expected_top5 = self.top5_reported
        if abs(expected_top1 - self.top1_accuracy) > 0.011:
            raise ValueError("Top-1 accuracy does not match the reported value")
        if abs(expected_top5 - self.top5_accuracy) > 0.011:
            raise ValueError("Top-5 accuracy does not match the reported value")
        return self

    @property
    def citation(self) -> str:
        return f"{self.source_pdf}, {self.source_location}, PDF pp. {self.source_pages}"


class ResultArtifacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: ResultProfile
    rows: list[ExperimentalResult]
    json_path: Path
    csv_path: Path
    markdown_path: Path
    chart_path: Path | None = None
    warning: str = COMPARISON_WARNING
    provenance_verified: bool = True
    source_sha256: dict[str, str] = Field(default_factory=dict)


class ResultsRepository:
    """Validated source of experimental values with explicit provenance."""

    def __init__(self, verified_results_path: Path) -> None:
        self.verified_results_path = verified_results_path
        self.source_hashes: dict[str, str] = {}
        self._provenance_validated = False

    def load(self, profile: ResultProfile | None = None) -> list[ExperimentalResult]:
        raw = json.loads(self.verified_results_path.read_text(encoding="utf-8"))
        rows = TypeAdapter(list[ExperimentalResult]).validate_python(raw)
        if not self._provenance_validated:
            self.source_hashes = self._validate_pdf_provenance(rows)
            self._provenance_validated = True
        if profile is None:
            return rows
        return [row for row in rows if row.profile == profile]

    def _validate_pdf_provenance(
        self,
        rows: list[ExperimentalResult],
    ) -> dict[str, str]:
        """Bind the audited result cache to current PDF bytes and cited pages."""
        project_root = self.verified_results_path.parent.parent
        hashes: dict[str, str] = {}
        extracted: dict[str, ExtractedPaper] = {}
        for row in rows:
            if row.paper_id not in extracted:
                cache_path = (
                    self.verified_results_path.parent
                    / "cache"
                    / "extracted"
                    / f"{row.paper_id}.json"
                )
                paper = ExtractedPaper.model_validate_json(cache_path.read_text(encoding="utf-8"))
                pdf_path = project_root / "papers" / row.source_pdf
                with pdf_path.open("rb") as handle:
                    current_hash = hashlib.file_digest(handle, "sha256").hexdigest()
                if current_hash != paper.sha256:
                    raise ValueError(
                        f"PDF provenance mismatch for {row.source_pdf}; re-ingest and audit"
                    )
                extracted[row.paper_id] = paper
                hashes[row.paper_id] = current_hash

            pages = _page_numbers(row.source_pages)
            cited_text = "\n".join(
                page.markdown for page in extracted[row.paper_id].pages if page.page_number in pages
            )
            missing_values = [
                value
                for value in (row.top1_reported, row.top5_reported)
                if str(value) not in cited_text
            ]
            if missing_values:
                raise ValueError(
                    f"Audited values {missing_values} were not found on cited PDF pages "
                    f"for {row.model}"
                )
        return hashes


def _page_numbers(value: str) -> set[int]:
    pages: set[int] = set()
    for token in re.findall(r"\d+(?:-\d+)?", value):
        if "-" not in token:
            pages.add(int(token))
            continue
        start, end = (int(part) for part in token.split("-", 1))
        pages.update(range(start, end + 1))
    return pages


def extract_experimental_results(
    repository: ResultsRepository,
    outputs_path: Path,
    profile: ResultProfile = "representative_models",
) -> ResultArtifacts:
    """Validate and export the two common metrics and their source protocols."""
    rows = repository.load(profile)
    if len({row.paper_id for row in rows}) < 2:
        raise ValueError(f"Profile {profile!r} must contain at least two documents")

    target = outputs_path / "results" / profile
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "experimental_results.json"
    csv_path = target / "experimental_results.csv"
    markdown_path = target / "experimental_results.md"

    json_path.write_text(
        json.dumps(
            [row.model_dump(mode="json") for row in rows],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_csv(rows, csv_path)
    markdown_path.write_text(_markdown_table(rows), encoding="utf-8")

    return ResultArtifacts(
        profile=profile,
        rows=rows,
        json_path=json_path,
        csv_path=csv_path,
        markdown_path=markdown_path,
        source_sha256=repository.source_hashes,
    )


def compare_results(
    repository: ResultsRepository,
    outputs_path: Path,
    profile: ResultProfile = "best_single_model_validation",
) -> ResultArtifacts:
    """Export a comparison table and a cited grouped-bar chart."""
    artifacts = extract_experimental_results(repository, outputs_path, profile)
    chart_path = artifacts.markdown_path.with_name("accuracy_comparison.png")

    labels = [row.model for row in artifacts.rows]
    top1 = [row.top1_accuracy for row in artifacts.rows]
    top5 = [row.top5_accuracy for row in artifacts.rows]
    x = list(range(len(labels)))
    width = 0.36

    fig, axis = plt.subplots(figsize=(12, 6.75), dpi=150)
    fig.patch.set_facecolor("#FFFDF8")
    axis.set_facecolor("#FFFDF8")
    bars_top1 = axis.bar(
        [value - width / 2 for value in x],
        top1,
        width,
        label="Top-1 accuracy",
        color="#CF6B4D",
    )
    bars_top5 = axis.bar(
        [value + width / 2 for value in x],
        top5,
        width,
        label="Top-5 accuracy",
        color="#165E58",
    )
    axis.set_ylim(0, 100)
    axis.set_ylabel("Accuracy (%)")
    axis.set_title(f"Reported accuracy - {profile.replace('_', ' ')}")
    axis.set_xticks(x, labels, rotation=10, ha="right")
    axis.grid(axis="y", alpha=0.2)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="lower right")
    axis.bar_label(bars_top1, fmt="%.2f", padding=3, fontsize=9)
    axis.bar_label(bars_top5, fmt="%.2f", padding=3, fontsize=9)
    fig.text(0.01, 0.01, COMPARISON_WARNING, fontsize=8, color="#4B5563")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(chart_path, bbox_inches="tight")
    plt.close(fig)

    return artifacts.model_copy(update={"chart_path": chart_path})


def _write_csv(rows: list[ExperimentalResult], path: Path) -> None:
    fieldnames = list(ExperimentalResult.model_fields)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(row.model_dump(mode="json") for row in rows)


def _markdown_table(rows: list[ExperimentalResult]) -> str:
    lines = [
        f"> {COMPARISON_WARNING}",
        "",
        "| Paper | Model | Top-1 Acc. | Top-5 Acc. | Protocol | Source |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.paper_id} | {row.model} | {row.top1_accuracy:.2f}% | "
            f"{row.top5_accuracy:.2f}% | {row.inference_protocol} | {row.citation} |"
        )
    return "\n".join(lines) + "\n"
