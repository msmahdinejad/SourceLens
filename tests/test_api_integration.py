from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import paperlens.webapp as webapp
from paperlens.config import Settings
from paperlens.conversations import ConversationContext
from paperlens.domain import Evidence, ResearchAnswer, ToolCallRecord
from paperlens.voice import Transcription
from paperlens.workspaces import WorkspaceStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    store = WorkspaceStore(tmp_path / "workspaces")
    store.ensure_default_workspace()
    store.ensure_workspace("demo", "Charge While You Shop")
    monkeypatch.setattr(webapp, "workspace_store", lambda: store)
    with TestClient(webapp.app, raise_server_exceptions=False) as current:
        yield current


def _evidence() -> Evidence:
    return Evidence(
        chunk_id="ev-retail-1",
        paper_id="ev-charging-guidance",
        paper_title="Public EV Charging Infrastructure Guidance",
        section="Retail charging",
        section_type="recommendations",
        page_start=6,
        page_end=6,
        text="Public guidance links charging power to expected dwell time and site purpose.",
        similarity=0.91,
    )


def test_chat_endpoint_returns_grounded_payload_and_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = ResearchAnswer(
        mode="rag",
        execution_path="rag",
        text="Charging power should match expected dwell time and site purpose [E1].",
        evidence=[_evidence()],
    )
    monkeypatch.setattr(webapp, "_ask_locked", lambda question, history: answer)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: (
            {"active_model": "test-model", "active_slot": 1, "last_failover": False},
            {"active_slot": 1, "credential_count": 1, "last_backend": "primary"},
        ),
    )

    response = client.post(
        "/api/chat",
        json={
            "question": "How should charging power match a retail stop?",
            "history": [{"role": "user", "content": "Tell me about retail EV charging."}],
            "workspace_id": "demo",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "rag"
    assert payload["execution_path"] == "rag"
    assert payload["evidence"][0]["chunk_id"] == "ev-retail-1"
    assert "[E1]" in payload["text"]
    assert "<p>" in payload["html"]
    assert len(payload["trace_id"]) == 12
    trace = client.get(f"/api/traces/{payload['trace_id']}")
    assert trace.status_code == 200
    assert trace.json()["status"] == "completed"


def test_chat_endpoint_uses_server_conversation_memory_and_records_the_new_exchange(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = ResearchAnswer(
        mode="rag",
        text="The follow-up uses retained evidence [E1].",
        evidence=[_evidence()],
    )

    class Memory:
        recorded = None

        @staticmethod
        def context_for(workspace_id, conversation_id, question, fallback_history=()):
            assert workspace_id == "demo"
            assert conversation_id == "chat-12345678"
            return ConversationContext(
                history=[("user", "Earlier question"), ("assistant", "Earlier answer")],
                summary="The user is studying retail EV charging.",
                evidence=[_evidence()],
            )

        def record_exchange(self, workspace_id, conversation_id, question, result):
            self.recorded = (workspace_id, conversation_id, question, result.text)

    memory = Memory()
    monkeypatch.setattr(webapp, "conversation_memory", lambda: memory)

    def ask(question, history, summary, prior_evidence):
        assert history[0].content == "Earlier question"
        assert summary == "The user is studying retail EV charging."
        assert prior_evidence[0].chunk_id == "ev-retail-1"
        return answer

    monkeypatch.setattr(webapp, "_ask_locked", ask)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: ({"active_model": "test"}, {"active_slot": 1}),
    )

    response = client.post(
        "/api/chat",
        json={
            "question": "Why does that matter?",
            "workspace_id": "demo",
            "conversation_id": "chat-12345678",
        },
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] == "chat-12345678"
    assert memory.recorded == (
        "demo",
        "chat-12345678",
        "Why does that matter?",
        answer.text,
    )


def test_chat_endpoint_rejects_invalid_request_shapes(client: TestClient) -> None:
    invalid_payloads = [
        {},
        {"question": "   "},
        {"question": "Valid question", "unexpected": True},
        {"question": "Valid question", "history": [{"role": "system", "content": "x"}]},
        {"question": "x" * 4001},
    ]

    for payload in invalid_payloads:
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 422, payload


def test_chat_endpoint_hides_internal_failures_but_keeps_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(question: str, history: list[Any]) -> ResearchAnswer:
        raise RuntimeError("provider secret diagnostic")

    monkeypatch.setattr(webapp, "_ask_locked", fail)

    response = client.post(
        "/api/chat",
        json={"question": "Explain charging interoperability", "workspace_id": "demo"},
    )

    assert response.status_code == 502
    assert "provider secret diagnostic" not in response.text
    trace_id = response.json()["detail"].rsplit(":", 1)[-1].strip()
    trace = client.get(f"/api/traces/{trace_id}")
    assert trace.status_code == 200
    assert trace.json()["status"] == "failed"


def test_chat_endpoint_never_serializes_private_tool_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = ResearchAnswer(
        mode="rag",
        execution_path="agent_tool",
        text="The verified comparison is ready.",
        evidence=[_evidence()],
        tool_calls=[
            ToolCallRecord(
                name="compare_results",
                arguments={},
                output='{"internal_secret": "must-not-leak"}',
            )
        ],
    )
    monkeypatch.setattr(webapp, "_ask_locked", lambda question, history: answer)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: (
            {"active_model": "test", "active_slot": 1, "last_failover": False},
            {"active_slot": 1, "credential_count": 1, "last_backend": "primary"},
        ),
    )

    response = client.post(
        "/api/chat",
        json={"question": "Compare the EV charging sources", "workspace_id": "demo"},
    )

    assert response.status_code == 200
    assert response.json()["tool_calls"] == [{"name": "compare_results", "arguments": {}}]
    assert "must-not-leak" not in response.text


def test_trace_redacts_configured_credentials_from_internal_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "trace-secret-credential-123"
    current = Settings(
        _env_file=None,
        llm_model="test",
        llm_api_key=secret,
        gemini_api_keys="",
        google_api_keys="",
        groq_chat_api_keys="",
        mistral_api_keys="",
        nvidia_api_keys="",
        embedding_api_key="",
        groq_api_key="",
    )
    monkeypatch.setattr(webapp, "settings", lambda: current)

    def fail(question: str, history: list[Any]) -> ResearchAnswer:
        raise RuntimeError(f"Authorization failed for {secret}")

    monkeypatch.setattr(webapp, "_ask_locked", fail)

    response = client.post(
        "/api/chat",
        json={"question": "Explain charging interoperability", "workspace_id": "demo"},
    )
    trace_id = response.json()["detail"].rsplit(":", 1)[-1].strip()
    trace = client.get(f"/api/traces/{trace_id}")

    assert response.status_code == 502
    assert secret not in trace.text
    assert "[REDACTED]" in trace.json()["error"]


def test_chat_endpoint_accepts_independent_requests_concurrently(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(4)

    def answer(question: str, history: list[Any]) -> ResearchAnswer:
        barrier.wait(timeout=5)
        return ResearchAnswer(
            mode="rag",
            text=f"Answer for {question} [E1].",
            evidence=[_evidence()],
        )

    monkeypatch.setattr(webapp, "_ask_locked", answer)
    monkeypatch.setattr(
        webapp,
        "_runtime_failover_status",
        lambda: (
            {"active_model": "test", "active_slot": 1, "last_failover": False},
            {"active_slot": 1, "credential_count": 1, "last_backend": "primary"},
        ),
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(
            executor.map(
                lambda index: client.post(
                    "/api/chat",
                    json={
                        "question": f"Question number {index}",
                        "workspace_id": "demo",
                    },
                ),
                range(4),
            )
        )

    assert [response.status_code for response in responses] == [200, 200, 200, 200]


def test_direct_tool_endpoint_validates_name_and_arguments(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown = client.post("/api/tools/delete_everything", json={"arguments": {}})
    assert unknown.status_code == 404

    def reject(tool_name: str, arguments: dict[str, Any]) -> str:
        raise ValueError("Unknown argument: unexpected")

    monkeypatch.setattr(
        webapp,
        "_execute_workspace_tool_locked",
        lambda tool_name, arguments, workspace_id: reject(tool_name, arguments),
    )
    invalid = client.post(
        "/api/tools/generate_survey",
        json={"arguments": {"unexpected": True}, "workspace_id": "demo"},
    )

    assert invalid.status_code == 422
    assert "Invalid tool arguments" in invalid.json()["detail"]


def test_direct_tool_endpoint_returns_report_preview_and_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = webapp.OUTPUTS_ROOT / "test-api" / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "# Verified report\n\nBoth EV charging sources are covered.",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        webapp,
        "_execute_workspace_tool_locked",
        lambda tool_name, arguments, workspace_id: (
            '{"path": "' + report.as_posix() + '", "evidence": []}'
        ),
    )
    try:
        response = client.post(
            "/api/tools/generate_survey",
            json={"arguments": {"language": "en"}, "workspace_id": "demo"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["path_url"] == "/artifacts/test-api/report.md"
        assert "<h1>Verified report</h1>" in payload["report_html"]
        assert client.get(payload["path_url"]).status_code == 200
        assert client.get(f"/api/traces/{payload['trace_id']}").status_code == 200
    finally:
        report.unlink(missing_ok=True)
        report.parent.rmdir()


def test_removed_global_demo_routes_use_workspace_apis_instead(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(webapp, "OUTPUTS_ROOT", tmp_path / "outputs")
    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "SourceLens" in homepage.text

    assert client.get("/api/papers").status_code == 404
    assert client.get("/api/papers/not-a-paper/pdf").status_code == 404
    assert client.get("/api/vector-store").status_code == 404
    assert client.get("/api/evaluation").status_code == 404

    assert client.get("/api/results").status_code == 404


def test_status_remains_available_when_runtime_is_not_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    current = Settings(
        _env_file=None,
        llm_api_key="",
        gemini_api_keys="",
        google_api_keys="",
        groq_chat_api_keys="",
        mistral_api_keys="",
        nvidia_api_keys="",
        embedding_api_key="",
        groq_api_key="",
        llm_routing_enabled=False,
        chroma_path=tmp_path / "empty-chroma",
    )

    class EmptyCollection:
        @staticmethod
        def count() -> int:
            return 0

    monkeypatch.setattr(webapp, "settings", lambda: current)
    monkeypatch.setattr(webapp, "_collection", lambda configured=None: EmptyCollection())

    def unavailable_assistant():
        raise ValueError("LLM_API_KEY is not configured")

    monkeypatch.setattr(webapp, "assistant", unavailable_assistant)

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert response.json()["runtime_available"] is False


def test_status_aggregates_chunks_from_workspace_documents(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces = [
        SimpleNamespace(
            documents=[
                SimpleNamespace(status="ready", chunk_count=12),
                SimpleNamespace(status="processing", chunk_count=4),
            ]
        )
    ]
    monkeypatch.setattr(
        webapp,
        "workspace_store",
        lambda: SimpleNamespace(list=lambda: workspaces),
    )

    payload = client.get("/api/status").json()

    assert payload["document_count"] == 2
    assert payload["chunk_count"] == 12


def test_chromadb_server_api_is_not_exposed(client: TestClient) -> None:
    response = client.post(
        "/api/v2/tenants/default/databases/default/collections",
        json={"name": "untrusted", "configuration": {"embedding_function": {}}},
    )

    assert response.status_code in {404, 405}


def test_vector_and_trace_query_bounds_are_enforced(client: TestClient) -> None:
    assert client.get("/api/vector-store/chunks?limit=0").status_code == 404
    assert client.get("/api/vector-store/chunks?limit=41").status_code == 404
    assert client.get("/api/traces?limit=0").status_code == 422
    assert client.get("/api/traces?limit=81").status_code == 422
    assert client.get("/api/traces/not-found").status_code == 404


def test_removed_global_rebuild_endpoint_is_not_exposed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = client.post("/api/vector-store/rebuild")

    assert response.status_code in {404, 405}


def test_transcription_endpoint_validates_media_and_returns_metadata(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsupported = client.post(
        "/api/transcribe",
        files={"audio": ("voice.txt", b"not audio", "text/plain")},
    )
    assert unsupported.status_code == 415

    empty = client.post(
        "/api/transcribe",
        files={"audio": ("voice.wav", b"", "audio/wav")},
    )
    assert empty.status_code == 400

    monkeypatch.setattr(
        webapp,
        "_transcribe_locked",
        lambda content, suffix: Transcription(
            text="Which source discusses interoperability?",
            language="en",
            language_probability=0.99,
            duration_seconds=1.5,
            provider="groq",
            model="whisper-large-v3",
        ),
    )
    accepted = client.post(
        "/api/transcribe",
        files={"audio": ("voice.wav", b"RIFF-valid-enough", "audio/wav")},
    )

    assert accepted.status_code == 200
    assert accepted.json()["provider"] == "groq"
    assert accepted.json()["text"] == "Which source discusses interoperability?"
    assert len(accepted.json()["trace_id"]) == 12


def test_transcription_endpoint_rejects_payload_above_25_mb(client: TestClient) -> None:
    response = client.post(
        "/api/transcribe",
        files={
            "audio": (
                "too-large.wav",
                b"x" * (25 * 1024 * 1024 + 1),
                "audio/wav",
            )
        },
    )

    assert response.status_code == 413


def test_artifact_mount_does_not_serve_files_outside_outputs(client: TestClient) -> None:
    response = client.get("/artifacts/%2e%2e/.env")

    assert response.status_code in {400, 404}
    assert "LLM_API_KEY" not in response.text
