from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class ChatCase:
    case_id: str
    question: str
    expected_mode: str
    expected_execution_path: str | tuple[str, ...]
    expected_tool: str | None = None
    minimum_evidence_documents: int = 0
    history: tuple[tuple[str, str], ...] = ()


CORE_CASES = (
    ChatCase(
        "grounded-overview",
        "How can retail fast charging make an everyday shopping stop useful to EV drivers?",
        "rag",
        ("rag", "hybrid", "agent_tool"),
        minimum_evidence_documents=1,
    ),
    ChatCase(
        "cross-source-rag-fa",
        "منابع انتخاب‌شده درباره نقش شارژ سریع فروشگاهی در سفر روزمره چه می‌گویند؟",
        "rag",
        ("rag", "hybrid", "agent_tool"),
        minimum_evidence_documents=2,
    ),
    ChatCase(
        "source-comparison",
        "Compare the selected studies' methods, evidence, limitations, and implications.",
        "rag",
        ("rag", "hybrid", "agent_tool"),
        minimum_evidence_documents=2,
    ),
    ChatCase(
        "general-fallback",
        "What is the capital of France?",
        "general_fallback",
        "general_fallback",
    ),
    ChatCase(
        "follow-up-rag",
        "Which evidence best supports that conclusion?",
        "rag",
        ("rag", "hybrid", "agent_tool"),
        minimum_evidence_documents=1,
        history=(
            (
                "user",
                "How can retail charging benefit both drivers and stores?",
            ),
        ),
    ),
)

DEEP_CASES = (
    ChatCase(
        "mini-survey",
        "Create a concise English mini-survey across the selected documents.",
        "rag",
        "agent_tool",
        expected_tool="generate_survey",
        minimum_evidence_documents=2,
    ),
    ChatCase(
        "research-gap",
        "Analyze research gaps and future work across the selected documents.",
        "rag",
        "agent_tool",
        expected_tool="find_research_gap",
        minimum_evidence_documents=2,
    ),
    ChatCase(
        "limitations",
        "Compare limitations and uncertainty across the selected documents.",
        "rag",
        "agent_tool",
        expected_tool="compare_limitations",
        minimum_evidence_documents=2,
    ),
)


def _chat(
    client: httpx.Client,
    case: ChatCase,
    workspace_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.post(
        "/api/chat",
        json={
            "question": case.question,
            "history": [
                {"role": role, "content": content} for role, content in case.history
            ],
            "workspace_id": workspace_id,
        },
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    payload = response.json()
    tools = [call["name"] for call in payload.get("tool_calls", [])]
    evidence_documents = sorted(
        {item["paper_id"] for item in payload.get("evidence", [])}
    )
    artifacts = payload.get("artifacts", [])

    assert payload["mode"] == case.expected_mode
    allowed_paths = (
        (case.expected_execution_path,)
        if isinstance(case.expected_execution_path, str)
        else case.expected_execution_path
    )
    assert payload["execution_path"] in allowed_paths
    if case.expected_tool:
        assert case.expected_tool in tools
    assert len(evidence_documents) >= case.minimum_evidence_documents
    if payload["mode"] == "rag" and not tools:
        assert re.search(r"\[E\d+]", payload["text"])
    if payload["mode"] == "general_fallback":
        assert payload["evidence"] == []
    if payload["execution_path"] in {"agent_tool", "hybrid"}:
        assert len(payload["text"].strip()) >= 100
    assert not re.search(r"[\u0e00-\u0e7f\u3400-\u9fff]", payload["text"])

    for artifact in artifacts:
        artifact_response = client.get(artifact["url"])
        assert artifact_response.status_code == 200
        assert artifact_response.content

    return {
        **asdict(case),
        "status": "passed",
        "duration_ms": duration_ms,
        "mode": payload["mode"],
        "execution_path": payload["execution_path"],
        "tools": tools,
        "evidence_documents": evidence_documents,
        "evidence_count": len(payload.get("evidence", [])),
        "artifact_count": len(artifacts),
        "trace_id": payload["trace_id"],
    }


def _platform_checks(client: httpx.Client, workspace_id: str) -> dict[str, Any]:
    status = client.get("/api/status")
    status.raise_for_status()
    status_payload = status.json()
    assert status_payload["ready"] is True
    assert status_payload["runtime_available"] is True

    vector = client.get(f"/api/workspaces/{workspace_id}/vector-store")
    vector.raise_for_status()
    vector_payload = vector.json()
    assert vector_payload["document_count"] >= 2
    assert vector_payload["chunk_count"] > 0

    documents = client.get(f"/api/workspaces/{workspace_id}/documents")
    documents.raise_for_status()
    document_payload = documents.json()
    assert len(document_payload) >= 2
    for document in document_payload:
        pdf = client.get(
            f"/api/workspaces/{workspace_id}/documents/{document['document_id']}/pdf"
        )
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")

    return {
        "ready": status_payload["ready"],
        "document_count": vector_payload["document_count"],
        "chunk_count": vector_payload["chunk_count"],
        "llm": status_payload["llm"],
        "embedding": status_payload["embedding"],
        "embedding_dimensions": status_payload["embedding_dimensions"],
        "workspace_id": vector_payload["workspace_id"],
        "collection_name": vector_payload["collection_name"],
    }


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Run live SourceLens acceptance checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run only this case id; may be repeated.",
    )
    parser.add_argument("--request-timeout", type=float, default=240.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/evaluation/final_acceptance.json"),
    )
    args = parser.parse_args()

    cases = CORE_CASES + (DEEP_CASES if args.deep else ())
    if args.case_ids:
        requested = set(args.case_ids)
        known = {case.case_id for case in cases}
        unknown = requested - known
        if unknown:
            parser.error(f"Unknown case id(s): {', '.join(sorted(unknown))}")
        cases = tuple(case for case in cases if case.case_id in requested)

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url, timeout=args.request_timeout) as client:
        platform = _platform_checks(client, args.workspace_id)
        for case in cases:
            try:
                results.append(_chat(client, case, args.workspace_id))
            except Exception as error:  # noqa: BLE001 - report every acceptance case
                results.append(
                    {
                        **asdict(case),
                        "status": "failed",
                        "error": f"{type(error).__name__}: {str(error)[:500]}",
                    }
                )

    passed_count = sum(result["status"] == "passed" for result in results)
    failed_count = len(results) - passed_count
    report = {
        "status": "passed" if failed_count == 0 else "failed",
        "executed_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "deep": args.deep,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "platform": platform,
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failed_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
