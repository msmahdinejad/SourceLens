from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from paperlens.language_models.base import LanguageModel, ToolArgumentError, ToolSpec
from paperlens.tools.analysis_reports import AnalysisReportGenerator
from paperlens.tools.documents import DocumentContext, DocumentTools
from paperlens.tools.references import ReferenceAnalyzer
from paperlens.tools.results import (
    ResultProfile,
    ResultsRepository,
    compare_results,
    extract_experimental_results,
)
from paperlens.tools.survey import SurveyGenerator

if TYPE_CHECKING:
    from paperlens.tools.survey import SearchableCorpus


_PROFILE_SCHEMA = {
    "type": ["string", "null"],
    "enum": ["representative_models", "best_single_model_validation", None],
    "description": "Comparison profile; null selects the function default.",
}
_LANGUAGE_SCHEMA = {
    "type": ["string", "null"],
    "enum": ["fa", "en", None],
    "description": "Output language; null defaults to Persian.",
}


class ResearchTools:
    """Validated dispatcher for functions that the language model may call."""

    def __init__(
        self,
        *,
        corpus: SearchableCorpus,
        language_model: LanguageModel,
        verified_results_path: Path,
        outputs_path: Path,
        document_context: DocumentContext | None = None,
        workspace_id: str = "default",
        selected_document_ids: set[str] | None = None,
    ) -> None:
        self.repository = ResultsRepository(verified_results_path)
        self.outputs_path = outputs_path
        report_document_ids = tuple(sorted(selected_document_ids or ()))
        if not report_document_ids:
            report_document_ids = (
                _verified_document_ids(verified_results_path)
                if verified_results_path.is_file()
                else ("workspace-document",)
            )
        self.survey_generator = SurveyGenerator(
            corpus, language_model, outputs_path, report_document_ids
        )
        self.analysis_generator = AnalysisReportGenerator(
            corpus,
            language_model,
            outputs_path,
            report_document_ids,
        )
        self.reference_analyzer = ReferenceAnalyzer(
            verified_results_path.parent / "cache" / "extracted",
            outputs_path,
        )
        self.workspace_id = workspace_id
        self.document_tools = (
            DocumentTools(
                document_context,
                workspace_id,
                allowed_document_ids=selected_document_ids,
            )
            if document_context
            else None
        )

    @staticmethod
    def specs() -> list[ToolSpec]:
        return [
            ToolSpec(
                name="extract_experimental_results",
                description=(
                    "ONLY extract and export raw cited Top-1 and Top-5 benchmark values "
                    "for all papers. Use when the user asks to list or extract metrics "
                    "without comparing, ranking, choosing the best, or drawing a chart. "
                    "Never use this function for a comparison or chart request."
                ),
                parameters=_single_nullable_parameter("profile", _PROFILE_SCHEMA),
            ),
            ToolSpec(
                name="compare_results",
                description=(
                    "Compare the selected papers, rank them, export a table, and generate a "
                    "performance chart. ALWAYS use this function for comparison, best-"
                    "paper, ranking, or chart requests instead of extracting raw values."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="generate_survey",
                description=(
                    "Generate and save a cited mini-survey covering the problem, each "
                    "paper's method, comparison, progress trend, and conclusion."
                ),
                parameters=_single_nullable_parameter("language", _LANGUAGE_SCHEMA),
            ),
            ToolSpec(
                name="find_research_gap",
                description=(
                    "Analyze conclusions, limitations, and future work to extract "
                    "research gaps, unresolved problems, and future directions."
                ),
                parameters=_single_nullable_parameter("language", _LANGUAGE_SCHEMA),
            ),
            ToolSpec(
                name="compare_references",
                description=(
                    "Count references by publication year for each paper and export "
                    "a comparison table and chart."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="compare_limitations",
                description=(
                    "Compare the limitations and weaknesses of the selected methods with "
                    "citations and an explicit uncertainty assessment."
                ),
                parameters=_single_nullable_parameter("language", _LANGUAGE_SCHEMA),
            ),
        ]

    @staticmethod
    def document_specs() -> list[ToolSpec]:
        nullable_filters = {
            "type": ["object", "null"],
            "properties": {
                "document_ids": {"type": ["array", "null"], "items": {"type": "string"}},
                "section_types": {"type": ["array", "null"], "items": {"type": "string"}},
                "chunk_types": {"type": ["array", "null"], "items": {"type": "string"}},
                "page_start": {"type": ["integer", "null"], "minimum": 1},
                "page_end": {"type": ["integer", "null"], "minimum": 1},
                "top_k": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 30,
                    "description": (
                        "Evidence candidate budget chosen for this information need; use a "
                        "small value for a narrow fact and a larger value for broad synthesis."
                    ),
                },
            },
            "required": [
                "document_ids",
                "section_types",
                "chunk_types",
                "page_start",
                "page_end",
                "top_k",
            ],
            "additionalProperties": False,
        }
        optional_document = {
            "type": ["string", "null"],
            "description": "Document id, or null for every document.",
        }
        return [
            ToolSpec(
                name="search_documents",
                description=(
                    "Search the active PDF workspace with hybrid vector and lexical "
                    "retrieval. Use filters only when the user's requested scope is explicit."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "filters": nullable_filters,
                    },
                    "required": ["query", "filters"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="summarize_document",
                description=(
                    "Collect broad, section-balanced evidence for an overview of one document. "
                    "Prefer this for vague requests such as what a document is about instead of "
                    "embedding the vague wording as a single search query."
                ),
                parameters={
                    "type": "object",
                    "properties": {"document_id": {"type": "string", "minLength": 1}},
                    "required": ["document_id"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="compare_documents",
                description=(
                    "Collect balanced evidence for comparing two or more workspace documents."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "document_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                        },
                        "aspects": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["document_ids", "aspects"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="extract_tables",
                description="Return VLM-extracted tables with document and physical page.",
                parameters=_single_nullable_parameter("document_id", optional_document),
            ),
            ToolSpec(
                name="extract_figures",
                description="Return VLM-extracted figures with document and physical page.",
                parameters=_single_nullable_parameter("document_id", optional_document),
            ),
            ToolSpec(
                name="inspect_workspace",
                description=(
                    "Inspect active documents, processing states, extraction, and vector build."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="rebuild_knowledge_base",
                description=(
                    "Re-extract and rebuild the active workspace vector knowledge base. "
                    "Use only when the user explicitly requests a rebuild."
                ),
                parameters={
                    "type": "object",
                    "properties": {"workspace_id": {"type": "string", "minLength": 1}},
                    "required": ["workspace_id"],
                    "additionalProperties": False,
                },
            ),
        ]

    def available_specs(self) -> list[ToolSpec]:
        if self.document_tools is None:
            return self.specs()
        generic = self.document_specs()
        reusable = [
            item
            for item in self.specs()
            if item.name in {"generate_survey", "find_research_gap", "compare_limitations"}
        ]
        return [*reusable, *generic]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        self.validate_available_arguments(name, arguments)
        if self.document_tools is not None:
            if name == "search_documents":
                return self.document_tools.search_documents(
                    arguments["query"], arguments["filters"]
                )
            if name == "summarize_document":
                return self.document_tools.summarize_document(arguments["document_id"])
            if name == "compare_documents":
                return self.document_tools.compare_documents(
                    arguments["document_ids"], arguments["aspects"]
                )
            if name == "extract_tables":
                return self.document_tools.extract_visuals("tables", arguments["document_id"])
            if name == "extract_figures":
                return self.document_tools.extract_visuals("figures", arguments["document_id"])
            if name == "inspect_workspace":
                return self.document_tools.inspect_workspace()
            if name == "rebuild_knowledge_base":
                return self.document_tools.rebuild(arguments["workspace_id"])
            if name == "generate_survey":
                return self.document_tools.survey_evidence(arguments.get("language") or "fa")
            if name in {
                "find_research_gap",
                "compare_limitations",
            }:
                kind = "research_gap" if name == "find_research_gap" else "limitations"
                return self.document_tools.research_evidence(
                    kind, arguments.get("language") or "fa"
                )
        if name == "extract_experimental_results":
            profile = self._profile(arguments.get("profile"), "representative_models")
            artifact = extract_experimental_results(
                self.repository,
                self.outputs_path,
                profile,
            )
            return self._artifact_json(artifact)
        if name == "compare_results":
            profile: ResultProfile = "best_single_model_validation"
            artifact = compare_results(self.repository, self.outputs_path, profile)
            return self._artifact_json(artifact)
        if name == "generate_survey":
            language = arguments.get("language") or "fa"
            self._validate_language(language)
            artifact = self.survey_generator.generate(language)
            return json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)
        if name == "find_research_gap":
            language = arguments.get("language") or "fa"
            self._validate_language(language)
            artifact = self.analysis_generator.generate("research_gap", language)
            return json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)
        if name == "compare_references":
            artifact = self.reference_analyzer.compare()
            return json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)
        if name == "compare_limitations":
            language = arguments.get("language") or "fa"
            self._validate_language(language)
            artifact = self.analysis_generator.generate("limitations", language)
            return json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)
        raise ValueError(f"Unknown tool: {name}")

    @classmethod
    def validate_arguments(cls, name: str, arguments: dict[str, Any]) -> None:
        """Enforce the same closed schema even when a provider ignores strict mode."""
        if not isinstance(arguments, dict):
            raise ToolArgumentError("Tool arguments must be a JSON object")
        spec = next((item for item in cls.specs() if item.name == name), None)
        if spec is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        schema = spec.parameters
        properties = schema.get("properties", {})
        unexpected = set(arguments) - set(properties)
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ToolArgumentError(f"Unexpected arguments for {name}: {names}")
        missing = set(schema.get("required", [])) - set(arguments)
        if missing:
            names = ", ".join(sorted(missing))
            raise ToolArgumentError(f"Missing required arguments for {name}: {names}")
        for argument_name, value in arguments.items():
            argument_schema = properties[argument_name]
            allowed = argument_schema.get("enum")
            if allowed is not None and value not in allowed:
                raise ToolArgumentError(f"Unsupported value for {name}.{argument_name}: {value!r}")

    def validate_available_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        self._validate_against_specs(name, arguments, self.available_specs())

    @staticmethod
    def _validate_against_specs(
        name: str,
        arguments: dict[str, Any],
        specs: list[ToolSpec],
    ) -> None:
        if not isinstance(arguments, dict):
            raise ToolArgumentError("Tool arguments must be a JSON object")
        spec = next((item for item in specs if item.name == name), None)
        if spec is None:
            raise ToolArgumentError(f"Unknown tool: {name}")
        properties = spec.parameters.get("properties", {})
        unexpected = set(arguments) - set(properties)
        if unexpected:
            raise ToolArgumentError(
                f"Unexpected arguments for {name}: {', '.join(sorted(unexpected))}"
            )
        missing = set(spec.parameters.get("required", [])) - set(arguments)
        if missing:
            raise ToolArgumentError(
                f"Missing required arguments for {name}: {', '.join(sorted(missing))}"
            )
        for argument_name, value in arguments.items():
            schema = properties[argument_name]
            allowed = schema.get("enum")
            if allowed is not None and value not in allowed:
                raise ToolArgumentError(f"Unsupported value for {name}.{argument_name}: {value!r}")
            if schema.get("minItems") and (
                not isinstance(value, list) or len(value) < schema["minItems"]
            ):
                raise ToolArgumentError(
                    f"{name}.{argument_name} requires at least {schema['minItems']} items"
                )

    @staticmethod
    def _profile(value: Any, default: ResultProfile) -> ResultProfile:
        profile = value or default
        if profile not in {"representative_models", "best_single_model_validation"}:
            raise ValueError(f"Unsupported result profile: {profile}")
        return profile

    @staticmethod
    def _artifact_json(artifact) -> str:
        return json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False)

    @staticmethod
    def _validate_language(language: str) -> None:
        if language not in {"fa", "en"}:
            raise ValueError(f"Unsupported output language: {language}")


def _single_nullable_parameter(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: schema},
        "required": [name],
        "additionalProperties": False,
    }


def _verified_document_ids(path: Path) -> tuple[str, ...]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    document_ids = tuple(
        dict.fromkeys(
            str(row["paper_id"])
            for row in rows
            if isinstance(row, dict) and row.get("paper_id")
        )
    )
    if not document_ids:
        raise ValueError("Verified results contain no document ids")
    return document_ids
