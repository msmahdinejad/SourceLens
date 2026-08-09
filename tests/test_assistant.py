import json
import re
from typing import Any

import pytest

from paperlens.assistant import (
    GENERAL_FALLBACK_PREFIX,
    ResearchAssistant,
    mentioned_document_ids,
)
from paperlens.domain import Evidence
from paperlens.language_models.base import (
    ExecutedToolCall,
    GenerationResult,
)
from paperlens.language_models.failover import ModelFallbackChain
from paperlens.tools.registry import ResearchTools


def evidence(similarity: float = 0.8) -> Evidence:
    return Evidence(
        chunk_id="c1",
        paper_id="homecharge",
        paper_title="HomeCharge",
        section="Experiments",
        section_type="experiments",
        page_start=6,
        page_end=6,
        text="HomeCharge-152 reports a top-5 validation error of 4.49%.",
        similarity=similarity,
    )


class FakeCorpus:
    def __init__(self, items: list[Evidence]) -> None:
        self.items = items
        self.last_kwargs: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.balanced_requests: list[dict[str, Any]] = []

    def search(self, *args: Any, **kwargs: Any) -> list[Evidence]:
        self.last_kwargs = kwargs
        self.requests.append(kwargs)
        paper_ids = kwargs.get("paper_ids")
        return [item for item in self.items if not paper_ids or item.paper_id in paper_ids]

    def search_balanced(
        self,
        query: str,
        paper_ids: tuple[str, ...],
        *,
        top_k: int,
        per_paper: int,
        section_types: tuple[str, ...] | None = None,
    ) -> list[Evidence]:
        self.balanced_requests.append(
            {
                "query": query,
                "paper_ids": paper_ids,
                "top_k": top_k,
                "per_paper": per_paper,
                "section_types": section_types,
            }
        )
        buckets = [
            [item for item in self.items if item.paper_id == paper_id][:per_paper]
            for paper_id in paper_ids
        ]
        balanced: list[Evidence] = []
        for offset in range(per_paper):
            for bucket in buckets:
                if offset < len(bucket):
                    balanced.append(bucket[offset])
                    if len(balanced) == top_k:
                        return balanced
        return balanced


class ScriptedModel:
    def __init__(self, results: list[GenerationResult]) -> None:
        self.results = results
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> GenerationResult:
        self.requests.append(kwargs)
        result = self.results[self.calls]
        self.calls += 1
        return result


class FakeTools:
    @staticmethod
    def specs() -> list:
        return ResearchTools.specs()

    @staticmethod
    def execute(name: str, arguments: dict[str, Any]) -> str:
        return "{}"


def assistant(
    items: list[Evidence],
    model: ScriptedModel,
    tools: Any | None = None,
) -> ResearchAssistant:
    return ResearchAssistant(
        corpus=FakeCorpus(items),
        language_model=model,
        tools=tools or FakeTools(),
        minimum_similarity=0.55,
        document_ids=("homecharge", "retailcharge", "smartcharge", "solarhub"),
    )


def test_grounded_answer_requires_valid_evidence_citation() -> None:
    model = ScriptedModel([GenerationResult(text="The result is 4.49% [E1].")])

    answer = assistant([evidence()], model).ask("What was the top-5 error?")

    assert answer.mode == "rag"
    assert answer.evidence[0].chunk_id == "c1"
    assert model.calls == 1
    assert model.requests[0]["tool_choice"] == "auto"
    assert {spec.name for spec in model.requests[0]["tools"]} == {
        "extract_experimental_results",
        "compare_results",
        "generate_survey",
        "find_research_gap",
        "compare_references",
        "compare_limitations",
    }


def test_follow_up_question_includes_bounded_conversation_context() -> None:
    model = ScriptedModel([GenerationResult(text="Because of residual learning [E1].")])

    answer = assistant([evidence()], model).ask(
        "Why?",
        history=[("user", "What is the main idea of HomeCharge?")],
    )

    assert answer.mode == "rag"
    assert "What is the main idea of HomeCharge?" in model.requests[0]["user_input"]
    assert "Current question:\nWhy?" in model.requests[0]["user_input"]


def test_unrelated_new_question_does_not_inherit_paper_scope_from_history() -> None:
    model = ScriptedModel(
        [
            GenerationResult(text="INSUFFICIENT_EVIDENCE"),
            GenerationResult(text="Paris is the capital of France."),
        ]
    )

    answer = assistant([evidence(0.2)], model).ask(
        "What is the capital of France?",
        history=[("user", "Explain the main idea of HomeCharge.")],
    )

    assert answer.mode == "general_fallback"
    assert answer.text.startswith(GENERAL_FALLBACK_PREFIX)


def test_missing_citation_is_repaired_once() -> None:
    model = ScriptedModel(
        [
            GenerationResult(text="The result is 4.49%."),
            GenerationResult(text="The result is 4.49% [E1]."),
        ]
    )

    answer = assistant([evidence()], model).ask("What was the top-5 error?")

    assert answer.mode == "rag"
    assert "[E1]" in answer.text
    assert model.calls == 2


def test_low_similarity_uses_required_general_fallback() -> None:
    model = ScriptedModel(
        [
            GenerationResult(text="NO_TOOL"),
            GenerationResult(text="Paris is the capital of France."),
        ]
    )

    answer = assistant([evidence(0.2)], model).ask("What is the capital of France?")

    assert answer.mode == "general_fallback"
    assert answer.text.startswith(GENERAL_FALLBACK_PREFIX)
    assert not answer.evidence


def test_general_fallback_does_not_claim_selected_papers_were_not_provided() -> None:
    missing_upload_claim = ScriptedModel(
        [GenerationResult(text="شما هیچ مقاله‌ای ضمیمه نکرده‌اید؛ لطفاً فایل مقالات را ارسال کنید.")]
    )
    useful_general_answer = ScriptedModel(
        [
            GenerationResult(
                text=(
                    "به‌طور عمومی، این خانوادهٔ روش‌ها در نوع بلوک معماری و شیوهٔ "
                    "افزایش ظرفیت شبکه با یکدیگر تفاوت دارند."
                )
            )
        ]
    )
    model = ModelFallbackChain([("bad", missing_upload_claim), ("good", useful_general_answer)])

    answer = assistant([], model).ask("موضوع مقالات رو با هم مقایسه کن")

    assert answer.mode == "general_fallback"
    assert "ضمیمه نکرده" not in answer.text
    assert "ارسال کنید" not in answer.text
    assert "نوع بلوک معماری" in answer.text


def test_prompt_image_description_can_report_a_visible_empty_source_state() -> None:
    visual_description = ScriptedModel(
        [
            GenerationResult(
                text=(
                    "The screenshot shows no papers uploaded in the Sources panel, "
                    "with an upload control beside the empty state."
                )
            )
        ]
    )
    unnecessary_fallback = ScriptedModel(
        [GenerationResult(text="A generic description from a weaker model.")]
    )
    model = ModelFallbackChain([("visual", visual_description), ("fallback", unnecessary_fallback)])
    question = (
        "[Prompt image evidence]\n\nRecognized text:\nمنابع پژوهش\n\n"
        "Current question:\nDescribe the interface."
    )

    answer = assistant([], model).ask(question)

    assert answer.mode == "general_fallback"
    assert visual_description.calls == 1
    assert unnecessary_fallback.calls == 0
    assert "Sources panel" in answer.text


def test_tool_request_is_routed_before_low_similarity_fallback() -> None:
    model = ScriptedModel(
        [
            GenerationResult(
                text="The survey was generated.",
                tool_calls=[
                    ExecutedToolCall(
                        name="generate_survey",
                        arguments={"language": "en"},
                        output='{"markdown_path": "outputs/survey.md"}',
                    )
                ],
            ),
            GenerationResult(
                text=(
                    "The generated survey synthesizes the problem, all four methods, "
                    "their progress, limitations, and the final research conclusion."
                )
            ),
        ]
    )

    answer = assistant([evidence(0.2)], model).ask("Create a comprehensive research overview.")

    assert answer.mode == "rag"
    assert answer.tool_calls[0].name == "generate_survey"
    assert model.calls == 2


def test_explicit_paper_name_bypasses_only_the_similarity_floor() -> None:
    model = ScriptedModel([GenerationResult(text="A grounded answer [E1].")])

    answer = assistant([evidence(0.2)], model).ask("What does HomeCharge report?")

    assert answer.mode == "rag"
    assert model.calls == 1


def test_tool_answer_does_not_require_evidence_label_repair() -> None:
    model = ScriptedModel(
        [
            GenerationResult(
                text="The comparison chart was generated.",
                tool_calls=[
                    ExecutedToolCall(
                        name="compare_results",
                        arguments={"profile": None},
                        output='{"chart_path": "chart.png"}',
                    )
                ],
            ),
            GenerationResult(
                text=(
                    "The comparison covers every returned model and analyzes the "
                    "reported performance while preserving the protocol caveat. It "
                    "also explains the ranking and the numerical differences clearly, "
                    "without changing any authoritative values returned by the tool."
                )
            ),
        ]
    )

    answer = assistant([evidence()], model).ask(
        "Compare the performance results of every model quantitatively."
    )

    assert answer.mode == "rag"
    assert answer.tool_calls[0].name == "compare_results"
    assert model.calls == 2


def test_explicit_paper_names_are_detected_without_substring_collisions() -> None:
    document_ids = ("homecharge", "retailcharge", "smartcharge", "solarhub")
    assert mentioned_document_ids("Compare HomeCharge with RetailCharge", document_ids) == {
        "homecharge",
        "retailcharge",
    }
    assert mentioned_document_ids("بلوک smartcharge چیست؟", document_ids) == {"smartcharge"}


class ComparisonTools(FakeTools):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        rows = [
            ("HomeCharge", "HomeCharge-152", 80.62, 95.51, "homecharge.pdf"),
            ("RetailCharge", "RetailCharge-101", 80.90, 95.60, "retailcharge.pdf"),
            ("SmartCharge", "SmartCharge-154", 83.12, 96.42, "smartcharge.pdf"),
            ("SolarHub", "SolarHub-B7", 84.40, 97.10, "solarhub.pdf"),
        ]
        return json.dumps(
            {
                "rows": [
                    {
                        "paper_title": paper,
                        "model": model,
                        "top1_accuracy": top1,
                        "top5_accuracy": top5,
                        "inference_protocol": "reported protocol",
                        "source_pdf": source,
                        "source_pages": "6",
                    }
                    for paper, model, top1, top5, source in rows
                ],
                "warning": "Protocols differ.",
            }
        )


def test_comparison_is_tool_selected_then_analyzed_by_the_language_model() -> None:
    tools = ComparisonTools()

    class ToolSelectingModel(ScriptedModel):
        def run(self, **kwargs: Any) -> GenerationResult:
            self.requests.append(kwargs)
            self.calls += 1
            if self.calls == 1:
                output = kwargs["execute_tool"]("compare_results", {})
                return GenerationResult(
                    text="",
                    tool_calls=[
                        ExecutedToolCall(
                            name="compare_results",
                            arguments={},
                            output=output,
                        )
                    ],
                )
            return GenerationResult(
                text=(
                    "تحلیل مدل: مقادیر Top-1 برای HomeCharge-152 برابر 80.62، برای "
                    "RetailCharge-101 برابر 80.90، برای SmartCharge-154 برابر 83.12 و برای "
                    "SolarHub-B7 برابر 84.40 درصد است. SolarHub-B7 به‌ترتیب "
                    "1.28، 3.50 و 3.78 واحد درصد جلوتر است؛ بااین‌حال تفاوت "
                    "پروتکل‌ها مانع مقایسه کاملاً کنترل‌شده است."
                )
            )

    model = ToolSelectingModel([])

    answer = assistant([evidence()], model, tools).ask(
        "نتایج 4 مقاله را کامل با هم مقایسه کن و مقایسه‌ات را تحلیل کن"
    )

    assert tools.calls == [("compare_results", {})]
    assert [call.name for call in answer.tool_calls] == ["compare_results"]
    assert "تحلیل مدل:" in answer.text
    assert "1.28" in answer.text
    assert model.calls == 2


def test_multi_paper_rag_balances_evidence_across_all_four_papers() -> None:
    items = [
        evidence().model_copy(
            update={"chunk_id": "r1", "paper_id": "homecharge", "similarity": 0.60}
        ),
        evidence().model_copy(
            update={"chunk_id": "r2", "paper_id": "homecharge", "similarity": 0.59}
        ),
        evidence().model_copy(update={"chunk_id": "x1", "paper_id": "retailcharge"}),
        evidence().model_copy(update={"chunk_id": "s1", "paper_id": "smartcharge"}),
        evidence().model_copy(update={"chunk_id": "e1", "paper_id": "solarhub"}),
        evidence().model_copy(update={"chunk_id": "x2", "paper_id": "retailcharge"}),
    ]
    corpus = FakeCorpus(items)
    model = ScriptedModel(
        [
            GenerationResult(
                text=(
                    "HomeCharge با اتصال باقیمانده [E1]، RetailCharge با تبدیل‌های تجمیعی "
                    "[E2]، SmartCharge با توجه کانالی [E3] و SolarHub با مقیاس‌بندی "
                    "مرکب [E4] از نظر معماری تفاوت دارند."
                )
            )
        ]
    )
    research_assistant = ResearchAssistant(
        corpus=corpus,
        language_model=model,
        tools=FakeTools(),
        minimum_similarity=0.66,
        top_k=6,
        document_ids=("homecharge", "retailcharge", "smartcharge", "solarhub"),
    )

    answer = research_assistant.ask("تفاوت معماری روش‌های چهار مقاله چیست؟")

    assert corpus.requests == []
    assert len(corpus.balanced_requests) == 1
    request = corpus.balanced_requests[0]
    assert request["per_paper"] == 2
    assert set(request["paper_ids"]) == {
        "homecharge",
        "retailcharge",
        "smartcharge",
        "solarhub",
    }
    assert {item.paper_id for item in answer.evidence} == {
        "homecharge",
        "retailcharge",
        "smartcharge",
        "solarhub",
    }
    assert {spec.name for spec in model.requests[0]["tools"]} == {
        "extract_experimental_results",
        "compare_results",
        "generate_survey",
        "find_research_gap",
        "compare_references",
        "compare_limitations",
    }


def test_multi_aspect_comparison_retrieves_three_chunks_per_paper() -> None:
    items = [
        evidence().model_copy(
            update={
                "chunk_id": f"{paper}-{offset}",
                "paper_id": paper,
                "paper_title": paper,
                "text": f"Evidence {offset} about {paper} architecture and method.",
            }
        )
        for paper in ("homecharge", "retailcharge", "smartcharge", "solarhub")
        for offset in range(3)
    ]
    corpus = FakeCorpus(items)
    model = ScriptedModel(
        [
            GenerationResult(
                text=(
                    "HomeCharge از ایدهٔ اتصال میان‌بُر و تغییر معماری بلوک پسماند بهره "
                    "می‌گیرد؛ مزیت آن آموزش عمیق‌تر است و محدودیت آن باید با عدم "
                    "قطعیت تفسیر شود [E1]. RetailCharge ایدهٔ cardinality را به معماری "
                    "اضافه می‌کند؛ مزیت آن ظرفیت بیشتر و محدودیت آن هزینه است [E2]. "
                    "SmartCharge معماری را با توجه کانالی تغییر می‌دهد؛ مزیت آن بازوزن‌دهی "
                    "ویژگی و محدودیت صریح آن در شواهد کامل نیست [E3]. SolarHub "
                    "ایدهٔ مقیاس‌بندی مرکب را برای عمق، عرض و وضوح وارد معماری می‌کند؛ "
                    "مزیت آن توازن بهتر و محدودیت آن وابستگی ضرایب است [E4]. مسیر "
                    "تکامل از عمق، به cardinality، توجه کانالی و سپس مقیاس‌بندی مرکب "
                    "می‌رسد [E1][E2][E3][E4]."
                )
            )
        ]
    )
    research_assistant = ResearchAssistant(
        corpus=corpus,
        language_model=model,
        tools=FakeTools(),
        minimum_similarity=0.66,
        top_k=6,
        document_ids=("homecharge", "retailcharge", "smartcharge", "solarhub"),
    )

    answer = research_assistant.ask(
        "چهار مقاله را از نظر ایده، معماری، مزایا، محدودیت‌ها و مسیر تکامل مقایسه کن"
    )

    assert answer.execution_path == "rag"
    assert corpus.balanced_requests[0]["top_k"] == 12
    assert corpus.balanced_requests[0]["per_paper"] == 3
    assert len(answer.evidence) == 12


def test_topic_comparison_uses_selected_papers_even_when_individual_scores_are_low() -> None:
    items = [
        evidence(0.31).model_copy(update={"chunk_id": "r-topic", "paper_id": "homecharge"}),
        evidence(0.29).model_copy(update={"chunk_id": "x-topic", "paper_id": "retailcharge"}),
        evidence(0.27).model_copy(update={"chunk_id": "s-topic", "paper_id": "smartcharge"}),
        evidence(0.25).model_copy(update={"chunk_id": "e-topic", "paper_id": "solarhub"}),
    ]
    model = ScriptedModel(
        [
            GenerationResult(
                text=(
                    "موضوع هر چهار مقاله طبقه‌بندی تصویر است، اما ایدهٔ اصلی "
                    "آن‌ها فرق دارد: HomeCharge اتصال باقیمانده [E1]، RetailCharge تبدیل "
                    "تجمیعی [E2]، SmartCharge توجه کانالی [E3] و SolarHub "
                    "مقیاس‌بندی مرکب [E4] را بررسی می‌کنند."
                )
            )
        ]
    )

    answer = assistant(items, model).ask(
        "چهار مقاله رو با هم مقایسه کن و تفاوتاشون در موضوع مقاله رو به من بگو"
    )

    assert answer.mode == "rag"
    assert {item.paper_id for item in answer.evidence} == {
        "homecharge",
        "retailcharge",
        "smartcharge",
        "solarhub",
    }
    assert not answer.text.startswith(GENERAL_FALLBACK_PREFIX)


def test_multi_paper_rag_requires_named_and_cited_coverage_of_all_four_papers() -> None:
    items = [
        evidence(0.30).model_copy(update={"chunk_id": "r", "paper_id": "homecharge"}),
        evidence(0.30).model_copy(update={"chunk_id": "x", "paper_id": "retailcharge"}),
        evidence(0.30).model_copy(update={"chunk_id": "s", "paper_id": "smartcharge"}),
        evidence(0.30).model_copy(update={"chunk_id": "e", "paper_id": "solarhub"}),
    ]
    incomplete = ScriptedModel(
        [
            GenerationResult(
                text="در این مجموعه فقط SolarHub از مقیاس‌بندی مرکب استفاده می‌کند [E4]."
            )
        ]
    )
    complete = ScriptedModel(
        [
            GenerationResult(
                text=(
                    "HomeCharge یادگیری باقیمانده را بررسی می‌کند [E1]؛ RetailCharge بر "
                    "تجمیع تبدیل‌ها متمرکز است [E2]؛ SmartCharge توجه کانالی را مطرح "
                    "می‌کند [E3]؛ و SolarHub مقیاس‌بندی مرکب را بررسی می‌کند "
                    "[E4]. بنابراین موضوع مشترک طبقه‌بندی تصویر است، اما نوآوری "
                    "معماری هر مقاله تفاوت دارد."
                )
            )
        ]
    )
    model = ModelFallbackChain([("incomplete", incomplete), ("complete", complete)])

    answer = assistant(items, model).ask("موضوع مقالات رو با هم مقایسه کن")

    assert "HomeCharge" in answer.text
    assert "RetailCharge" in answer.text
    assert "SmartCharge" in answer.text
    assert "SolarHub" in answer.text
    assert incomplete.calls == 1
    assert complete.calls == 1


def test_model_analysis_is_inserted_before_the_artifact_section() -> None:
    combined = ResearchAssistant._combine_verified_and_analysis(
        "## Verified\n\nTable\n\n### Generated artifacts\n\n- CSV: `x.csv`",
        "## Language-model analysis",
        "The model's comparative reasoning.",
    )

    assert combined.index("Language-model analysis") < combined.index("Generated artifacts")


def test_comparison_validator_rejects_incorrect_numeric_gaps() -> None:
    result = GenerationResult(
        text="",
        tool_calls=[
            ExecutedToolCall(
                name="compare_results",
                arguments={},
                output=ComparisonTools().execute("compare_results", {}),
            )
        ],
    )
    wrong = (
        "HomeCharge-152 80.62, RetailCharge-101 80.90, SmartCharge-154 83.12, "
        "SolarHub-B7 84.40 is the winner. The gaps are 1.00, 2.00, "
        "and 3.00 percentage points, while protocol differences mean this "
        "is not a fully controlled comparison across the four models."
    )

    assert not ResearchAssistant._valid_tool_synthesis(wrong, result, "Compare results")


def test_comparison_rejects_mixed_script_analysis_and_uses_clean_persian() -> None:
    tools = ComparisonTools()
    mixed_script = (
        "مقایسهٔ چهار مدل نشان می‌دهد HomeCharge-152 با 80.62، RetailCharge-101 با "
        "80.90، SmartCharge-154 با 83.12 و SolarHub-B7 با 84.40 درصد مرتب شده‌اند. "
        "فاصلهٔ برنده با سه مدل دیگر 1.28، 3.50 و 3.78 واحد درصد است. تفاوت "
        "پروتکل‌ها مانع یک مقایسهٔ کاملاً کنترل‌شده است، اما 输入 و 原因 دقیق نیستند."
    )
    clean_persian = (
        "مقایسهٔ چهار مدل نشان می‌دهد HomeCharge-152 با 80.62، RetailCharge-101 با "
        "80.90، SmartCharge-154 با 83.12 و SolarHub-B7 با 84.40 درصد مرتب شده‌اند. "
        "فاصلهٔ SolarHub-B7 با سه مدل دیگر به‌ترتیب 1.28، 3.50 و 3.78 "
        "واحد درصد است. بااین‌حال تفاوت پروتکل‌های ارزیابی مانع یک مقایسهٔ "
        "کاملاً کنترل‌شده است و این رتبه‌بندی فقط نتایج گزارش‌شده را نشان می‌دهد."
    )

    class ValidationAwareModel:
        def __init__(self) -> None:
            self.synthesis_attempts = 0

        def run_validated(self, validator, **kwargs: Any) -> GenerationResult:
            if kwargs.get("tools"):
                output = kwargs["execute_tool"]("compare_results", {})
                candidate = GenerationResult(
                    text="",
                    tool_calls=[
                        ExecutedToolCall(
                            name="compare_results",
                            arguments={},
                            output=output,
                        )
                    ],
                )
                assert validator(candidate)
                return candidate
            for text in (mixed_script, clean_persian):
                self.synthesis_attempts += 1
                candidate = GenerationResult(text=text)
                if validator(candidate):
                    return candidate
            return candidate

    model = ValidationAwareModel()
    answer = assistant([evidence()], model, tools).ask(
        "نتایج چهار مقاله را با هم مقایسه و تحلیل کن"
    )

    assert model.synthesis_attempts == 2
    assert not re.search(r"[\u3400-\u9fff]", answer.text)
    assert "پروتکل‌های ارزیابی" in answer.text


@pytest.mark.parametrize(
    "contaminated",
    [
        "فروشگاه‌ها سو퍼센ترهای مناسبی برای شارژ سریع هستند.",
        "این मکان برای شارژ خودرو مناسب است.",
        "این مشابهIstگاه برای توقف کوتاه مناسب است.",
        "این triển khai برای رانندگان ساده است.",
        "این طرح در انڈیاناپولیس اجرا شده است.",
    ],
)
def test_persian_language_guard_rejects_cross_script_contamination(
    contaminated: str,
) -> None:
    question = "چرا فروشگاه‌ها برای شارژ سریع خودرو مناسب هستند؟"

    assert not ResearchAssistant._language_matches(contaminated, question)


def test_persian_language_guard_allows_separated_technical_names() -> None:
    question = "چرا فروشگاه‌ها برای شارژ سریع خودرو مناسب هستند؟"
    answer = "شارژ DC در فروشگاه برای رانندگان سریع و کاربردی است."

    assert ResearchAssistant._language_matches(answer, question)


def test_result_comparison_answers_an_explicit_method_recommendation() -> None:
    tools = ComparisonTools()

    class RecommendingModel(ScriptedModel):
        def run(self, **kwargs: Any) -> GenerationResult:
            self.requests.append(kwargs)
            self.calls += 1
            if kwargs.get("tools"):
                output = kwargs["execute_tool"]("compare_results", {})
                return GenerationResult(
                    text="",
                    tool_calls=[
                        ExecutedToolCall(
                            name="compare_results",
                            arguments={},
                            output=output,
                        )
                    ],
                )
            return GenerationResult(
                text=(
                    "HomeCharge-152 با 80.62، RetailCharge-101 با 80.90، SmartCharge-154 با "
                    "83.12 و SolarHub-B7 با 84.40 درصد مرتب می‌شوند. فاصلهٔ "
                    "SolarHub-B7 با سه مدل دیگر 1.28، 3.50 و 3.78 واحد درصد "
                    "است. چون پروتکل‌ها یکسان نیستند، این مقایسه کاملاً کنترل‌شده "
                    "نیست. پیشنهاد نهایی: اگر معیار اصلی بیشترین Top-1 گزارش‌شده "
                    "باشد، SolarHub-B7 انتخاب مناسب‌تری است؛ برای محدودیت منابع "
                    "باید بنچمارک هم‌شرایط جداگانه انجام شود."
                )
            )

    answer = assistant([evidence()], RecommendingModel([]), tools).ask(
        "کدام مقاله نتایج بهتری داشته و کدام روش را در نهایت پیشنهاد می‌دهی؟"
    )

    assert "پیشنهاد نهایی" in answer.text
    assert "اگر معیار اصلی" in answer.text
    assert "SolarHub-B7" in answer.text
    assert answer.text.count("1.28") == 1
    assert "Protocols differ" not in answer.text


@pytest.mark.parametrize(
    ("tool_name", "question", "required_instruction"),
    [
        (
            "extract_experimental_results",
            "Extract and export the raw accuracy results.",
            "raw metric extraction",
        ),
        (
            "generate_survey",
            "Generate a mini survey of all four papers.",
            "mini-survey",
        ),
        (
            "find_research_gap",
            "Find the research gaps and future work.",
            "research gaps",
        ),
        (
            "compare_references",
            "Compare the references by publication year.",
            "reference history",
        ),
        (
            "compare_limitations",
            "Compare the limitations of all four papers.",
            "limitations",
        ),
    ],
)
def test_non_comparison_tools_receive_task_specific_synthesis_instructions(
    tool_name: str,
    question: str,
    required_instruction: str,
) -> None:
    model = ScriptedModel(
        [
            GenerationResult(
                text="",
                tool_calls=[
                    ExecutedToolCall(
                        name=tool_name,
                        arguments={},
                        output=json.dumps(
                            {
                                "text": (
                                    "A complete cited research artifact covering all four "
                                    "selected papers and the requested analytical dimensions."
                                )
                            }
                        ),
                    )
                ],
            ),
            GenerationResult(
                text=(
                    "This final synthesis explains the requested research artifact, "
                    "highlights the most important supported findings, preserves "
                    "uncertainty, and avoids introducing unsupported numerical claims."
                )
            ),
        ]
    )

    answer = assistant([evidence()], model).ask(question)

    assert answer.execution_path == "agent_tool"
    synthesis_instructions = model.requests[1]["instructions"].casefold()
    assert required_instruction in synthesis_instructions
    assert "state every winner-vs-model gap" not in synthesis_instructions


def test_llm_generated_research_report_is_not_synthesized_twice() -> None:
    items = [
        evidence().model_copy(update={"paper_id": "homecharge", "chunk_id": "r"}),
        evidence().model_copy(update={"paper_id": "retailcharge", "chunk_id": "x"}),
        evidence().model_copy(update={"paper_id": "smartcharge", "chunk_id": "s"}),
        evidence().model_copy(update={"paper_id": "solarhub", "chunk_id": "e"}),
    ]

    class GeneratedReportTools(FakeTools):
        def execute(self, name: str, arguments: dict[str, Any]) -> str:
            assert name == "compare_limitations"
            return json.dumps(
                {
                    "generated_by_llm": True,
                    "text": (
                        "گزارش مستند محدودیت‌های HomeCharge، RetailCharge، SmartCharge و "
                        "SolarHub را با حفظ عدم قطعیت مقایسه می‌کند."
                    ),
                    "evidence": [item.model_dump(mode="json") for item in items],
                },
                ensure_ascii=False,
            )

    class PlannerOnlyModel(ScriptedModel):
        def run(self, **kwargs: Any) -> GenerationResult:
            self.requests.append(kwargs)
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("An LLM-generated report must not be synthesized twice")
            output = kwargs["execute_tool"]("compare_limitations", {"language": "fa"})
            return GenerationResult(
                text="",
                tool_calls=[
                    ExecutedToolCall(
                        name="compare_limitations",
                        arguments={"language": "fa"},
                        output=output,
                    )
                ],
            )

    model = PlannerOnlyModel([])
    answer = assistant(items, model, GeneratedReportTools()).ask(
        "محدودیت‌های همه مقالات را مقایسه و تحلیل کن"
    )

    assert answer.execution_path == "agent_tool"
    assert "گزارش مستند محدودیت‌ها" in answer.text
    assert model.calls == 1


def test_multi_aspect_comparison_combines_rag_evidence_with_optional_tool_report() -> None:
    question = (
        "چهار مقاله را از نظر ایدهٔ اصلی، تغییر معماری، مزایا، محدودیت‌ها و "
        "مسیر تکامل روش‌ها با یکدیگر مقایسه کن."
    )
    items = [
        evidence().model_copy(
            update={
                "paper_id": "homecharge",
                "chunk_id": "r",
                "paper_title": "HomeCharge",
                "text": "HomeCharge introduces residual skip connections.",
            }
        ),
        evidence().model_copy(
            update={
                "paper_id": "retailcharge",
                "chunk_id": "x",
                "paper_title": "RetailCharge",
                "text": "RetailCharge introduces cardinality and aggregated transformations.",
            }
        ),
        evidence().model_copy(
            update={
                "paper_id": "smartcharge",
                "chunk_id": "s",
                "paper_title": "SmartCharge",
                "text": "SmartCharge recalibrates channels with squeeze and excitation.",
            }
        ),
        evidence().model_copy(
            update={
                "paper_id": "solarhub",
                "chunk_id": "e",
                "paper_title": "SolarHub",
                "text": "SolarHub jointly scales depth, width, and resolution.",
            }
        ),
    ]

    class GeneratedLimitationsTools(FakeTools):
        def execute(self, name: str, arguments: dict[str, Any]) -> str:
            assert name == "compare_limitations"
            return json.dumps(
                {
                    "generated_by_llm": True,
                    "text": "گزارش مکمل محدودیت‌های چهار روش با استناد تولید شد.",
                    "evidence": [item.model_dump(mode="json") for item in items],
                },
                ensure_ascii=False,
            )

    class HybridModel(ScriptedModel):
        def run(self, **kwargs: Any) -> GenerationResult:
            self.requests.append(kwargs)
            self.calls += 1
            if kwargs.get("tools"):
                output = kwargs["execute_tool"]("compare_limitations", {"language": "fa"})
                return GenerationResult(
                    text="",
                    tool_calls=[
                        ExecutedToolCall(
                            name="compare_limitations",
                            arguments={"language": "fa"},
                            output=output,
                        )
                    ],
                )
            return GenerationResult(
                text=(
                    "## مقایسهٔ جامع\n\n"
                    "**HomeCharge — ایده و معماری:** اتصال میان‌بُر را معرفی می‌کند؛ "
                    "مزیت آن آموزش شبکهٔ عمیق‌تر و محدودیت آن بازده نزولی عمق است [E1].\n\n"
                    "**RetailCharge — ایده و معماری:** Cardinality و تبدیل‌های تجمیع‌شده "
                    "را اضافه می‌کند؛ مزیت آن افزایش ظرفیت و محدودیت آن هزینه است [E2].\n\n"
                    "**SmartCharge — ایده و معماری:** کانال‌ها را بازوزن‌دهی می‌کند؛ "
                    "مزیت آن توجه کانالی و محدودیت آن سربار بلوک است [E3].\n\n"
                    "**SolarHub — ایده و معماری:** عمق، عرض و وضوح را هم‌زمان "
                    "مقیاس می‌دهد؛ مزیت آن توازن دقت و کارایی و محدودیت آن وابستگی "
                    "به ضریب‌های مقیاس است [E4].\n\n"
                    "**مسیر تکامل:** این روند از بهینه‌سازی عمق در HomeCharge، به تنوع "
                    "مسیر در RetailCharge، توجه کانالی در SmartCharge و مقیاس‌بندی مرکب در "
                    "SolarHub می‌رسد [E1][E2][E3][E4]."
                )
            )

    model = HybridModel([])
    answer = assistant(items, model, GeneratedLimitationsTools()).ask(question)

    assert answer.execution_path == "hybrid"
    assert model.calls == 2
    assert all(
        name in answer.text
        for name in ("HomeCharge", "RetailCharge", "SmartCharge", "SolarHub")
    )
    assert all(axis in answer.text for axis in ("ایده", "معماری", "مزیت", "محدودیت", "مسیر تکامل"))
    assert "گزارش پژوهشی به بررسی و مقایسه ضعف‌ها" not in answer.text
    assert "Retrieved evidence" in model.requests[1]["user_input"]
    assert "Tool report" in model.requests[1]["user_input"]


def test_agent_plan_allows_multiple_distinct_llm_selected_tools() -> None:
    result = GenerationResult(
        text="",
        tool_calls=[
            ExecutedToolCall(
                name="compare_results",
                arguments={},
                output="{}",
            ),
            ExecutedToolCall(
                name="compare_references",
                arguments={},
                output="{}",
            ),
        ],
    )

    assert ResearchAssistant._valid_tool_plan(
        result,
        {"compare_results", "compare_references", "generate_survey"},
    )


def test_grouped_citation_syntax_is_normalized_for_clickable_evidence() -> None:
    text = (
        "HomeCharge و RetailCharge تفاوت معماری دارند [E1, E2] و SmartCharge [E3] و "
        "SolarHub [E4–E6] مسیر تکامل را کامل می‌کنند."
    )

    normalized = ResearchAssistant._normalize_grouped_citations(text)

    assert "[E1][E2]" in normalized
    assert "[E1, E2]" not in normalized
    assert "[E4][E5][E6]" in normalized
