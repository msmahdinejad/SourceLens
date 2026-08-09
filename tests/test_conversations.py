from __future__ import annotations

from paperlens.conversations import ConversationMemory
from paperlens.domain import Evidence, ResearchAnswer
from paperlens.language_models.base import GenerationResult


def _evidence(chunk_id: str, text: str) -> Evidence:
    return Evidence(
        chunk_id=chunk_id,
        paper_id="doc-1",
        paper_title="Source",
        section="Methods",
        section_type="methodology",
        page_start=2,
        page_end=2,
        text=text,
        similarity=0.91,
        workspace_id="ws-1",
        document_id="doc-1",
        filename="source.pdf",
    )


class SummaryModel:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def run(self, **kwargs) -> GenerationResult:
        self.requests.append(kwargs)
        return GenerationResult(
            text=(
                "The conversation established that hybrid retrieval combines semantic and "
                "lexical evidence. The user next wants its practical implications."
            )
        )


def test_conversation_memory_persists_turns_and_reuses_relevant_evidence(tmp_path) -> None:
    memory = ConversationMemory(tmp_path, SummaryModel())
    answer = ResearchAnswer(
        mode="rag",
        text="It combines semantic and lexical retrieval [E1].",
        evidence=[_evidence("chunk-1", "Hybrid retrieval combines semantic and lexical search.")],
    )

    memory.record_exchange("ws-1", "chat-12345678", "What is the method?", answer)
    context = memory.context_for(
        "ws-1",
        "chat-12345678",
        "Why does hybrid retrieval matter?",
    )

    assert context.history == [
        ("user", "What is the method?"),
        ("assistant", "It combines semantic and lexical retrieval [E1]."),
    ]
    assert [item.chunk_id for item in context.evidence] == ["chunk-1"]
    restored = ConversationMemory(tmp_path, SummaryModel()).get_public("ws-1", "chat-12345678")
    assert restored["turns"][1]["evidence"][0]["chunk_id"] == "chunk-1"


def test_conversation_memory_compacts_old_context_without_losing_the_recent_turns(
    tmp_path,
) -> None:
    model = SummaryModel()
    memory = ConversationMemory(
        tmp_path,
        model,
        context_budget_tokens=220,
        recent_turns=2,
    )
    answer = ResearchAnswer(mode="rag", text="A" * 700)
    for index in range(3):
        memory.record_exchange(
            "ws-1",
            "chat-12345678",
            f"Question {index} " + "Q" * 300,
            answer,
        )

    context = memory.context_for("ws-1", "chat-12345678", "What follows from that?")

    assert model.requests
    assert context.summary.startswith("The conversation established")
    assert context.history == [
        ("user", "Question 2 " + "Q" * 300),
        ("assistant", "A" * 700),
    ]
    assert context.compacted is True
    stored = memory.get_public("ws-1", "chat-12345678")
    assert stored["turn_count"] == 6
    assert len(stored["turns"]) == 6


def test_conversation_compaction_survives_a_temporary_model_outage(tmp_path) -> None:
    class UnavailableSummaryModel:
        @staticmethod
        def run(**_kwargs):
            raise RuntimeError("all summary routes are cooling down")

    memory = ConversationMemory(
        tmp_path,
        UnavailableSummaryModel(),
        context_budget_tokens=220,
        recent_turns=2,
    )
    answer = ResearchAnswer(mode="rag", text="An established answer " * 50)
    for index in range(3):
        memory.record_exchange(
            "ws-1",
            "chat-12345678",
            f"Research goal {index} " + "context " * 50,
            answer,
        )

    context = memory.context_for("ws-1", "chat-12345678", "Continue the analysis")

    assert context.compacted is True
    assert "Research goal" in context.summary
    assert len(context.history) == 2
