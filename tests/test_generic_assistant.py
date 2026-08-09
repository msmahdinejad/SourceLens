from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from paperlens.assistant import GENERAL_FALLBACK_PREFIX, ResearchAssistant
from paperlens.domain import Evidence
from paperlens.language_models.base import ExecutedToolCall, GenerationResult
from paperlens.tools.documents import DocumentTools
from paperlens.tools.registry import ResearchTools


class EmptyCorpus:
    def search(self, *args, **kwargs):
        return []

    def search_balanced(self, *args, **kwargs):
        return []


class NoImplicitSearchCorpus:
    def search(self, *args, **kwargs):
        raise AssertionError("Generic chat must let the agent choose retrieval")

    def search_balanced(self, *args, **kwargs):
        raise AssertionError("Generic chat must let the agent choose retrieval")


class SearchTools:
    @staticmethod
    def available_specs():
        return [spec for spec in ResearchTools.document_specs() if spec.name == "search_documents"]

    @staticmethod
    def execute(name, arguments):
        assert name == "search_documents"
        evidence = Evidence(
            chunk_id="uploaded-1",
            paper_id="doc-1",
            paper_title="Uploaded document",
            section="Introduction",
            section_type="introduction",
            page_start=2,
            page_end=2,
            text="The uploaded document describes a hybrid retrieval architecture.",
            similarity=0.92,
            workspace_id="ws-1",
            document_id="doc-1",
            filename="uploaded.pdf",
        )
        return json.dumps(
            {"evidence": [evidence.model_dump(mode="json")]},
            ensure_ascii=False,
        )


class ToolThenSynthesisModel:
    def __init__(self) -> None:
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            arguments = {
                "query": "hybrid retrieval architecture",
                "filters": {
                    "document_ids": None,
                    "section_types": None,
                    "chunk_types": None,
                    "page_start": None,
                    "page_end": None,
                    "top_k": 8,
                },
            }
            output = kwargs["execute_tool"]("search_documents", arguments)
            return GenerationResult(
                text="",
                tool_calls=[
                    ExecutedToolCall(
                        name="search_documents",
                        arguments=arguments,
                        output=output,
                    )
                ],
            )
        return GenerationResult(
            text=(
                "The uploaded document presents hybrid retrieval as an architecture "
                "that combines complementary retrieval signals and keeps the result "
                "grounded in document evidence on the cited physical page [E1]."
            )
        )


def test_llm_can_choose_document_search_when_initial_retrieval_is_empty() -> None:
    model = ToolThenSynthesisModel()
    assistant = ResearchAssistant(
        corpus=EmptyCorpus(),
        language_model=model,
        tools=SearchTools(),
        document_ids=["doc-1"],
        demo_mode=False,
    )

    answer = assistant.ask("What retrieval architecture does the document use?")

    assert answer.mode == "rag"
    assert answer.execution_path == "agent_tool"
    assert answer.evidence[0].page_start == 2
    assert answer.tool_calls[0].name == "search_documents"
    assert "[E1]" in answer.text
    assert model.calls[0]["tool_choice"] == "auto"
    assert model.calls[0]["return_after_tool"] is False
    assert "uploaded-1" not in model.calls[1]["user_input"]
    assert model.calls[1]["user_input"].count(
        "The uploaded document describes a hybrid retrieval architecture."
    ) == 1


def test_persian_search_discards_unvalidated_planner_prose_and_repairs_synthesis() -> None:
    contaminated = (
        "فروشگاه‌ها سو퍼센ترهای مناسبی هستند و م_LOCATION شارژ را ساده می‌کند [E1]."
    )
    clean = (
        "فروشگاه‌ها به دلیل دسترسی محلی و زمان توقف خرید، محل مناسبی برای شارژ "
        "خودروهای برقی هستند. راننده می‌تواند هنگام خرید، خودرو را شارژ کند و "
        "بدون انتظار اضافه به سفر ادامه دهد [E1]."
    )

    class ContaminatedPlannerModel(ToolThenSynthesisModel):
        def run(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("tools"):
                arguments = {
                    "query": "شارژ سریع خودرو در فروشگاه",
                    "filters": {
                        "document_ids": None,
                        "section_types": None,
                        "chunk_types": None,
                        "page_start": None,
                        "page_end": None,
                        "top_k": 8,
                    },
                }
                output = kwargs["execute_tool"]("search_documents", arguments)
                return GenerationResult(
                    text=contaminated,
                    tool_calls=[
                        ExecutedToolCall(
                            name="search_documents",
                            arguments=arguments,
                            output=output,
                        )
                    ],
                )
            if len(self.calls) == 2:
                return GenerationResult(text=contaminated)
            return GenerationResult(text=clean)

    model = ContaminatedPlannerModel()
    assistant = ResearchAssistant(
        corpus=EmptyCorpus(),
        language_model=model,
        tools=SearchTools(),
        document_ids=["doc-1"],
        demo_mode=False,
    )

    answer = assistant.ask(
        "چرا فروشگاه‌ها برای شارژ سریع خودروهای برقی مناسب هستند؟"
    )

    assert answer.text == clean
    assert "سو퍼센تر" not in answer.text
    assert "م_LOCATION" not in answer.text
    assert "تحلیل مدل زبانی" not in answer.text
    assert len(model.calls) == 3


def test_generic_chat_uses_the_agent_selected_evidence_budget() -> None:
    class BroadQuestionModel(ToolThenSynthesisModel):
        def run(self, **kwargs):
            if kwargs.get("tools"):
                arguments = {
                    "query": "purpose methods findings limitations future work",
                    "filters": {
                        "document_ids": None,
                        "section_types": None,
                        "chunk_types": None,
                        "page_start": None,
                        "page_end": None,
                        "top_k": 17,
                    },
                }
                output = kwargs["execute_tool"]("search_documents", arguments)
                return GenerationResult(
                    text="",
                    tool_calls=[
                        ExecutedToolCall(
                            name="search_documents",
                            arguments=arguments,
                            output=output,
                        )
                    ],
                )
            return super().run(**kwargs)

    assistant = ResearchAssistant(
        corpus=NoImplicitSearchCorpus(),
        language_model=BroadQuestionModel(),
        tools=SearchTools(),
        document_ids=["doc-1"],
        demo_mode=False,
    )

    answer = assistant.ask("Give me a complete analysis of this document.")

    assert answer.tool_calls[0].arguments["filters"]["top_k"] == 17


def test_required_missing_information_prefix_mentions_documents() -> None:
    assert GENERAL_FALLBACK_PREFIX == (
        "پاسخ شما در مقالات/اسناد انتخاب‌شده یافت نشد. اما با استفاده از مدل زبانی، "
        "پاسخ عمومی به شرح زیر است:"
    )


class ScopedDocumentContext:
    def __init__(self) -> None:
        self.search_document_ids = None
        self.search_top_k = None
        self.balanced_document_ids = None
        self.queries = []
        self.store = SimpleNamespace(
            get=lambda _workspace_id: SimpleNamespace(
                name="Workspace",
                documents=[
                    SimpleNamespace(document_id="doc-1"),
                    SimpleNamespace(document_id="doc-2"),
                ],
            )
        )

    def search(self, _workspace_id, _query, **kwargs):
        self.queries.append(_query)
        self.search_document_ids = kwargs["document_ids"]
        self.search_top_k = kwargs["top_k"]
        return []

    def search_balanced(self, _workspace_id, _query, document_ids, **_kwargs):
        self.balanced_document_ids = document_ids
        return []


def test_document_tools_cannot_escape_selected_document_scope() -> None:
    context = ScopedDocumentContext()
    tools = DocumentTools(context, "ws-1", allowed_document_ids={"doc-1"})

    tools.search_documents("query", filters=None)
    tools.compare_documents(["doc-1", "doc-2"], aspects=None)

    assert context.search_document_ids == {"doc-1"}
    assert context.balanced_document_ids == ["doc-1"]
    with pytest.raises(ValueError, match="selected document scope"):
        tools.summarize_document("doc-2")


def test_document_search_derives_a_budget_when_the_agent_does_not_supply_one() -> None:
    context = ScopedDocumentContext()
    tools = DocumentTools(context, "ws-1", allowed_document_ids={"doc-1"})

    tools.search_documents("reported accuracy", filters=None)
    narrow_budget = context.search_top_k
    tools.search_documents(
        "compare purpose methods results limitations implications and future work",
        filters=None,
    )

    assert narrow_budget is not None
    assert context.search_top_k > narrow_budget


def test_document_summary_collects_coverage_queries_not_the_vague_user_phrase() -> None:
    context = ScopedDocumentContext()
    tools = DocumentTools(context, "ws-1", allowed_document_ids={"doc-1"})

    tools.summarize_document("doc-1")

    assert len(context.queries) >= 4
    assert any("purpose" in query for query in context.queries)
    assert any("methods" in query for query in context.queries)
    assert any("results" in query for query in context.queries)
    assert any("limitations" in query for query in context.queries)


def test_agent_prompt_teaches_models_to_rewrite_broad_document_questions() -> None:
    assistant = ResearchAssistant(
        corpus=EmptyCorpus(),
        language_model=ToolThenSynthesisModel(),
        tools=SearchTools(),
        document_ids=["doc-1"],
        demo_mode=False,
    )

    instructions = assistant._agent_instructions()

    assert "vague question verbatim" in instructions
    assert "multiple focused searches" in instructions
