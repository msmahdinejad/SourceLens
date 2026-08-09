from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Protocol

from paperlens.domain import Evidence, ResearchAnswer, ToolCallRecord
from paperlens.language_models.base import (
    ExecutedToolCall,
    GenerationResult,
    LanguageModel,
    ModelCapabilityError,
)
from paperlens.language_quality import has_persian_script, is_clean_persian
from paperlens.routing import (
    is_multi_aspect_comparison,
    is_multi_paper_question,
)
from paperlens.tools.registry import ResearchTools

GENERAL_FALLBACK_PREFIX = (
    "پاسخ شما در مقالات/اسناد انتخاب‌شده یافت نشد. اما با استفاده از مدل زبانی، "
    "پاسخ عمومی به شرح زیر است:"
)
_CITATION = re.compile(r"\[E(\d+)]")
_INSUFFICIENT_MARKER = "INSUFFICIENT_EVIDENCE"
ANSWERABLE_SECTION_TYPES = {
    "abstract",
    "introduction",
    "related_work",
    "methodology",
    "experiments",
    "limitations",
    "conclusion",
    "other",
    "front_matter",
}
_TOOL_SYNTHESIS_TASKS = {
    "extract_experimental_results": """
This is a raw metric extraction, not a ranking task. Briefly explain what was
exported, confirm that Top-1 and Top-5 cover every returned document, and preserve the
evaluation-protocol caveat. Do not choose a winner or calculate performance gaps.
""".strip(),
    "compare_results": """
This is a quantitative result comparison. Identify the winner, state every
winner-vs-model gap in percentage points, explain the numerical progression,
and explain why protocol differences prevent a fully controlled comparison.
Mention every returned model. Do not attribute the gaps to architectural or training
causes because the reported comparison does not isolate causality.
""".strip(),
    "generate_survey": """
This is a mini-survey synthesis. Give a concise executive analysis of the common
problem, the method introduced by each selected document, the chronological
progress across the source set, and the final research
conclusion. Preserve the cited report's uncertainty and do not invent metrics.
""".strip(),
    "find_research_gap": """
This is a research gaps analysis. Synthesize explicitly supported limitations,
unresolved problems, and future directions across the papers. Clearly distinguish
authors' statements from cross-paper inferences and do not present missing data as
a proven limitation.
""".strip(),
    "compare_references": """
This is a reference history analysis. Explain the main publication-year patterns
and historical differences visible in the returned counts. Do not invent missing
counts and do not infer research quality or causality from reference volume alone.
""".strip(),
    "compare_limitations": """
This is a limitations comparison. Contrast the supported weaknesses of all selected
methods, preserve uncertainty, distinguish explicit statements from inference,
and do not turn absent evidence into a claimed limitation.
""".strip(),
    "search_documents": "Synthesize the retrieved evidence into a cited answer.",
    "summarize_document": "Write a structured, evidence-grounded document summary.",
    "compare_documents": "Compare the requested documents and aspects with balanced citations.",
    "extract_tables": "Explain the extracted tables; never invent missing cells or values.",
    "extract_figures": "Explain the extracted figures and their visible meaning.",
    "inspect_workspace": "Explain workspace health, document states, and vector statistics.",
    "rebuild_knowledge_base": "Confirm and analyze the completed rebuild report.",
}


class SearchableCorpus(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 6,
        paper_ids: set[str] | None = None,
        section_types: set[str] | None = None,
    ) -> list[Evidence]: ...

    def search_balanced(
        self,
        query: str,
        paper_ids: Sequence[str],
        *,
        top_k: int = 6,
        per_paper: int = 2,
        section_types: set[str] | None = None,
    ) -> list[Evidence]: ...


class ResearchAssistant:
    """Grounded question answering, function routing, and explicit fallback."""

    def __init__(
        self,
        *,
        corpus: SearchableCorpus,
        language_model: LanguageModel,
        tools: ResearchTools,
        minimum_similarity: float = 0.55,
        top_k: int = 6,
        document_ids: Sequence[str] = (),
        demo_mode: bool = False,
    ) -> None:
        self.corpus = corpus
        self.language_model = language_model
        self.tools = tools
        self.minimum_similarity = minimum_similarity
        self.top_k = top_k
        self.document_ids = tuple(document_ids)
        self.demo_mode = demo_mode

    def ask(
        self,
        question: str,
        history: Sequence[tuple[str, str]] = (),
        *,
        conversation_summary: str = "",
        prior_evidence: Sequence[Evidence] = (),
    ) -> ResearchAnswer:
        question = question.strip()
        if not question:
            raise ValueError("Question cannot be empty")
        contextual_question = self._contextual_question(
            question,
            history,
            conversation_summary=conversation_summary,
        )
        tool_specs = (
            self.tools.available_specs()
            if hasattr(self.tools, "available_specs")
            else self.tools.specs()
        )
        agent_can_search = any(spec.name == "search_documents" for spec in tool_specs)
        evidence = self._search_evidence(
            contextual_question,
            prior_evidence=prior_evidence,
            agent_can_search=agent_can_search,
        )
        if not evidence and not any(spec.name == "search_documents" for spec in tool_specs):
            return self._general_fallback(contextual_question)
        if tool_specs:
            tool_answer = self._agent_tool_answer(
                contextual_question,
                evidence,
                tool_specs,
                current_question=question,
            )
            if tool_answer is not None:
                return tool_answer
        if not evidence:
            return self._general_fallback(contextual_question)

        # Conversation history improves retrieval for true follow-ups, but it must
        # not make an unrelated current question look in-scope merely because an
        # earlier turn named one of the papers.
        if not self._retrieval_is_sufficient(question, evidence):
            return self._general_fallback(contextual_question)

        rag_request = {
            "instructions": self._rag_instructions(),
            "user_input": self._rag_input(contextual_question, evidence),
            "tools": [],
        }
        if hasattr(self.language_model, "run_validated"):
            result = self.language_model.run_validated(
                lambda candidate: (
                    _INSUFFICIENT_MARKER in candidate.text
                    or self._valid_rag_response(candidate.text, question, evidence)
                ),
                validation_profile="rag",
                **rag_request,
            )
        else:
            result = self.language_model.run(**rag_request)
        if _INSUFFICIENT_MARKER in result.text:
            return self._general_fallback(contextual_question)

        if not self._valid_rag_response(result.text, question, evidence):
            result = self._repair_citations(contextual_question, result.text, evidence)
            if not self._valid_rag_response(result.text, question, evidence):
                return self._general_fallback(contextual_question)

        tool_evidence = self._tool_evidence(result)
        return ResearchAnswer(
            mode="rag",
            text=self._normalize_grouped_citations(result.text),
            evidence=tool_evidence if result.tool_calls else evidence,
            tool_calls=[
                ToolCallRecord(
                    name=call.name,
                    arguments=call.arguments,
                    output=call.output,
                )
                for call in result.tool_calls
            ],
        )

    @staticmethod
    def _contextual_question(
        question: str,
        history: Sequence[tuple[str, str]],
        *,
        conversation_summary: str = "",
    ) -> str:
        """Add bounded conversation context so follow-ups are real, not visual-only."""
        turns = [
            (role, content.strip()[:1200])
            for role, content in history[-8:]
            if role in {"user", "assistant"} and content.strip()
        ]
        summary = conversation_summary.strip()[:8_000]
        if not turns and not summary:
            return question
        transcript = "\n".join(f"{role}: {content}" for role, content in turns)
        context = []
        if summary:
            context.append(f"Conversation memory:\n{summary}")
        if transcript:
            context.append(f"Recent conversation:\n{transcript}")
        return f"{'\n\n'.join(context)}\n\nCurrent question:\n{question}"

    def _agent_tool_answer(
        self,
        question: str,
        evidence: list[Evidence],
        specs: list,
        *,
        current_question: str,
    ) -> ResearchAnswer | None:
        """Let the LLM freely choose RAG, one/more tools, or insufficient evidence."""
        allowed = {spec.name for spec in specs}

        def execute_allowed(name: str, arguments: dict) -> str:
            if name not in allowed:
                raise ModelCapabilityError(f"The model selected an unavailable tool: {name}")
            return self.tools.execute(name, arguments)

        planner_kwargs = {
            "instructions": self._agent_instructions(),
            "user_input": self._rag_input(question, evidence),
            "tools": specs,
            "execute_tool": execute_allowed,
            "tool_choice": "auto",
            "return_after_tool": False,
        }

        def valid_agent_turn(candidate: GenerationResult) -> bool:
            if not self._valid_tool_plan(candidate, allowed):
                return False
            if candidate.tool_calls:
                return True
            if _INSUFFICIENT_MARKER in candidate.text:
                selected_ids = set(self.document_ids)
                complete_comparison_evidence = bool(
                    selected_ids
                    and selected_ids.issubset({item.paper_id for item in evidence})
                )
                return not (
                    is_multi_aspect_comparison(current_question) and complete_comparison_evidence
                )
            return bool(
                self._retrieval_is_sufficient(current_question, evidence)
                and self._valid_autonomous_answer(
                    candidate.text,
                    current_question,
                    evidence,
                )
            )

        if hasattr(self.language_model, "run_validated"):
            planned = self.language_model.run_validated(
                valid_agent_turn,
                validation_profile="agent_decision",
                **planner_kwargs,
            )
        else:
            planned = self.language_model.run(**planner_kwargs)
        if not planned.tool_calls:
            if _INSUFFICIENT_MARKER in planned.text:
                return None
            if self._retrieval_is_sufficient(
                current_question, evidence
            ) and self._valid_autonomous_answer(
                planned.text,
                current_question,
                evidence,
            ):
                return ResearchAnswer(
                    mode="rag",
                    text=self._normalize_grouped_citations(planned.text),
                    evidence=evidence,
                )
            if self._retrieval_is_sufficient(current_question, evidence):
                repaired = self._repair_citations(question, planned.text, evidence)
                if self._valid_autonomous_answer(
                    repaired.text,
                    current_question,
                    evidence,
                ):
                    return ResearchAnswer(
                        mode="rag",
                        text=self._normalize_grouped_citations(repaired.text),
                        evidence=evidence,
                    )
            return None
        if not self._valid_tool_plan(planned, allowed):
            raise ModelCapabilityError("The model returned an invalid tool plan")

        tool_evidence = self._tool_evidence(planned)
        analysis_evidence = (
            self._merge_evidence(tool_evidence, evidence)
            if is_multi_aspect_comparison(current_question) or len(planned.tool_calls) > 1
            else tool_evidence or evidence
        )
        if is_multi_aspect_comparison(current_question) or len(planned.tool_calls) > 1:
            hybrid = self._hybrid_tool_rag_answer(
                current_question,
                planned,
                analysis_evidence,
            )
            if hybrid is not None:
                return hybrid
        if self._tool_result_is_llm_generated(planned):
            generated_report = self._render_tool_result(question, planned)
            if not generated_report:
                raise RuntimeError("The LLM-backed research function returned no report")
            return ResearchAnswer(
                mode="rag",
                execution_path="agent_tool",
                text=self._normalize_grouped_citations(generated_report),
                evidence=analysis_evidence,
                tool_calls=[
                    ToolCallRecord(
                        name=call.name,
                        arguments=call.arguments,
                        output=call.output,
                    )
                    for call in planned.tool_calls
                ],
            )
        synthesis_input = self._tool_synthesis_input(
            question,
            planned,
            analysis_evidence,
        )
        if hasattr(self.language_model, "run_validated"):
            synthesis = self.language_model.run_validated(
                lambda candidate: self._valid_tool_synthesis(
                    self._sanitize_tool_synthesis(candidate.text),
                    planned,
                    question,
                ),
                validation_profile=("tool_synthesis:" + planned.tool_calls[0].name),
                instructions=self._tool_synthesis_instructions(planned, question),
                user_input=synthesis_input,
            )
        else:
            synthesis = self.language_model.run(
                instructions=self._tool_synthesis_instructions(planned, question),
                user_input=synthesis_input,
            )
            if not self._valid_tool_synthesis(
                self._sanitize_tool_synthesis(synthesis.text),
                planned,
                question,
            ):
                synthesis = self.language_model.run(
                    instructions=self._tool_repair_instructions(planned, question),
                    user_input=synthesis_input + f"\n\nIncomplete draft:\n{synthesis.text}",
                )
        text = self._sanitize_tool_synthesis(synthesis.text)
        if not self._valid_tool_synthesis(text, planned, question):
            raise ModelCapabilityError(
                "No configured model produced a valid analysis of the tool output"
            )
        else:
            verified = self._render_tool_result(question, planned)
            if verified:
                heading = (
                    "## تحلیل مدل زبانی"
                    if re.search(r"[\u0600-\u06ff]", question)
                    else "## Language-model analysis"
                )
                text = self._combine_verified_and_analysis(verified, heading, text)
        if not text:
            raise RuntimeError("The tool-analysis stage returned no displayable response")

        return ResearchAnswer(
            mode="rag",
            execution_path="agent_tool",
            text=text,
            evidence=analysis_evidence,
            tool_calls=[
                ToolCallRecord(
                    name=call.name,
                    arguments=call.arguments,
                    output=call.output,
                )
                for call in planned.tool_calls
            ],
        )

    def _hybrid_tool_rag_answer(
        self,
        question: str,
        planned: GenerationResult,
        evidence: list[Evidence],
    ) -> ResearchAnswer | None:
        """Synthesize broad comparisons from retrieval plus a supplemental tool."""
        tool_report = self._render_all_tool_results(question, planned)
        request = {
            "instructions": self._hybrid_comparison_instructions(),
            "user_input": (
                f"User question:\n{question}\n\n"
                f"Tool report (supplemental, not the final answer):\n{tool_report}\n\n"
                f"Retrieved evidence (authoritative citation map):\n"
                f"{self._evidence_context(evidence)}"
            ),
        }

        def validator(candidate: GenerationResult) -> bool:
            return self._valid_hybrid_comparison(
                candidate.text,
                question,
                evidence,
            )

        if hasattr(self.language_model, "run_validated"):
            synthesis = self.language_model.run_validated(
                validator,
                validation_profile="hybrid_tool_rag_comparison",
                **request,
            )
        else:
            synthesis = self.language_model.run(**request)
            if not validator(synthesis):
                synthesis = self.language_model.run(
                    instructions=(
                        self._hybrid_comparison_instructions()
                        + "\n\nThe previous draft was incomplete. Rewrite it and cover "
                        "every requested comparison dimension and selected document."
                    ),
                    user_input=request["user_input"] + f"\n\nIncomplete draft:\n{synthesis.text}",
                )
        if not validator(synthesis):
            return None
        return ResearchAnswer(
            mode="rag",
            execution_path="hybrid",
            text=self._normalize_grouped_citations(synthesis.text).strip(),
            evidence=evidence,
            tool_calls=[
                ToolCallRecord(
                    name=call.name,
                    arguments=call.arguments,
                    output=call.output,
                )
                for call in planned.tool_calls
            ],
        )

    @staticmethod
    def _merge_evidence(
        primary: list[Evidence],
        supplemental: list[Evidence],
    ) -> list[Evidence]:
        merged: list[Evidence] = []
        seen: set[tuple[str, str, int, int]] = set()
        for item in [*primary, *supplemental]:
            key = (item.paper_id, item.chunk_id, item.page_start, item.page_end)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    def _valid_hybrid_comparison(
        self,
        text: str,
        question: str,
        evidence: list[Evidence],
    ) -> bool:
        if len(text.strip()) < 350 or len(text) > 9000:
            return False
        if not self._valid_rag_response(text, question, evidence):
            return False
        normalized = text.casefold().replace("‌", " ")
        required_facets = {
            "idea": ("ایده", "نوآوری", "idea", "innovation"),
            "architecture": ("معماری", "ساختار", "architecture"),
            "advantage": ("مزیت", "مزایا", "نقطه قوت", "advantage", "strength"),
            "limitation": ("محدودیت", "نقطه ضعف", "weakness", "limitation"),
            "evolution": ("تکامل", "روند", "evolution", "progression"),
        }
        question_normalized = question.casefold().replace("‌", " ")
        requested = [
            aliases
            for aliases in required_facets.values()
            if any(alias in question_normalized for alias in aliases)
        ]
        return all(any(alias in normalized for alias in aliases) for aliases in requested)

    @staticmethod
    def _hybrid_comparison_instructions() -> str:
        return """
You are the senior comparative analyst of SourceLens. Answer the complete user
question, not merely the topic of the supplemental function report. Combine the
retrieved paper evidence with useful facts from the tool report. The tool report
is supporting material and must never become the title or scope of the answer.

For every requested dimension, explicitly compare each document in the requested
scope. When requested, cover: main idea, architectural change, supported advantages,
explicitly supported limitations, and chronological evolution. Use a consistent
per-document structure followed by a cross-document synthesis. Do not
invent a limitation when the evidence is silent; state the uncertainty instead.

Use only the unified Retrieved evidence for paper claims and cite every factual
claim with its [E#] label. Citation labels in the tool report refer to the same
leading evidence entries, but the Retrieved evidence block is authoritative.
Write multiple citations as [E1][E2], never as [E1, E2] in one bracket.
Answer exclusively in the user's language. Return polished Markdown without JSON,
internal implementation details, tool names, or artifact paths.
""".strip()

    @staticmethod
    def _valid_tool_plan(result: GenerationResult, allowed: set[str]) -> bool:
        calls = result.tool_calls
        names = [call.name for call in calls]
        repeated = {name for name in names if names.count(name) > 1}
        return bool(
            len(names) <= 8
            and repeated.issubset({"search_documents"})
            and set(names).issubset(allowed)
        )

    def _search_evidence(
        self,
        question: str,
        *,
        prior_evidence: Sequence[Evidence] = (),
        agent_can_search: bool = False,
    ) -> list[Evidence]:
        if agent_can_search:
            return list(prior_evidence)
        selected_ids = tuple(self.document_ids)
        named_ids = mentioned_document_ids(question, selected_ids)
        if not (is_multi_paper_question(question) and selected_ids):
            return self.corpus.search(
                question,
                top_k=self.top_k,
                paper_ids=named_ids or set(selected_ids) or None,
                section_types=ANSWERABLE_SECTION_TYPES,
            )
        scoped_ids = tuple(sorted(named_ids)) or selected_ids
        result_limit = (
            max(self.top_k, len(scoped_ids) * 3)
            if is_multi_aspect_comparison(question)
            else max(self.top_k, len(scoped_ids))
        )
        per_document = max(1, (result_limit + len(scoped_ids) - 1) // len(scoped_ids))
        return self.corpus.search_balanced(
            question,
            scoped_ids,
            top_k=result_limit,
            per_paper=per_document,
            section_types=ANSWERABLE_SECTION_TYPES,
        )

    def _render_tool_result(self, question: str, result: GenerationResult) -> str:
        verified = self._render_verified_tool_result(question, result)
        if verified:
            return verified
        for call in result.tool_calls:
            payload = json.loads(call.output)
            if isinstance(payload.get("text"), str) and payload["text"].strip():
                return payload["text"].strip()
            if call.name == "compare_references" and payload.get("counts"):
                return self._render_reference_counts(question, payload)
        # A model adapter may return post-tool prose alongside the executed calls.
        # That prose is a planner by-product, not a deterministic tool result, and
        # has not passed the synthesis language/citation validator.
        return ""

    def _render_all_tool_results(
        self,
        question: str,
        result: GenerationResult,
    ) -> str:
        blocks = []
        for call in result.tool_calls:
            single = GenerationResult(text="", tool_calls=[call])
            rendered = self._render_tool_result(question, single)
            if rendered:
                blocks.append(rendered)
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _tool_evidence(result: GenerationResult) -> list[Evidence]:
        collected: list[Evidence] = []
        seen: set[tuple[str, str, int, int]] = set()
        for call in result.tool_calls:
            payload = json.loads(call.output)
            if payload.get("evidence"):
                call_evidence = [Evidence.model_validate(item) for item in payload["evidence"]]
            elif payload.get("rows"):
                call_evidence = []
                for row in payload["rows"]:
                    pages = [
                        int(value)
                        for value in re.findall(r"\d+", str(row.get("source_pages", "1")))
                    ] or [1]
                    call_evidence.append(
                        Evidence(
                            chunk_id=(
                                "verified-result-"
                                + str(row.get("paper_id", row["model"])).casefold()
                            ),
                            paper_id=str(row.get("paper_id", row["model"])).casefold(),
                            paper_title=str(row["paper_title"]),
                            section=str(row.get("source_location", "Experimental results")),
                            section_type="experiments",
                            page_start=pages[0],
                            page_end=pages[-1],
                            text=(
                                f"{row['model']} reports Top-1 accuracy "
                                f"{float(row['top1_accuracy']):.2f}% and Top-5 accuracy "
                                f"{float(row['top5_accuracy']):.2f}% on "
                                f"{row.get('dataset', 'reported dataset')} "
                                f"{row.get('split', 'validation')}; protocol: "
                                f"{row['inference_protocol']}."
                            ),
                            similarity=1.0,
                        )
                    )
            else:
                call_evidence = []
            for item in call_evidence:
                key = (item.paper_id, item.chunk_id, item.page_start, item.page_end)
                if key not in seen:
                    seen.add(key)
                    collected.append(item)
        return collected

    @staticmethod
    def _tool_result_is_llm_generated(result: GenerationResult) -> bool:
        return bool(
            result.tool_calls
            and all(
                json.loads(call.output).get("generated_by_llm") is True
                for call in result.tool_calls
            )
        )

    @staticmethod
    def _render_reference_counts(question: str, payload: dict) -> str:
        counts = payload["counts"]
        papers = sorted(counts)
        years = sorted({int(year) for values in counts.values() for year in values})
        persian = bool(re.search(r"[\u0600-\u06ff]", question))
        title = "## مقایسهٔ تاریخی منابع" if persian else "## Reference history comparison"
        lines = [title, "", "| Year | " + " | ".join(papers) + " |"]
        lines.append("|---:" + "|---:" * len(papers) + "|")
        for year in years:
            values = [
                str(counts[paper].get(str(year), counts[paper].get(year, 0))) for paper in papers
            ]
            lines.append(f"| {year} | " + " | ".join(values) + " |")
        if payload.get("warning"):
            lines.extend(["", f"> {payload['warning']}"])
        return "\n".join(lines)

    def _retrieval_is_sufficient(
        self,
        question: str,
        evidence: list[Evidence],
    ) -> bool:
        retrieved_paper_ids = {item.paper_id for item in evidence}
        has_complete_comparison_context = (
            is_multi_paper_question(question)
            and len(retrieved_paper_ids) >= 2
        )
        return bool(
            evidence
            and (
                mentioned_document_ids(question, self.document_ids)
                or has_complete_comparison_context
                or max(item.similarity for item in evidence) >= self.minimum_similarity
            )
        )

    def _general_fallback(self, question: str) -> ResearchAnswer:
        request = {
            "instructions": (
                "Answer only the exact user question below from general knowledge. "
                "Be accurate and concise; ignore unrelated topics. Answer in Persian "
                "when the actual question contains Persian script, otherwise use the actual "
                "question's language. If a Current question marker is present, detect language "
                "from that final section and not from attached image evidence. Do not imply "
                "that the answer came from selected sources or ask the user to attach them. "
                "Do not claim sources are missing unless an attached image directly shows an "
                "empty-source interface state relevant to the question."
            ),
            "user_input": question,
        }
        has_prompt_image = "[Prompt image evidence]" in question

        def validator(candidate: GenerationResult) -> bool:
            return bool(
                len(candidate.text.strip()) >= 12
                and not _CITATION.search(candidate.text)
                and (has_prompt_image or not self._claims_missing_selected_papers(candidate.text))
                and self._language_matches(candidate.text, question)
            )

        if hasattr(self.language_model, "run_validated"):
            result = self.language_model.run_validated(
                validator,
                validation_profile="general_fallback",
                **request,
            )
        else:
            result = self.language_model.run(**request)
        if not validator(result):
            result = GenerationResult(
                text=(
                    "مدل زبانی در این نوبت پاسخ عمومیِ قابل‌اعتمادی تولید نکرد."
                    if re.search(r"[\u0600-\u06ff]", question)
                    else "The language model did not produce a reliable general answer."
                )
            )
        return ResearchAnswer(
            mode="general_fallback",
            execution_path="general_fallback",
            text=f"{GENERAL_FALLBACK_PREFIX}\n\n{result.text}",
        )

    @staticmethod
    def _claims_missing_selected_papers(text: str) -> bool:
        normalized = text.casefold().replace("‌", " ")
        persian_claim = re.search(
            r"(?:هیچ\s+)?مقاله.{0,45}(?:ضمیمه|آپلود|ارسال|نفرست|نداده|ارائه\s+نکرد)",
            normalized,
        )
        english_claim = re.search(
            r"(?:no|not\s+provided|without).{0,35}papers?.{0,45}(?:attach|upload|send|provide)",
            normalized,
        )
        return bool(persian_claim or english_claim)

    @staticmethod
    def _language_matches(text: str, question: str) -> bool:
        current_question = question.rsplit("Current question:\n", 1)[-1]
        if not has_persian_script(current_question):
            return True
        return is_clean_persian(text)

    def _repair_citations(
        self,
        question: str,
        draft: str,
        evidence: list[Evidence],
    ) -> GenerationResult:
        return self.language_model.run(
            instructions=(
                "Rewrite the draft using only the supplied evidence. Add valid inline "
                "citations such as [E1]. Write multiple citations as [E1][E2], never "
                "[E1, E2]. If evidence is insufficient, output exactly "
                f"{_INSUFFICIENT_MARKER}."
            ),
            user_input=(
                f"Question:\n{question}\n\nDraft:\n{draft}\n\n"
                f"Evidence:\n{self._evidence_context(evidence)}"
            ),
        )

    @staticmethod
    def _has_valid_citation(text: str, evidence: list[Evidence]) -> bool:
        cited = {int(match) for match in _CITATION.findall(text)}
        return bool(cited and all(1 <= number <= len(evidence) for number in cited))

    def _valid_rag_response(
        self,
        text: str,
        question: str,
        evidence: list[Evidence],
    ) -> bool:
        text = self._normalize_grouped_citations(text)
        if not self._has_valid_citation(text, evidence):
            return False
        if not self._language_matches(text, question):
            return False
        if not is_multi_paper_question(question):
            return True
        cited_numbers = {int(match) for match in _CITATION.findall(text)}
        cited_papers = {
            evidence[number - 1].paper_id
            for number in cited_numbers
            if 1 <= number <= len(evidence)
        }
        return len(cited_papers) >= min(2, len({item.paper_id for item in evidence}))

    @staticmethod
    def _normalize_grouped_citations(text: str) -> str:
        def expand_range(match: re.Match[str]) -> str:
            start, end = (int(match.group(1)), int(match.group(2)))
            if end < start or end - start > 50:
                return match.group(0)
            return "".join(f"[E{number}]" for number in range(start, end + 1))

        def expand(match: re.Match[str]) -> str:
            numbers = re.findall(r"\d+", match.group(1))
            return "".join(f"[E{number}]" for number in numbers)

        expanded_ranges = re.sub(
            r"\[E(\d+)\s*[‐‑‒–—-]\s*E?(\d+)\]",
            expand_range,
            text,
        )
        return re.sub(
            r"\[((?:E\d+\s*[,;،]\s*)+E?\d+)\]",
            expand,
            expanded_ranges,
        )

    def _valid_autonomous_answer(
        self,
        text: str,
        question: str,
        evidence: list[Evidence],
    ) -> bool:
        if is_multi_aspect_comparison(question):
            return self._valid_hybrid_comparison(text, question, evidence)
        return self._valid_rag_response(text, question, evidence)

    def _rag_input(self, question: str, evidence: list[Evidence]) -> str:
        return f"Question:\n{question}\n\nEvidence:\n{self._evidence_context(evidence)}"

    @staticmethod
    def _evidence_context(evidence: list[Evidence]) -> str:
        return "\n\n".join(
            f"[E{index}] {item.citation_label}\n{item.text}"
            for index, item in enumerate(evidence, start=1)
        )

    def _rag_instructions(self) -> str:
        comparison_rule = (
            "For a multi-document comparison, cite evidence from every document "
            "whose claims you compare; state when a requested aspect is unsupported."
        )
        return f"""
You are SourceLens, a rigorous bilingual research assistant.
Answer in the language of the user's question.

Use only the supplied evidence for claims about the selected documents.
Cite each factual claim inline with evidence labels such as [E1].
Never invent a metric, dataset, method, limitation, or citation.
If the evidence cannot answer the question, output exactly {_INSUFFICIENT_MARKER}.
{comparison_rule}

This is the evidence-only answer path; no tools are available in this pass. Do not
claim that a function ran. Explain protocol differences when comparing reported
results.
""".strip()

    def _agent_instructions(self) -> str:
        inventory = ", ".join(self.document_ids) or "No indexed document ids were supplied."
        scope_rule = """
The active workspace may contain arbitrary documents, images, or audio. Never
assume any particular demo papers are present. For comparisons, use only the
active document inventory and cite every document whose claims you compare.
""".strip()
        return f"""
You are the autonomous research brain of SourceLens. The application already
contains an active research workspace. You receive its retrieved evidence and all
available research-function schemas on every turn.

Active document ids: {inventory}
{scope_rule}

Decide freely whether the best answer needs no function, one function, or several
different functions. Use functions only when their structured output materially
helps answer the complete request; never select a function merely because one
keyword appears in the question. A broad, multi-aspect comparison must retain all
requested aspects even if a supplemental function covers only one of them.

Plan retrieval around the information need rather than searching a vague question verbatim.
For an overview such as "what is this document about?", collect section-balanced coverage
with summarize_document. For several distinct aspects, use multiple focused searches.
Choose source terminology, inspect what each search found, and then synthesize.
When using search_documents, choose top_k from the actual information need. A narrow fact
usually needs only a few candidates; a broad synthesis or comparison needs wider coverage.
There is no evidence quota. You may call search_documents again with a distinct query when
the first result does not cover a requested aspect. Reuse relevant conversation evidence,
but replace stale evidence rather than carrying it into an unrelated answer.

For a broad comparison, lack of an explicit limitation statement in one document
is not grounds to reject the whole request. State that uncertainty for that document and
answer the remaining supported dimensions, or select a useful research function.

If no function is useful, answer directly from the supplied evidence with inline
[E#] citations. If the evidence cannot answer the question, output exactly
INSUFFICIENT_EVIDENCE. Never invent a function name, argument, paper claim, metric,
limitation, or citation. Answer exclusively in the user's language.
Write a self-contained answer; do not begin with "in continuation".
""".strip()

    @staticmethod
    def _tool_synthesis_instructions(
        result: GenerationResult,
        question: str,
    ) -> str:
        task = ResearchAssistant._tool_synthesis_task(result)
        recommendation = (
            """
The user explicitly requested a recommendation. End with a clearly labeled,
conditional recommendation: prefer the reported winner only when highest reported
Top-1 is the decision criterion, and state that deployment/resource choices require
a controlled benchmark. Do not present this as a universal winner.
""".strip()
            if ResearchAssistant._recommendation_requested(question)
            else ("The user did not request a recommendation; do not add one.")
        )
        return f"""
You are the senior research analyst of SourceLens. Answer in the user's language.
The function output below is authoritative. Write only the analytical prose that
will appear after an already-rendered verified table. Do not recreate a table and
do not list artifact paths.

Task-specific requirements:
{task}

Recommendation requirement:
{recommendation}

Answer exclusively in the language of the user's question; a Persian question
must receive Persian prose.
Never alter or invent values. Return concise Markdown prose (roughly 180–350
words), without JSON or code fences. Do not mention internal tools. Do not discuss
parameters, FLOPs, crop sizes, computational efficiency, hardware, or deployment
recommendations unless the authoritative function output explicitly supports
them. Keep metric digits in Latin form so they can be verified.
When the function output contains an evidence array, cite document claims with
the matching [E#] labels. Never cite a label that is not present.
""".strip()

    @staticmethod
    def _tool_repair_instructions(
        result: GenerationResult,
        question: str,
    ) -> str:
        task = ResearchAssistant._tool_synthesis_task(result)
        recommendation = (
            "Include a conditional final recommendation because the user requested it."
            if ResearchAssistant._recommendation_requested(question)
            else "Do not include a recommendation because the user did not request one."
        )
        return f"""
Rewrite the incomplete draft as a full research answer in the user's language.
Use the authoritative function JSON exactly. Return only 180–350 words of
analytical Markdown prose to follow a table that the application renders itself.
Do not make a table or list artifact paths. JSON and code fences are forbidden.
Persian questions must be answered only in Persian.

Task-specific requirements:
{task}

{recommendation}
""".strip()

    @staticmethod
    def _tool_synthesis_task(result: GenerationResult) -> str:
        names = {call.name for call in result.tool_calls}
        if not names:
            raise ValueError("A synthesis pass requires at least one research tool")
        try:
            return "\n\n".join(
                f"For `{name}`:\n{_TOOL_SYNTHESIS_TASKS[name]}" for name in sorted(names)
            )
        except KeyError as error:
            raise ValueError(f"Unsupported synthesis tool: {error.args[0]}") from error

    def _tool_synthesis_input(
        self,
        question: str,
        result: GenerationResult,
        evidence: list[Evidence],
    ) -> str:
        outputs = "\n\n".join(
            f"Function: {call.name}\nArguments: {json.dumps(call.arguments, ensure_ascii=False)}"
            f"\nAuthoritative JSON:\n{self._authoritative_tool_output(call)}"
            for call in result.tool_calls
        )
        evidence_text = self._compact_evidence_context(evidence) if evidence else "None"
        facts = self._comparison_facts(result)
        return (
            f"User question:\n{question}\n\n{outputs}\n\n"
            f"Verified arithmetic derived from the rows:\n{facts}\n\n"
            f"Optional retrieved method evidence:\n{evidence_text}"
        )

    @staticmethod
    def _authoritative_tool_output(call: ExecutedToolCall) -> str:
        payload = json.loads(call.output)
        if call.name in {
            "search_documents",
            "summarize_document",
            "compare_documents",
            "extract_tables",
            "extract_figures",
        }:
            # Evidence is supplied once below in a compact, citation-numbered block.
            # Repeating every full chunk inside each tool JSON made multi-search chat
            # prompts unnecessarily large and could add minutes to synthesis latency.
            projected = {key: value for key, value in payload.items() if key != "evidence"}
            projected["evidence_count"] = len(payload.get("evidence") or [])
            return json.dumps(projected, ensure_ascii=False)
        if call.name not in {"compare_results", "extract_experimental_results"}:
            return call.output
        fields = (
            "paper_id",
            "paper_title",
            "model",
            "dataset",
            "split",
            "top1_accuracy",
            "top5_accuracy",
            "inference_protocol",
            "source_pdf",
            "source_pages",
        )
        projected = {
            "rows": [
                {field: row.get(field) for field in fields} for row in payload.get("rows", [])
            ],
            "warning": payload.get("warning"),
        }
        return json.dumps(projected, ensure_ascii=False)

    @staticmethod
    def _valid_tool_synthesis(
        text: str,
        result: GenerationResult,
        question: str,
    ) -> bool:
        stripped = text.strip()
        if len(stripped) < 80 or len(stripped) > 5000:
            return False
        if not ResearchAssistant._language_matches(stripped, question):
            return False
        if "```" in stripped or '"authoritative_analysis"' in stripped:
            return False
        if re.search(r"[\u0600-\u06ff]", question):
            persian_characters = len(re.findall(r"[\u0600-\u06ff]", stripped))
            if persian_characters < 40:
                return False
        for call in result.tool_calls:
            if call.name != "compare_results":
                continue
            if len(stripped) < 160:
                return False
            payload = json.loads(call.output)
            models = [str(row.get("model", "")).strip() for row in payload.get("rows", [])]
            normalized_text = ResearchAssistant._canonical_model_text(text)
            if models and not all(
                ResearchAssistant._canonical_model_text(model) in normalized_text
                for model in models
            ):
                return False
            values = [float(row["top1_accuracy"]) for row in payload.get("rows", [])]
            analysis_terms = (
                "واحد درصد",
                "اختلاف",
                "فاصله",
                "percentage point",
                "gap",
            )
            if values and not any(term in text.casefold() for term in analysis_terms):
                return False
            if values:
                winner = max(payload["rows"], key=lambda row: row["top1_accuracy"])
                winner_value = float(winner["top1_accuracy"])
                if f"{winner_value:.2f}" not in text and f"{winner_value:g}" not in text:
                    return False
                expected_gaps = {
                    f"{winner_value - float(row['top1_accuracy']):.2f}"
                    for row in payload["rows"]
                    if row is not winner
                }
                if not expected_gaps.issubset(set(re.findall(r"\d+\.\d{2}", text))):
                    return False
            forbidden = (
                "flop",
                "پارامتر",
                "محاسبات",
                "سخت افزار",
                "سخت‌افزار",
                "deployment",
            )
            if any(term in text.casefold() for term in forbidden):
                return False
            recommendation_terms = (
                "پیشنهاد",
                "توصیه",
                "انتخاب",
                "recommend",
            )
            has_recommendation = any(term in text.casefold() for term in recommendation_terms)
            if ResearchAssistant._recommendation_requested(question):
                if not has_recommendation:
                    return False
                conditional_terms = ("اگر", "مشروط", "در صورتی", "if ", "depending")
                if not any(term in text.casefold() for term in conditional_terms):
                    return False
            elif has_recommendation:
                return False
            caveats = ("پروتکل", "کنترل", "protocol", "controlled")
            if not any(term in text.casefold() for term in caveats):
                return False
        generic_evidence_tools = {
            "search_documents",
            "summarize_document",
            "compare_documents",
            "extract_tables",
            "extract_figures",
        }
        if generic_evidence_tools.intersection({call.name for call in result.tool_calls}):
            evidence = ResearchAssistant._tool_evidence(result)
            if evidence and not ResearchAssistant._has_valid_citation(text, evidence):
                return False
        return True

    @staticmethod
    def _sanitize_tool_synthesis(text: str) -> str:
        without_tables = re.sub(
            r"(?:^\s*\|.*\|\s*$\r?\n?)+",
            "",
            text,
            flags=re.MULTILINE,
        )
        normalized = without_tables.replace("پروگرس عددی", "روند عددی")
        normalized = normalized.replace("و احتمالا روش‌های", "و تفاوت روش‌های")
        normalized = normalized.replace("و احتمالاً روش‌های", "و تفاوت روش‌های")
        return re.sub(r"\n{3,}", "\n\n", normalized).strip()

    @staticmethod
    def _canonical_model_text(value: str) -> str:
        normalized = value.casefold().replace("×", "x")
        normalized = re.sub(r"[‐‑‒–—−]", "-", normalized)
        return re.sub(r"\s+", " ", normalized)

    @staticmethod
    def _compact_evidence_context(evidence: list[Evidence]) -> str:
        return "\n\n".join(
            f"[E{index}] {item.citation_label}\n{item.text[:500]}"
            for index, item in enumerate(evidence[:6], start=1)
        )

    @staticmethod
    def _comparison_facts(result: GenerationResult) -> str:
        call = next((item for item in result.tool_calls if item.name == "compare_results"), None)
        if call is None:
            return "Not applicable."
        rows = json.loads(call.output).get("rows", [])
        if not rows:
            return "No result rows were returned."
        ranked = sorted(rows, key=lambda row: float(row["top1_accuracy"]), reverse=True)
        winner = ranked[0]
        facts = [f"Winner: {winner['model']} at {float(winner['top1_accuracy']):.2f}% Top-1."]
        for row in ranked[1:]:
            gap = float(winner["top1_accuracy"]) - float(row["top1_accuracy"])
            facts.append(f"Winner vs {row['model']}: {gap:.2f} percentage points.")
        for better, lower in zip(ranked, ranked[1:], strict=False):
            gap = float(better["top1_accuracy"]) - float(lower["top1_accuracy"])
            facts.append(f"{better['model']} vs {lower['model']}: {gap:.2f} points.")
        return "\n".join(facts)

    @staticmethod
    def _recommendation_requested(question: str) -> bool:
        normalized = question.casefold().replace("‌", " ")
        persian = re.search(
            r"(?:پیشنهاد|توصیه|انتخاب\s+کن|کدام\s+(?:روش|مدل).*(?:بهتر|مناسب))",
            normalized,
        )
        english = re.search(
            r"\b(?:recommend|recommendation|which\s+(?:method|model)\s+should)\b",
            normalized,
        )
        return bool(persian or english)

    @staticmethod
    def _combine_verified_and_analysis(
        verified: str,
        heading: str,
        analysis: str,
    ) -> str:
        marker = re.search(
            r"\n###\s+(?:فایل‌های خروجی|Generated artifacts)\s*\n",
            verified,
        )
        block = f"{heading}\n\n{analysis.strip()}"
        if marker is None:
            return f"{verified.rstrip()}\n\n{block}".strip()
        before = verified[: marker.start()].rstrip()
        artifacts = verified[marker.start() :].strip()
        return f"{before}\n\n{block}\n\n{artifacts}"

    @staticmethod
    def _render_verified_tool_result(
        question: str,
        result: GenerationResult,
    ) -> str | None:
        call = next(
            (
                item
                for item in result.tool_calls
                if item.name in {"compare_results", "extract_experimental_results"}
            ),
            None,
        )
        if call is None:
            return None
        payload = json.loads(call.output)
        rows = payload.get("rows", [])
        if not rows:
            return None

        is_persian = bool(re.search(r"[\u0600-\u06ff]", question))
        if is_persian:
            title = (
                "## مقایسهٔ نتایج تأییدشده"
                if call.name == "compare_results"
                else "## نتایج تجربی استخراج‌شده"
            )
            header = (
                "| مقاله | مدل | Top-1 Accuracy | Top-5 Accuracy | پروتکل / منبع |\n"
                "|---|---|---:|---:|---|"
            )
        else:
            title = (
                "## Verified result comparison"
                if call.name == "compare_results"
                else "## Extracted experimental results"
            )
            header = (
                "| Paper | Model | Top-1 Accuracy | Top-5 Accuracy | Protocol / source |\n"
                "|---|---|---:|---:|---|"
            )

        table_rows = [
            (
                f"| {row['paper_title']} | {row['model']} | "
                f"{row['top1_accuracy']:.2f}% | {row['top5_accuracy']:.2f}% | "
                f"{row['inference_protocol']}; {row['source_pdf']}, "
                f"p. {row['source_pages']} |"
            )
            for row in rows
        ]
        lines = [title, "", header, *table_rows]

        if call.name == "compare_results":
            winner = max(rows, key=lambda item: item["top1_accuracy"])
            if is_persian:
                lines.extend(
                    [
                        "",
                        (
                            f"**بهترین Top-1 گزارش‌شده:** {winner['model']} با "
                            f"{winner['top1_accuracy']:.2f}٪."
                        ),
                    ]
                )
            else:
                lines.extend(
                    [
                        "",
                        (
                            f"**Best reported Top-1:** {winner['model']} at "
                            f"{winner['top1_accuracy']:.2f}%."
                        ),
                    ]
                )

        warning = payload.get("warning")
        if warning:
            prefix = "**نکتهٔ مقایسه:**" if is_persian else "**Comparison caveat:**"
            displayed_warning = (
                "این اعداد همان نتایج گزارش‌شده در مقالات هستند، نه یک مقایسهٔ "
                "کاملاً کنترل‌شده؛ پروتکل برش تصویر، اندازهٔ ورودی و دستور آموزش "
                "در مقاله‌ها یکسان نیست."
                if is_persian
                else warning
            )
            lines.extend(["", f"{prefix} {displayed_warning}"])

        artifact_labels = {
            "csv_path": "CSV",
            "markdown_path": "Markdown",
            "chart_path": "Chart",
            "json_path": "JSON",
        }
        artifacts = [
            f"- {label}: `{_display_artifact_path(payload[key])}`"
            for key, label in artifact_labels.items()
            if payload.get(key)
        ]
        if artifacts:
            lines.extend(
                [
                    "",
                    "### فایل‌های خروجی" if is_persian else "### Generated artifacts",
                    "",
                    *artifacts,
                ]
            )
        return "\n".join(lines)


def _display_artifact_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    marker = "/outputs/"
    if marker in normalized:
        return "outputs/" + normalized.split(marker, 1)[1]
    return normalized


def mentioned_document_ids(query: str, document_ids: Sequence[str]) -> set[str]:
    """Extract explicitly named document ids for metadata-aware retrieval."""
    normalized = re.sub(r"[-_\s]+", " ", query.casefold())
    mentioned: set[str] = set()
    for document_id in document_ids:
        name = " ".join(
            document_id.casefold().replace("-", " ").replace("_", " ").split()
        )
        if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized):
            mentioned.add(document_id)
    return mentioned
