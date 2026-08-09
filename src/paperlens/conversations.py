from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from paperlens.domain import Evidence, ResearchAnswer
from paperlens.language_models.base import LanguageModel
from paperlens.workspaces import utc_now

_SAFE_WORKSPACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
_SAFE_CONVERSATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,100}")
_WORD = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_PERSISTENCE_LOCK = threading.RLock()


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str
    evidence: list[Evidence] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    workspace_id: str
    title: str
    summary: str = ""
    compacted_turn_count: int = Field(default=0, ge=0)
    turns: list[ConversationTurn] = Field(default_factory=list)
    evidence_pool: list[Evidence] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class ConversationContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    history: list[tuple[Literal["user", "assistant"], str]] = Field(default_factory=list)
    summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    compacted: bool = False


class ConversationMemory:
    """Persist full chats while exposing a bounded, automatically compacted model context."""

    def __init__(
        self,
        workspaces_root: Path,
        language_model: LanguageModel | Callable[[], LanguageModel],
        *,
        context_budget_tokens: int = 12_000,
        recent_turns: int = 8,
        evidence_memory_chars: int = 32_000,
        evidence_context_chars: int = 10_000,
    ) -> None:
        self.root = workspaces_root.resolve()
        self.language_model = language_model
        self.context_budget_tokens = max(100, context_budget_tokens)
        self.recent_turns = max(2, recent_turns)
        self.evidence_memory_chars = max(2_000, evidence_memory_chars)
        self.evidence_context_chars = max(1_000, evidence_context_chars)

    def context_for(
        self,
        workspace_id: str,
        conversation_id: str,
        question: str,
        fallback_history: Sequence[tuple[str, str]] = (),
    ) -> ConversationContext:
        with _PERSISTENCE_LOCK:
            record = self._load(workspace_id, conversation_id)
            if record is None:
                record = self._new(workspace_id, conversation_id, question)
                record.turns = [
                    ConversationTurn(role=role, content=content.strip())
                    for role, content in fallback_history
                    if role in {"user", "assistant"} and content.strip()
                ]
                self._save(record)
        compacted = self._compact_if_needed(workspace_id, conversation_id, question)
        with _PERSISTENCE_LOCK:
            record = self._require(workspace_id, conversation_id)
            active_turns = record.turns[record.compacted_turn_count :]
            return ConversationContext(
                history=[(turn.role, turn.content) for turn in active_turns],
                summary=record.summary,
                evidence=self._relevant_evidence(record, question),
                compacted=compacted,
            )

    def record_exchange(
        self,
        workspace_id: str,
        conversation_id: str,
        question: str,
        answer: ResearchAnswer,
    ) -> None:
        with _PERSISTENCE_LOCK:
            record = self._load(workspace_id, conversation_id) or self._new(
                workspace_id, conversation_id, question
            )
            record.turns.extend(
                [
                    ConversationTurn(role="user", content=question.strip()),
                    ConversationTurn(
                        role="assistant",
                        content=answer.text.strip(),
                        evidence=answer.evidence,
                    ),
                ]
            )
            record.evidence_pool = self._merge_evidence(record.evidence_pool, answer.evidence)
            self._save(record)

    def get_public(self, workspace_id: str, conversation_id: str) -> dict:
        with _PERSISTENCE_LOCK:
            record = self._require(workspace_id, conversation_id)
            return self._public_payload(record, include_turns=True)

    def list_public(self, workspace_id: str) -> list[dict]:
        directory = self._directory(workspace_id)
        if not directory.is_dir():
            return []
        records: list[ConversationRecord] = []
        with _PERSISTENCE_LOCK:
            for path in directory.glob("*.json"):
                try:
                    records.append(
                        ConversationRecord.model_validate_json(path.read_text(encoding="utf-8"))
                    )
                except (OSError, ValueError):
                    continue
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return [self._public_payload(record, include_turns=False) for record in records]

    def delete_all(self, workspace_id: str) -> None:
        directory = self._directory(workspace_id)
        if not directory.is_dir():
            return
        with _PERSISTENCE_LOCK:
            for path in directory.glob("*.json"):
                path.unlink(missing_ok=True)

    def _compact_if_needed(
        self,
        workspace_id: str,
        conversation_id: str,
        question: str,
    ) -> bool:
        with _PERSISTENCE_LOCK:
            record = self._require(workspace_id, conversation_id)
            base_compacted_count = record.compacted_turn_count
            pending = record.turns[base_compacted_count:]
            context = "\n".join(turn.content for turn in pending)
            size = self._estimate_tokens(record.summary + context + question)
            if size <= self.context_budget_tokens or len(pending) <= self.recent_turns:
                return False
            compactable = pending[: -self.recent_turns]
            existing_summary = record.summary
        transcript = "\n".join(f"{turn.role}: {turn.content}" for turn in compactable)
        model = self.language_model() if callable(self.language_model) else self.language_model
        try:
            result = model.run(
                instructions=(
                    "Compress the older part of a research conversation into durable memory. "
                    "Preserve user goals, source-specific facts, decisions, unresolved questions, "
                    "and citation provenance. Remove repetition. Do not answer the new question."
                ),
                user_input=(
                    f"Existing memory:\n{existing_summary or '(none)'}\n\n"
                    f"Older turns:\n{transcript}\n\nUpcoming question:\n{question}"
                ),
            )
            summary = result.text.strip()
        except Exception:
            summary = self._extractive_summary(existing_summary, compactable)
        if not summary:
            summary = self._extractive_summary(existing_summary, compactable)
        with _PERSISTENCE_LOCK:
            record = self._require(workspace_id, conversation_id)
            if record.compacted_turn_count != base_compacted_count:
                return False
            record.summary = summary[:8_000]
            record.compacted_turn_count += len(compactable)
            self._save(record)
        return True

    @staticmethod
    def _extractive_summary(
        existing_summary: str,
        turns: Sequence[ConversationTurn],
    ) -> str:
        blocks = [existing_summary.strip()] if existing_summary.strip() else []
        blocks.extend(
            f"{turn.role}: {re.sub(r'\s+', ' ', turn.content).strip()[:700]}" for turn in turns
        )
        return "\n".join(blocks)[-8_000:]

    def _relevant_evidence(
        self,
        record: ConversationRecord,
        question: str,
    ) -> list[Evidence]:
        if not record.evidence_pool:
            return []
        recent_context = " ".join(turn.content for turn in record.turns[-4:])
        query_terms = self._terms(f"{record.summary} {recent_context} {question}")
        ranked: list[tuple[float, int, Evidence]] = []
        for index, item in enumerate(record.evidence_pool):
            overlap = len(query_terms & self._terms(f"{item.section} {item.text}"))
            if query_terms and overlap == 0:
                continue
            score = overlap + max(0.0, item.similarity) * 0.1 + index / 100_000
            ranked.append((score, index, item))
        if not ranked and len(self._terms(question)) < 3:
            ranked = [
                (index / 100_000, index, item) for index, item in enumerate(record.evidence_pool)
            ]
        ranked.sort(reverse=True, key=lambda value: (value[0], value[1]))
        selected: list[Evidence] = []
        used = 0
        for _, _, item in ranked:
            size = len(item.text)
            if selected and used + size > self.evidence_context_chars:
                continue
            selected.append(item)
            used += size
            if used >= self.evidence_context_chars:
                break
        return selected

    def _merge_evidence(
        self,
        existing: Sequence[Evidence],
        current: Sequence[Evidence],
    ) -> list[Evidence]:
        ordered: dict[tuple[str, str, int, int], Evidence] = {}
        for item in [*existing, *current]:
            key = (item.document_id or item.paper_id, item.chunk_id, item.page_start, item.page_end)
            ordered.pop(key, None)
            ordered[key] = item
        kept: list[Evidence] = []
        used = 0
        for item in reversed(ordered.values()):
            size = len(item.text)
            if kept and used + size > self.evidence_memory_chars:
                continue
            kept.append(item)
            used += size
            if used >= self.evidence_memory_chars:
                break
        kept.reverse()
        return kept

    def _new(
        self,
        workspace_id: str,
        conversation_id: str,
        question: str,
    ) -> ConversationRecord:
        self._validate_workspace_id(workspace_id)
        self._validate_conversation_id(conversation_id)
        title = re.sub(r"\s+", " ", question).strip()[:80] or "New conversation"
        return ConversationRecord(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            title=title,
        )

    def _load(self, workspace_id: str, conversation_id: str) -> ConversationRecord | None:
        path = self._path(workspace_id, conversation_id)
        if not path.is_file():
            return None
        return ConversationRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _require(self, workspace_id: str, conversation_id: str) -> ConversationRecord:
        record = self._load(workspace_id, conversation_id)
        if record is None:
            raise KeyError(f"Unknown conversation: {conversation_id}")
        return record

    def _save(self, record: ConversationRecord) -> None:
        updated = record.model_copy(update={"updated_at": utc_now()})
        path = self._path(updated.workspace_id, updated.conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _path(self, workspace_id: str, conversation_id: str) -> Path:
        return self._directory(workspace_id) / (
            f"{self._validate_conversation_id(conversation_id)}.json"
        )

    def _directory(self, workspace_id: str) -> Path:
        safe_workspace = self._validate_workspace_id(workspace_id)
        path = (self.root / safe_workspace / "conversations").resolve()
        if self.root not in path.parents:
            raise ValueError("Conversation path escapes storage root")
        return path

    @staticmethod
    def _validate_workspace_id(value: str) -> str:
        if not _SAFE_WORKSPACE_ID.fullmatch(value):
            raise ValueError("Invalid workspace id")
        return value

    @staticmethod
    def _validate_conversation_id(value: str) -> str:
        if not _SAFE_CONVERSATION_ID.fullmatch(value):
            raise ValueError("Invalid conversation id")
        return value

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 3)

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {term.casefold() for term in _WORD.findall(text) if len(term) > 1}

    @staticmethod
    def _public_payload(record: ConversationRecord, *, include_turns: bool) -> dict:
        payload = {
            "id": record.conversation_id,
            "workspace_id": record.workspace_id,
            "title": record.title,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "turn_count": len(record.turns),
        }
        if include_turns:
            payload["turns"] = [turn.model_dump(mode="json") for turn in record.turns]
        return payload
