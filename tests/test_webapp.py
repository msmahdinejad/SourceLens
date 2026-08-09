import pytest
from pydantic import ValidationError

from paperlens.domain import ResearchAnswer, ToolCallRecord
from paperlens.studio import StudioArtifact
from paperlens.webapp import (
    ChatRequest,
    ToolRequest,
    _answer_artifacts,
    _studio_payload,
)


def test_application_exposes_stable_domain_router_seams() -> None:
    from paperlens.web.application import ApplicationContainer
    from paperlens.webapp import app

    assert isinstance(app.state.container, ApplicationContainer)
    paths = app.openapi()["paths"]
    assert paths["/api/workspaces"]["get"]["tags"] == ["workspaces"]
    assert paths["/api/workspaces/{workspace_id}/studio/artifacts"]["post"]["tags"] == [
        "studio"
    ]
    assert paths["/api/chat"]["post"]["tags"] == ["chat"]
    assert paths["/api/provider-settings"]["get"]["tags"] == ["providers"]
    assert paths["/api/traces"]["get"]["tags"] == ["observability"]


def test_extraction_artifacts_use_default_profile_when_model_passes_null(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "results" / "representative_models"
    base.mkdir(parents=True)
    (base / "experimental_results.csv").write_text("model,score\nfixture,1\n", encoding="utf-8")
    monkeypatch.setattr("paperlens.webapp.OUTPUTS_ROOT", tmp_path)
    answer = ResearchAnswer(
        mode="rag",
        text="Extracted results",
        tool_calls=[
            ToolCallRecord(
                name="extract_experimental_results",
                arguments={"profile": None},
                output=('{"csv_path": "' + (base / "experimental_results.csv").as_posix() + '"}'),
            )
        ],
    )

    artifacts = _answer_artifacts(answer)

    assert artifacts
    assert len(artifacts) == 1
    assert "representative_models" in artifacts[0]["url"]


def test_chat_history_is_bounded_and_role_validated() -> None:
    request = ChatRequest(
        question="Why?",
        history=[{"role": "user", "content": "About HomeCharge"}],
    )
    assert request.history[0].role == "user"

    with pytest.raises(ValidationError):
        ChatRequest(
            question="Why?",
            history=[{"role": "user", "content": str(index)} for index in range(21)],
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ChatRequest, {"question": "   "}),
        (ChatRequest, {"question": "Valid question", "unexpected": True}),
        (
            ChatRequest,
            {
                "question": "Valid question",
                "history": [{"role": "user", "content": "   "}],
            },
        ),
        (ToolRequest, {"arguments": {}, "unexpected": True}),
    ],
)
def test_request_models_reject_ambiguous_or_unknown_input(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_request_models_normalize_surrounding_whitespace() -> None:
    request = ChatRequest(
        question="  What is HomeCharge?  ",
        history=[{"role": "user", "content": "  Previous question  "}],
    )

    assert request.question == "What is HomeCharge?"
    assert request.history[0].content == "Previous question"


def test_studio_payload_renders_markdown_in_structured_fields() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact-markdown",
        workspace_id="workspace-markdown",
        artifact_type="flashcards",
        title="Policy review",
        status="ready",
        document_ids=["document-1"],
        data={
            "cards": [
                {
                    "front": r"What does **$\epsilon$-greedy** mean?",
                    "back": "- Explore\n- Exploit",
                    "citations": [],
                }
            ]
        },
    )

    payload = _studio_payload(artifact)
    rendered_card = payload["rendered_data"]["cards"][0]

    assert "<strong>" in rendered_card["front"]["inline"]
    assert "<math" in rendered_card["front"]["inline"]
    assert "<ul>" in rendered_card["back"]["block"]


@pytest.mark.parametrize(
    ("artifact_type", "data", "expected_fields"),
    [
        (
            "slide_deck",
            {"slides": [{"title": "Opening"}], "pptx_filename": "deck.pptx"},
            ("presentation_url", "download_url"),
        ),
        (
            "video_overview",
            {
                "scenes": [{"title": "Opening", "narration": "Introduction"}],
                "video_filename": "overview.mp4",
                "captions_filename": "captions.srt",
                "timeline_filename": "timeline.json",
            },
            ("video_url", "captions_url", "timeline_url", "download_url"),
        ),
    ],
)
def test_visual_studio_payload_exposes_downloadable_assets(
    artifact_type,
    data,
    expected_fields,
) -> None:
    artifact = StudioArtifact(
        artifact_id="visual-output",
        workspace_id="workspace-visual",
        artifact_type=artifact_type,
        title="Visual output",
        status="ready",
        document_ids=["document-1"],
        data=data,
    )

    payload = _studio_payload(artifact)

    for field in expected_fields:
        assert payload[field]
        assert "visual-output" in payload[field]
