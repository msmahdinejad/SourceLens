from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from paperlens.domain import Evidence
from paperlens.language_models.base import GenerationResult, ToolSpec
from paperlens.studio import (
    StudioArtifact,
    StudioArtifactStore,
    StudioGenerationRequest,
    StudioService,
    normalize_studio_options,
    parse_legacy_flashcards,
    studio_workflow_registry,
)
from paperlens.workspaces import WorkspaceStore


def test_studio_accepts_every_output_offered_by_the_product_interface() -> None:
    for artifact_type in (
        "report",
        "summary",
        "briefing",
        "study_guide",
        "faq",
        "quiz",
        "chart",
        "timeline",
        "flashcards",
        "comparison",
        "concept_map",
        "mini_survey",
        "research_gap",
        "infographic",
        "data_table",
    ):
        request = StudioGenerationRequest(
            workspace_id="workspace",
            artifact_type=artifact_type,
            document_ids=["document"],
        )
        assert request.artifact_type == artifact_type


def test_studio_creation_catalog_combines_notebook_and_existing_actions() -> None:
    from paperlens.studio import studio_creation_catalog

    catalog = studio_creation_catalog()

    assert [item["artifact_type"] for item in catalog] == [
        "audio_overview",
        "report",
        "concept_map",
        "flashcards",
        "quiz",
        "infographic",
        "data_table",
        "chart",
        "timeline",
        "comparison",
        "research_gap",
        "slide_deck",
        "video_overview",
    ]
    assert all(item["defaults"] for item in catalog)
    assert all(len(item["fields"]) >= 6 for item in catalog)
    report = catalog[1]
    assert "custom" in report["fields"]["report_format"]["choices"]
    assert report["fields"]["detail_level"]["choices"] == [
        "concise",
        "standard",
        "detailed",
        "comprehensive",
    ]
    assert "custom_report_type" in report["fields"]


def test_each_studio_workflow_has_typed_request_plan_and_result_models() -> None:
    from paperlens.studio import studio_creation_catalog

    registry = studio_workflow_registry()

    for entry in studio_creation_catalog():
        artifact_type = entry["artifact_type"]
        models = registry.models(artifact_type)
        request = models.request.model_validate(
            {
                "artifact_type": artifact_type,
                "workspace_id": "workspace-1",
                "document_ids": ["document-1"],
                "options": entry["defaults"],
            }
        )
        plan = registry.plan(artifact_type)
        result = models.result.model_validate(
            {"artifact_type": artifact_type, "title": "Result", "data": {}}
        )

        assert request.artifact_type == artifact_type
        assert plan.artifact_type == artifact_type
        assert result.artifact_type == artifact_type
        assert models.request.__name__.startswith(artifact_type.title().replace("_", ""))


def test_studio_options_apply_defaults_and_reject_unknown_customization() -> None:
    options = normalize_studio_options("quiz", {"question_count": 18})

    assert options["question_count"] == 18
    assert options["difficulty"] == "intermediate"
    assert options["include_explanations"] is True
    assert options["evidence_depth"] == "balanced"

    with pytest.raises(ValueError, match="Unsupported Studio option"):
        normalize_studio_options("quiz", {"unsupported": True})

    with pytest.raises(ValueError, match="custom report brief"):
        normalize_studio_options("report", {"report_format": "custom"})


def test_video_options_use_seconds_and_force_noncommercial_music_policy() -> None:
    automatic = normalize_studio_options("video_overview", {})
    migrated = normalize_studio_options(
        "video_overview",
        {"duration_minutes": 2, "music_usage": "commercial"},
    )

    assert automatic["duration_seconds"] == 180
    assert automatic["scene_count_strategy"] == "auto"
    assert automatic["presenter_name"] == ""
    assert automatic["organization"] == ""
    assert "duration_minutes" not in automatic
    assert "music_usage" not in automatic
    assert migrated["duration_seconds"] == 120
    assert "music_usage" not in migrated
    with pytest.raises(ValueError, match="outside the allowed range"):
        normalize_studio_options("video_overview", {"duration_seconds": 29})


def test_video_normalizer_treats_a_single_media_query_as_one_query() -> None:
    payload = {
        "title": "Overview",
        "scenes": [
            {
                "title": "First",
                "narration": "First scene narration.",
                "media_queries": "research team at work",
            },
            {
                "title": "Second",
                "narration": "Second scene narration.",
                "media_query": "data visualization",
            },
        ],
    }

    normalized = StudioService._normalize_video_overview(payload)

    assert normalized["scenes"][0]["media_queries"] == ["research team at work"]
    assert normalized["scenes"][1]["media_queries"] == ["data visualization"]


def test_video_normalizer_preserves_only_safe_semantic_direction() -> None:
    normalized = StudioService._normalize_video_overview(
        {
            "title": "Overview",
            "scenes": [
                {
                    "title": "Subject",
                    "narration": "Explain the subject.",
                    "visual": {
                        "hierarchy": "subject",
                        "spacing": "airy",
                        "emphasis": "title",
                    },
                },
                {
                    "title": "Invalid",
                    "narration": "Use safe defaults.",
                    "visual": {
                        "hierarchy": "x=9999",
                        "spacing": "overflow",
                        "emphasis": "tiny",
                    },
                },
            ],
        }
    )

    assert normalized["scenes"][0]["visual"]["hierarchy"] == "subject"
    assert normalized["scenes"][0]["visual"]["spacing"] == "airy"
    assert normalized["scenes"][0]["visual"]["emphasis"] == "title"
    assert normalized["scenes"][1]["visual"]["hierarchy"] == "balanced"
    assert normalized["scenes"][1]["visual"]["spacing"] == "balanced"
    assert normalized["scenes"][1]["visual"]["emphasis"] == "balanced"


def test_deck_normalizer_repairs_a_case_study_with_only_one_media_query() -> None:
    normalized = StudioService._normalize_slide_deck(
        {
            "title": "Deck",
            "slides": [
                {
                    "title": "Grounded case",
                    "layout": "case_study",
                    "body": "A substantive source-grounded case explanation for the audience.",
                    "image_queries": ["reinforcement learning robot laboratory"],
                    "citations": ["S1"],
                }
            ],
        }
    )

    assert normalized["slides"][0]["layout"] == "editorial_split"
    assert normalized["slides"][0]["media_required"] is True


def test_deck_normalizer_repairs_an_unsupported_chart_layout_without_faking_data() -> None:
    normalized = StudioService._normalize_slide_deck(
        {
            "title": "Deck",
            "slides": [
                {
                    "title": "Evidence without a numeric series",
                    "layout": "chart_focus",
                    "body": "The source supports this explanation but no aligned chart values.",
                    "citations": ["S1"],
                }
            ],
        }
    )

    assert normalized["slides"][0]["layout"] == "big_statement"
    assert normalized["slides"][0]["chart"] == {}


def test_deck_normalizer_accepts_a_substantive_metrics_slide() -> None:
    normalized = StudioService._normalize_slide_deck(
        {
            "title": "Deck",
            "slides": [
                {
                    "title": "Measured protection",
                    "layout": "metrics_dashboard",
                    "metrics": [
                        {"value": "97%", "label": "wave-energy attenuation in the study"},
                        {"value": "$1.8M", "label": "modeled annual avoided losses"},
                    ],
                    "citations": ["S1"],
                }
            ],
        }
    )

    assert normalized["slides"][0]["metrics"][0]["value"] == "97%"


def test_deck_normalizer_accepts_common_chart_dataset_shape() -> None:
    normalized = StudioService._normalize_slide_deck(
        {
            "title": "Deck",
            "slides": [
                {
                    "title": "Why resilience matters",
                    "layout": "big_statement",
                    "body": "Coastal communities face material flood exposure.",
                    "citations": ["S1"],
                },
                {
                    "title": "Restoration strategy",
                    "layout": "big_statement",
                    "body": "Living reefs can alter nearshore wave conditions.",
                    "citations": ["S1"],
                },
                {
                    "title": "Modeled flood losses",
                    "layout": "chart_focus",
                    "chart": {
                        "type": "bar",
                        "labels": ["Baseline", "Restored"],
                        "datasets": [{"label": "Annual loss", "data": [4.2, 1.8]}],
                        "citations": ["S1"],
                    },
                    "citations": ["S1"],
                }
            ],
        }
    )

    assert normalized["slides"][2]["chart"]["series"] == [
        {"name": "Annual loss", "values": [4.2, 1.8]}
    ]


def test_infographic_normalizer_formats_metric_objects_for_display() -> None:
    normalized = StudioService._normalize_infographic(
        {
            "sections": [
                {
                    "heading": "Protection",
                    "body": "Modeled annual benefit.",
                    "metric": {"label": "Annual protection", "value": "$859 million"},
                },
                {
                    "heading": "Maturity",
                    "body": "Modeled lifecycle.",
                    "metric": "{'label': 'Asset maturity', 'value': '30 years'}",
                },
            ]
        }
    )

    assert normalized["sections"][0]["metric"] == "Annual protection: $859 million"
    assert normalized["sections"][1]["metric"] == "Asset maturity: 30 years"


def test_deck_normalizer_preserves_llm_hierarchy_and_promotes_legacy_equations() -> None:
    normalized = StudioService._normalize_slide_deck(
        {
            "title": "Deck",
            "slides": [
                {
                    "title": "Bellman model",
                    "layout": "big_statement",
                    "body": "The value function connects present rewards to future states.",
                    "footnote": "Bellman equation: V(s) = E[r + γV(s')]",
                    "visual_hierarchy": "balanced",
                    "spacing": "airy",
                    "citations": ["S1"],
                }
            ],
        }
    )

    slide = normalized["slides"][0]
    assert slide["equation"] == "Bellman equation: V(s) = E[r + γV(s')]"
    assert slide["footnote"] == ""
    assert slide["visual_hierarchy"] == "equation"
    assert slide["spacing"] == "airy"


def test_deck_quality_gate_requires_real_media_queries_when_media_is_automatic() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Deck",
        document_ids=["document"],
        options={
            "slide_count_strategy": "auto",
            "image_strategy": "auto",
            "media_density": "auto",
        },
    )
    slides = [
        {
            "title": f"Grounded slide {index}",
            "layout": "big_statement",
            "body": "A substantive source-grounded explanation for the intended audience.",
            "citations": ["S1"],
        }
        for index in range(1, 7)
    ]

    with pytest.raises(ValueError, match="licensed-media queries"):
        StudioService._decode_output(artifact, json.dumps({"title": "Deck", "slides": slides}))


def test_automatic_deep_multi_source_deck_rejects_a_two_slide_stub() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Deck",
        document_ids=["one", "two", "three", "four", "five"],
        options={
            "slide_count_strategy": "auto",
            "content_depth": "deep",
            "image_strategy": "none",
        },
    )
    slides = [
        {
            "title": "Opening",
            "layout": "big_statement",
            "body": "The evidence establishes the problem and why it matters.",
            "citations": ["S1"],
        },
        {
            "title": "Conclusion",
            "layout": "big_statement",
            "body": "The evidence supports an actionable conclusion.",
            "citations": ["S2"],
        },
    ]

    with pytest.raises(ValueError, match="too shallow"):
        StudioService._decode_output(
            artifact, json.dumps({"title": "Deck", "slides": slides})
        )


def test_deck_quality_gate_counts_source_grounded_native_visuals() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Deck",
        document_ids=["document"],
        options={
            "slide_count_strategy": "auto",
            "image_strategy": "pexels",
            "media_density": "rich",
        },
    )
    slides = [
        {
            "title": f"Grounded slide {index}",
            "layout": "editorial_split",
            "body": "A substantive source-grounded explanation for the intended audience.",
            "image_queries": [f"specific retail EV charging field photograph {index}"],
            "citations": ["S1"],
        }
        for index in range(1, 5)
    ]
    slides.extend(
        [
            {
                "title": "Measured change",
                "layout": "chart_focus",
                "body": "The source reports a measurable difference between scenarios.",
                "chart": {
                    "type": "bar",
                    "labels": ["Current", "Restored"],
                    "series": [{"name": "Risk", "values": [80, 20]}],
                },
                "citations": ["S1"],
            },
            {
                "title": "How protection works",
                "layout": "quote_photo",
                "body": (
                    "Retail charging field monitoring connects charger uptime to driver "
                    "confidence."
                ),
                "quote": "Restoration succeeds when ecology and engineering work together.",
                "citations": ["S1"],
            },
        ]
    )
    slides.extend(
        {
            "title": f"Implication {index}",
            "layout": "big_statement",
            "body": "A substantive source-grounded implication for the intended audience.",
            "citations": ["S1"],
        }
        for index in range(7, 10)
    )

    payload, _, _ = StudioService._decode_output(
        artifact, json.dumps({"title": "Deck", "slides": slides})
    )

    assert len(payload["slides"]) == 9
    assert payload["slides"][5]["image_queries"]
    assert payload["slides"][5]["layout"] == "quote_photo"


def test_deck_planning_replaces_generic_document_headings_with_a_claim_subject() -> None:
    evidence = Evidence(
        chunk_id="chunk",
        paper_id="paper",
        paper_title="A bibliographic paper title",
        section="Results",
        section_type="results",
        page_start=2,
        page_end=2,
        text="Claims should be organized around the decision they support. More evidence follows.",
        similarity=0.9,
    )

    title = StudioService._deck_planning_topic(evidence, "Fallback title")

    assert title == "Claims should be organized around the decision they support"
    fallback_title = StudioService._deck_fallback_title(
        "References",
        evidence.text,
        fallback="Fallback",
    )
    assert fallback_title == title


def test_slide_retrieval_query_starts_with_the_users_focus() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Test deck",
        document_ids=["document"],
        language="fa",
        options={"focus": "یادگیری تقویتی چیست؟"},
    )

    query = StudioService._retrieval_query(artifact)

    assert query == "یادگیری تقویتی چیست؟"


def test_slide_deck_prompt_requires_sparse_bounded_json() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Evidence deck",
        document_ids=["document"],
        language="en",
        options={
            "slide_count_strategy": "auto",
            "content_depth": "deep",
            "media_density": "rich",
        },
    )

    instructions = StudioService._instructions(artifact)

    assert "Omit every optional field that the chosen layout does not use" in instructions
    assert "normally use 6 to 10 slides" in instructions


def test_slide_deck_generation_evidence_is_bounded_and_source_diverse() -> None:
    evidence = [
        Evidence(
            chunk_id=f"chunk-{index}",
            paper_id=document,
            paper_title=document,
            document_id=document,
            filename=f"{document}.pdf",
            section="Results",
            section_type="results",
            page_start=index,
            page_end=index,
            text=(f"Claim {index} " + "evidence " * 300),
            similarity=1 - index / 100,
        )
        for index, document in enumerate(("A", "A", "A", "B", "B", "C"), start=1)
    ]

    selected = StudioService._select_generation_evidence(evidence, limit=4)
    citations, context = StudioService._context(selected, max_text_chars=1_200)

    assert [item.document_id for item in selected] == ["A", "B", "C", "A"]
    assert [citation.label for citation in citations] == ["S1", "S2", "S3", "S4"]
    assert context.count("...") == 4


def test_agentic_video_contract_enforces_scene_timing_and_media_provenance() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact-video-123",
        workspace_id="workspace",
        artifact_type="video_overview",
        title="Video overview",
        document_ids=["document"],
        options={
            "duration_seconds": 30,
            "scene_count_strategy": "auto",
            "_agent_scene_count": 2,
            "_agent_video_plan": {
                "pace": "balanced",
                "narrative_arc": "explanation",
                "total_duration_seconds": 30,
                "scenes": [
                    {
                        "id": "scene-01",
                        "phase": "hook",
                        "role": "insight",
                        "objective": "Frame the documented finding",
                        "duration_seconds": 18,
                        "narration_word_budget": 12,
                        "visual_intent": "footage",
                        "evidence_ids": ["S1"],
                        "citations": ["S1"],
                        "chart": None,
                        "media_request": {
                            "query": "solar panel assembly line workers",
                            "source_evidence_ids": ["S1"],
                            "citations": ["S1"],
                            "require_api": True,
                            "allow_generated": False,
                        },
                    },
                    {
                        "id": "scene-02",
                        "phase": "close",
                        "role": "synthesis",
                        "objective": "Close with the documented implication",
                        "duration_seconds": 12,
                        "narration_word_budget": 10,
                        "visual_intent": "statement",
                        "evidence_ids": ["S2"],
                        "citations": ["S2"],
                        "chart": None,
                        "media_request": None,
                    },
                ],
            },
        },
    )
    draft = json.dumps(
        {
            "title": "Model title",
            "scenes": [
                {
                    "id": "wrong-id",
                    "title": "Opening finding",
                    "narration": "This is a concise source-grounded opening statement.",
                    "duration_seconds": 3,
                    "media_query": "unrelated stock footage",
                    "media_queries": ["solar factory workers close up"],
                    "citations": ["S9"],
                },
                {
                    "title": "Closing finding",
                    "narration": "This is the documented conclusion.",
                    "duration_seconds": 3,
                    "chart": {"labels": ["Fake", "Chart"], "values": [1, 2]},
                    "citations": ["S9"],
                },
            ],
        }
    )

    data, _markdown, _title = StudioService._decode_output(artifact, draft)

    assert [scene["id"] for scene in data["scenes"]] == ["scene-01", "scene-02"]
    assert [scene["duration_seconds"] for scene in data["scenes"]] == [18, 12]
    assert [scene["citations"] for scene in data["scenes"]] == [["S1"], ["S2"]]
    assert data["scenes"][0]["media_query"] == "solar factory workers close up"
    assert data["scenes"][0]["media_queries"][-1] == "solar panel assembly line workers"
    assert data["scenes"][0]["media_provenance"]["require_api"] is True
    assert data["scenes"][0]["media_provenance"]["allow_generated"] is False
    assert data["scenes"][1]["media_queries"] == []
    assert data["scenes"][1]["chart"] == {}


def test_agentic_video_contract_allows_word_budget_to_move_between_scenes() -> None:
    plan = {
        "narrative_arc": "story",
        "pace": "balanced",
        "total_duration_seconds": 30,
        "scenes": [
            {
                "id": "scene-01",
                "duration_seconds": 15,
                "narration_word_budget": 10,
                "media_request": None,
            },
            {
                "id": "scene-02",
                "duration_seconds": 15,
                "narration_word_budget": 10,
                "media_request": None,
            },
        ],
    }
    payload = {
        "title": "Balanced narration",
        "scenes": [
            {
                "title": "Longer opening",
                "narration": (
                    "One two three four five six seven eight nine ten eleven twelve thirteen"
                ),
            },
            {"title": "Short close", "narration": "One two three four five six seven"},
        ],
    }

    result = StudioService._apply_video_agent_plan(payload, plan)

    assert len(result["scenes"][0]["narration"].split()) == 13
    assert sum(len(scene["narration"].split()) for scene in result["scenes"]) == 20


def test_agentic_video_contract_allows_natural_speech_safety_margin() -> None:
    plan = {
        "narrative_arc": "story",
        "pace": "balanced",
        "total_duration_seconds": 60,
        "scenes": [
            {
                "id": "scene-01",
                "duration_seconds": 30,
                "narration_word_budget": 20,
                "media_request": None,
            },
            {
                "id": "scene-02",
                "duration_seconds": 30,
                "narration_word_budget": 20,
                "media_request": None,
            },
        ],
    }
    payload = {
        "title": "Natural narration margin",
        "scenes": [
            {"title": "One", "narration": " ".join(["word"] * 25)},
            {"title": "Two", "narration": " ".join(["word"] * 25)},
        ],
    }

    result = StudioService._apply_video_agent_plan(payload, plan)

    assert sum(len(scene["narration"].split()) for scene in result["scenes"]) == 50


def test_agentic_video_contract_rejects_excessive_total_narration() -> None:
    plan = {
        "total_duration_seconds": 30,
        "scenes": [
            {
                "id": "scene-01",
                "duration_seconds": 15,
                "narration_word_budget": 10,
                "media_request": None,
            },
            {
                "id": "scene-02",
                "duration_seconds": 15,
                "narration_word_budget": 10,
                "media_request": None,
            },
        ],
    }
    payload = {
        "title": "Too much narration",
        "scenes": [
            {"title": "One", "narration": " ".join(["word"] * 18)},
            {"title": "Two", "narration": " ".join(["word"] * 17)},
        ],
    }

    with pytest.raises(ValueError, match="total word budget"):
        StudioService._apply_video_agent_plan(payload, plan)


def test_video_scene_language_gate_rejects_source_boilerplate_and_wrong_language() -> None:
    payload = {
        "scenes": [
            {
                "title": "Charging Behavior",
                "kicker": "اولویت راننده",
                "on_screen_text": "For details see: https://example.test/publication/123",
                "narration": "این روایت فارسی درباره شارژ هنگام خرید و ادامه سفر است.",
            }
        ]
    }

    with pytest.raises(ValueError, match="not clean Persian"):
        StudioService._validate_video_scene_language("fa", payload)

    payload["scenes"][0]["title"] = "رفتار شارژ"
    with pytest.raises(ValueError, match="source or reference boilerplate"):
        StudioService._validate_video_scene_language("fa", payload)


def test_agentic_video_contract_allows_persian_natural_speech_budget() -> None:
    plan = {
        "total_duration_seconds": 60,
        "scenes": [
            {"id": "scene-01", "duration_seconds": 30, "narration_word_budget": 20},
            {"id": "scene-02", "duration_seconds": 30, "narration_word_budget": 20},
        ],
    }
    payload = {
        "title": "روایت فارسی",
        "scenes": [
            {"title": "یک", "narration": " ".join(["روایت"] * 36)},
            {"title": "دو", "narration": " ".join(["طبیعی"] * 36)},
        ],
    }

    result = StudioService._apply_video_agent_plan(payload, plan)

    assert sum(len(scene["narration"].split()) for scene in result["scenes"]) == 72


def test_video_generation_uses_the_agentic_plan_end_to_end(tmp_path: Path) -> None:
    class AgenticVideoModel:
        def __init__(self) -> None:
            self.instructions = ""

        def run(self, *, instructions: str, user_input: str, **_kwargs) -> GenerationResult:
            self.instructions = instructions
            marker = "Autonomous video planning contract (follow exactly; do not pad):\n"
            contract = json.loads(instructions.split(marker, maxsplit=1)[1])
            scenes = []
            for scene in contract["scenes"]:
                request = scene.get("media_request") or {}
                scenes.append(
                    {
                        "title": scene["title"],
                        "narration": "A concise source-grounded explanation.",
                        "media_queries": [request["query"]] if request else [],
                    }
                )
            return GenerationResult(text=json.dumps({"title": "Model title", "scenes": scenes}))

        def failover_status(self) -> dict[str, str]:
            return {"provider": "test", "active_model": "agentic-video-test"}

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"The selected evidence documents a measured retrieval quality result.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = AgenticVideoModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="video_overview",
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "generating", result.error
    assert result.stage == "render_queued"
    assert result.progress == 80
    assert "Autonomous video planning contract" in model.instructions
    assert result.data["agent_plan"]["scene_count"] == len(result.data["scenes"])
    assert sum(scene["duration_seconds"] for scene in result.data["scenes"]) == 180
    assert all("media_provenance" in scene for scene in result.data["scenes"])
    assert result.options["duration_seconds"] == 180
    assert not any(key.startswith("_agent_") for key in result.options)


def test_autonomous_deck_never_publishes_raw_plan_excerpts_when_model_is_invalid(
    tmp_path: Path,
) -> None:
    class EmptySlidesModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(text='{"title":"Deck","slides":[]}')

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Retrieval augmented generation grounds an answer in selected source evidence.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = EmptySlidesModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="slide_deck",
            document_ids=[document.document_id],
            options={"design_control": "auto", "slide_count_strategy": "auto"},
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "failed"
    assert not result.data.get("slides")
    assert "slide" in str(result.error).casefold()
    assert len(model.requests) == 2


def test_autonomous_deck_lets_the_composer_choose_a_concise_count(tmp_path: Path) -> None:
    class AutonomousDeckModel:
        def __init__(self) -> None:
            self.instructions = ""

        def run(self, *, instructions: str, **_kwargs) -> GenerationResult:
            self.instructions = instructions
            slides = [
                {
                    "title": f"Grounded narrative step {index}",
                    "layout": "big_statement",
                    "body": "A substantive audience-facing explanation grounded in the source.",
                    "citations": ["S1"],
                }
                for index in range(1, 6)
            ]
            return GenerationResult(text=json.dumps({"title": "Deck", "slides": slides}))

        def failover_status(self) -> dict[str, str]:
            return {"provider": "test", "active_model": "autonomous-deck-test"}

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Selected evidence supports a focused presentation.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = AutonomousDeckModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="slide_deck",
            document_ids=[document.document_id],
            options={
                "slide_count_strategy": "auto",
                "slide_count": 12,
                "image_strategy": "none",
            },
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "generating", result.error
    assert result.stage == "render_queued"
    assert len(result.data["slides"]) == 5
    assert "Autonomous deck planning contract" not in model.instructions
    assert "agent_plan" not in result.data


def test_fixed_deck_count_is_user_controlled_and_does_not_use_an_autonomous_plan(
    tmp_path: Path,
) -> None:
    class FixedCountDeckModel:
        def __init__(self) -> None:
            self.instructions = ""

        def run(self, *, instructions: str, **_kwargs) -> GenerationResult:
            self.instructions = instructions
            slides = [
                {
                    "title": f"Narrative step {index}",
                    "layout": "big_statement",
                    "body": "A distinct source-grounded explanation for this step.",
                    "citations": ["S1"],
                }
                for index in range(1, 6)
            ]
            return GenerationResult(text=json.dumps({"title": "Manual deck", "slides": slides}))

        def failover_status(self) -> dict[str, str]:
            return {"provider": "test", "active_model": "manual-deck-test"}

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Selected evidence supports a concise, useful presentation.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = FixedCountDeckModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="slide_deck",
            document_ids=[document.document_id],
            options={
                "design_control": "custom",
                "slide_count_strategy": "fixed",
                "slide_count": 5,
                "image_strategy": "none",
            },
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "generating", result.error
    assert result.stage == "render_queued"
    assert len(result.data["slides"]) == 5
    assert "Autonomous deck planning contract" not in model.instructions
    assert "exactly 5 slides" in model.instructions
    assert "agent_plan" not in result.data


def test_studio_cancel_marks_any_active_artifact_and_prevents_generation(tmp_path: Path) -> None:
    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Selected evidence for a cancellable studio job.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = StudioModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="video_overview",
            document_ids=[document.document_id],
        )
    )
    service.artifacts.save(
        queued.model_copy(
            update={"status": "generating", "stage": "rendering_file", "progress": 82}
        )
    )

    cancelled = service.request_cancel(workspace.workspace_id, queued.artifact_id)
    result = service.generate(queued.artifact_id, workspace_id=workspace.workspace_id)

    assert cancelled.status == "cancelled"
    assert cancelled.stage == "cancelled"
    assert cancelled.data["cancel_requested"] is True
    assert result.status == "cancelled"
    assert not model.requests


def test_successful_flashcards_must_contain_renderable_cards(tmp_path: Path) -> None:
    class EmptyThenValidFlashcardsModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            if len(self.requests) == 1:
                return GenerationResult(text='{"title":"Deck","cards":[]}')
            return GenerationResult(
                text=(
                    '{"title":"Deck","flashcards":['
                    '{"question":"What is retrieval?","answer":"Finding evidence.",'
                    '"citations":["S1"]}]}'
                )
            )

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Retrieval finds relevant source evidence.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = EmptyThenValidFlashcardsModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="flashcards",
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.data["cards"] == [
        {
            "front": "What is retrieval?",
            "back": "Finding evidence.",
            "citations": ["S1"],
        }
    ]
    assert len(model.requests) == 2


def test_legacy_markdown_flashcards_are_recovered_as_interactive_cards() -> None:
    markdown = """### فلش‌کارت ۱: بازیابی
* **پرسش:** RAG چیست؟
* **پاسخ:**
  * بازیابی شاهد و تولید پاسخ.
* **ارجاع:** [S1]

---

### Flashcard 2
* **Question:** Why cite sources?
* **Answer:** To keep answers grounded.
* **Source:** [S2]
"""

    cards = parse_legacy_flashcards(markdown)

    assert cards == [
        {
            "front": "RAG چیست؟",
            "back": "بازیابی شاهد و تولید پاسخ.",
            "citations": ["S1"],
        },
        {
            "front": "Why cite sources?",
            "back": "To keep answers grounded.",
            "citations": ["S2"],
        },
    ]


def test_markdown_flashcards_become_ready_when_a_model_ignores_the_json_format(
    tmp_path: Path,
) -> None:
    class MarkdownFlashcardsModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(
                text=(
                    "### Flashcard 1\n"
                    "**Question:** What is RAG?\n"
                    "**Answer:** Retrieval-grounded generation [S1].\n"
                    "**Source:** [S1]"
                )
            )

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"RAG retrieves evidence before generation.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = MarkdownFlashcardsModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="flashcards",
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.data["cards"][0]["front"] == "What is RAG?"
    assert result.data["cards"][0]["citations"] == ["S1"]
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    ("artifact_type", "empty_payload", "valid_payload", "content_key"),
    [
        (
            "quiz",
            '{"title":"Quiz","questions":[]}',
            '{"title":"Quiz","questions":[{"question":"Q?","choices":["A","B"],'
            '"correct_index":0,"explanation":"Because.","citations":["S1"]}]}',
            "questions",
        ),
        (
            "chart",
            '{"title":"Chart","labels":[],"datasets":[]}',
            '{"title":"Chart","chart_type":"bar","labels":["A"],'
            '"datasets":[{"label":"Score","data":[1]}],"explanation":"Result",'
            '"citations":["S1"]}',
            "datasets",
        ),
        (
            "concept_map",
            '{"title":"Map","nodes":[],"edges":[]}',
            '{"title":"Map","root":"Retrieval","nodes":[{"id":"n1",'
            '"label":"Evidence","description":"Source support","citations":["S1"]}],'
            '"edges":[]}',
            "nodes",
        ),
        (
            "infographic",
            '{"title":"Info","sections":[]}',
            '{"title":"Info","subtitle":"Overview","sections":[{"heading":"Finding",'
            '"body":"Evidence matters","metric":"1","citations":["S1"]}],'
            '"key_takeaway":"Ground answers","citations":["S1"]}',
            "sections",
        ),
        (
            "data_table",
            '{"title":"Table","columns":[],"rows":[]}',
            '{"title":"Table","columns":["Topic","Page"],'
            '"rows":[{"Topic":"Retrieval","Page":"1"}],"notes":"Grounded",'
            '"citations":["S1"]}',
            "rows",
        ),
    ],
)
def test_structured_studio_outputs_are_non_empty_before_becoming_ready(
    tmp_path: Path,
    artifact_type: str,
    empty_payload: str,
    valid_payload: str,
    content_key: str,
) -> None:
    class RepairingModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(
                text=empty_payload if len(self.requests) == 1 else valid_payload
            )

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Retrieval finds relevant source evidence.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = RepairingModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type=artifact_type,
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.data[content_key]
    assert len(model.requests) == 2


def test_structured_output_recovers_json_with_preamble_and_literal_control_characters() -> None:
    artifact = StudioGenerationRequest(
        workspace_id="workspace",
        artifact_type="concept_map",
        document_ids=["document"],
    )
    draft = (
        "Here is the requested map:\n```json\n"
        '{"title":"Map","root":"RAG","nodes":[{"id":"n1","label":"RAG",'
        '"description":"Retrieve\nthen generate","citations":["S1"]}],"edges":[]}\n```'
    )
    queued = StudioArtifact(
        artifact_id="artifact-123456abcdef",
        workspace_id=artifact.workspace_id,
        artifact_type=artifact.artifact_type,
        title="Map",
        document_ids=artifact.document_ids,
    )

    data, markdown, title = StudioService._decode_output(queued, draft)

    assert markdown == ""
    assert title == "Map"
    assert data["nodes"][0]["description"] == "Retrieve\nthen generate"


def test_concept_map_normalizes_an_object_root_without_exposing_python_repr() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact-concept-root",
        workspace_id="workspace",
        artifact_type="concept_map",
        title="نقشه مفهومی",
        document_ids=["document"],
    )
    draft = json.dumps(
        {
            "title": "نقشه مفهومی",
            "root": {
                "id": "root",
                "label": "شارژ هنگام خرید",
                "description": "این مفهوم محور نقشه است.",
            },
            "nodes": [{"id": "n1", "label": "فروشگاه", "description": "مقصد سفر"}],
            "edges": [{"source": "root", "target": "n1", "label": "پیوند"}],
        },
        ensure_ascii=False,
    )

    data, _, _ = StudioService._decode_output(artifact, draft)

    assert data["root"] == "root"
    assert data["nodes"][0]["id"] == "root"
    assert data["nodes"][0]["label"] == "شارژ هنگام خرید"
    assert not data["root"].startswith("{")


def test_timeline_output_is_normalized_into_complete_renderable_events() -> None:
    artifact = StudioArtifact(
        artifact_id="artifact-timeline-123",
        workspace_id="workspace",
        artifact_type="timeline",
        title="Timeline",
        document_ids=["document"],
    )
    draft = (
        '{"title":"تاریخچه RAG","events":['
        '{"date":"2020","title":"Dense retrieval",'
        '"description":"بازیابی متراکم introduced semantic search.",'
        '"significance":"بهبود بازیابی","citations":["S1"]},'
        '{"date":"نامشخص","title":"ارزیابی",'
        '"description":"زمان دقیق در منبع ذکر نشده است.",'
        '"uncertain":true,"citations":["S2"]}]}'
    )

    data, markdown, title = StudioService._decode_output(artifact, draft)

    assert markdown == ""
    assert title == "تاریخچه RAG"
    assert data["events"] == [
        {
            "date": "2020",
            "title": "Dense retrieval",
            "description": "بازیابی متراکم introduced semantic search.",
            "significance": "بهبود بازیابی",
            "uncertain": False,
            "citations": ["S1"],
        },
        {
            "date": "نامشخص",
            "title": "ارزیابی",
            "description": "زمان دقیق در منبع ذکر نشده است.",
            "significance": "",
            "uncertain": True,
            "citations": ["S2"],
        },
    ]


def test_structured_output_gets_multiple_repair_attempts_before_failing(tmp_path: Path) -> None:
    class TwiceBrokenModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            responses = (
                '{"title":"Map","nodes":[',
                '{"title":"Map","nodes":[{"id":"n1",',
                '{"title":"Map","root":"RAG","nodes":[{"id":"n1",'
                '"label":"RAG","description":"Grounded","citations":["S1"]}],'
                '"edges":[]}',
            )
            return GenerationResult(text=responses[len(self.requests) - 1])

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Retrieval augmented generation uses evidence.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = TwiceBrokenModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="concept_map",
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.data["nodes"][0]["label"] == "RAG"
    assert len(model.requests) == 3


def test_structured_output_uses_model_quality_fallback_before_repair(tmp_path: Path) -> None:
    class QualityFallbackModel(StudioModel):
        def __init__(self) -> None:
            super().__init__()
            self.validated_calls = 0

        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(text='{ "title": "Cards", "cards": [')

        def run_validated(self, validator, **kwargs) -> GenerationResult:
            self.validated_calls += 1
            candidates = (
                GenerationResult(text='{ "title": "Cards", "cards": ['),
                GenerationResult(
                    text=(
                        '{"title":"Cards","cards":[{"front":"RAG?",'
                        '"back":"Grounded generation","citations":["S1"]}]}'
                    )
                ),
            )
            return next(candidate for candidate in candidates if validator(candidate))

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"RAG retrieves evidence before generation.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = QualityFallbackModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="flashcards",
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.data["cards"][0]["front"] == "RAG?"
    assert model.validated_calls == 1
    assert model.requests == []


def test_slide_deck_quality_fallback_reaches_a_cross_provider_tier() -> None:
    class BoundedDeckModel:
        def __init__(self) -> None:
            self.arguments: dict = {}

        def run_validated(self, *_args, **_kwargs) -> GenerationResult:
            raise AssertionError("Slide decks must use the bounded runner")

        def run_validated_bounded(self, validator, **kwargs) -> GenerationResult:
            self.arguments = kwargs
            candidate = GenerationResult(
                text=json.dumps(
                    {
                        "title": "Deck",
                        "slides": [
                            {
                                "title": f"Grounded slide {index}",
                                "layout": "big_statement",
                                "body": "A source-grounded explanation with enough detail.",
                                "citations": ["S1"],
                            }
                            for index in range(1, 6)
                        ],
                    }
                )
            )
            assert validator(candidate)
            return candidate

    model = BoundedDeckModel()
    service = StudioService.__new__(StudioService)
    service.language_model = model
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="slide_deck",
        title="Deck",
        document_ids=["document"],
        options={
            "slide_count_strategy": "fixed",
            "slide_count": 5,
            "image_strategy": "none",
            "media_density": "minimal",
        },
    )

    result = service._run_output(artifact, instructions="x", user_input="y")

    assert result.text
    assert model.arguments["max_attempts"] == 6
    assert model.arguments["max_credential_attempts"] == 6
    assert model.arguments["max_output_tokens"] == 6_000


def test_structured_studio_outputs_use_the_bounded_quality_route() -> None:
    class BoundedStructuredModel:
        def __init__(self) -> None:
            self.arguments: dict = {}

        def run_validated(self, *_args, **_kwargs) -> GenerationResult:
            raise AssertionError("Structured Studio output must use the bounded runner")

        def run_validated_bounded(self, validator, **kwargs) -> GenerationResult:
            self.arguments = kwargs
            candidate = GenerationResult(
                text=json.dumps(
                    {
                        "title": "Cards",
                        "cards": [
                            {"front": "What is fast charging?", "back": "High-power charging."}
                        ],
                    }
                )
            )
            assert validator(candidate)
            return candidate

    model = BoundedStructuredModel()
    service = StudioService.__new__(StudioService)
    service.language_model = model
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="flashcards",
        title="Cards",
        document_ids=["document"],
        options={},
    )

    result = service._run_output(artifact, instructions="x", user_input="y")

    assert result.text
    assert model.arguments["max_attempts"] == 6
    assert model.arguments["max_credential_attempts"] == 6
    assert model.arguments["max_output_tokens"] == 3_000


def test_persian_structured_output_retries_cross_script_contamination() -> None:
    class PersianStructuredModel:
        def __init__(self) -> None:
            self.attempts = 0

        def run_validated(self, *_args, **_kwargs) -> GenerationResult:
            raise AssertionError("Structured Studio output must use the bounded runner")

        def run_validated_bounded(self, validator, **_kwargs) -> GenerationResult:
            candidates = (
                GenerationResult(
                    text='{"title":"شارژ فروشگاهی","cards":[{"front":"वाल‌مارت宣佈 چه کرد؟",'
                    '"back":"این پاسخ آلوده است."}]}'
                ),
                GenerationResult(
                    text='{"title":"شارژ فروشگاهی","cards":[{"front":"فروشگاه چه نقشی دارد؟",'
                    '"back":"این توقف برای خرید و شارژ هم‌زمان مناسب است."}]}'
                ),
            )
            for candidate in candidates:
                self.attempts += 1
                if validator(candidate):
                    return candidate
            raise AssertionError("No valid Persian candidate")

    model = PersianStructuredModel()
    service = StudioService.__new__(StudioService)
    service.language_model = model
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="flashcards",
        title="کارت‌ها",
        document_ids=["document"],
        language="fa",
        options={},
    )

    result = service._run_output(artifact, instructions="x", user_input="y")

    assert "宣" not in result.text
    assert model.attempts == 2


def test_markdown_studio_outputs_bound_provider_tiers_when_supported() -> None:
    class BoundedMarkdownModel:
        def __init__(self) -> None:
            self.arguments: dict = {}

        def run(self, **_kwargs) -> GenerationResult:
            raise AssertionError("Markdown Studio output must use validated tier bounds")

        def run_validated(self, validator, **kwargs) -> GenerationResult:
            self.arguments = kwargs
            candidate = GenerationResult(
                text="# Evidence brief\n\nGrounded result.\n\n<!-- PAPERLENS_COMPLETE -->"
            )
            assert validator(candidate)
            return candidate

    model = BoundedMarkdownModel()
    service = StudioService.__new__(StudioService)
    service.language_model = model
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="report",
        title="Report",
        document_ids=["document"],
        options={},
    )

    result = service._run_output(artifact, instructions="x", user_input="y")

    assert result.text
    assert model.arguments["max_attempts"] == 6
    assert model.arguments["max_credential_attempts"] == 6


def test_persian_markdown_output_retries_cross_script_contamination() -> None:
    class PersianMarkdownModel:
        def __init__(self) -> None:
            self.attempts = 0

        def run_validated(self, validator, **_kwargs) -> GenerationResult:
            candidates = (
                GenerationResult(
                    text="# گزارش\n\nاین वाल‌مارت پاسخ آلوده است.\n\n<!-- PAPERLENS_COMPLETE -->"
                ),
                GenerationResult(
                    text=(
                        "# گزارش\n\nاین گزارش درباره شارژ هنگام خرید است."
                        "\n\n<!-- PAPERLENS_COMPLETE -->"
                    )
                ),
            )
            for candidate in candidates:
                self.attempts += 1
                if validator(candidate):
                    return candidate
            raise AssertionError("No valid Persian candidate")

    model = PersianMarkdownModel()
    service = StudioService.__new__(StudioService)
    service.language_model = model
    artifact = StudioArtifact(
        artifact_id="artifact",
        workspace_id="workspace",
        artifact_type="report",
        title="گزارش",
        document_ids=["document"],
        language="fa",
        options={},
    )

    result = service._run_output(artifact, instructions="x", user_input="y")

    assert "वाल" not in result.text
    assert model.attempts == 2


@pytest.mark.parametrize("artifact_type", ["report", "comparison", "research_gap"])
def test_markdown_studio_actions_produce_renderable_content(
    tmp_path: Path,
    artifact_type: str,
) -> None:
    class MarkdownModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(
                text=(
                    "# Grounded output\n\nA complete source-grounded result with evidence "
                    "[S1]\n\n<!-- PAPERLENS_COMPLETE -->"
                ),
                finish_reason="stop",
            )

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Source evidence for the output.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=MarkdownModel(),
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type=artifact_type,
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.content_markdown.startswith("# Grounded output")
    assert result.content_markdown.endswith("[S1]")


@pytest.mark.parametrize(
    ("artifact_type", "response", "data_key"),
    [
        ("report", "# Report\n\nGrounded report [S1].\n\n<!-- PAPERLENS_COMPLETE -->", None),
        (
            "concept_map",
            '{"title":"Map","root":"RAG","nodes":[{"id":"n1","label":"RAG",'
            '"description":"Grounded","citations":["S1"]}],"edges":[]}',
            "nodes",
        ),
        (
            "flashcards",
            '{"title":"Cards","cards":[{"front":"RAG?","back":"Grounded generation",'
            '"citations":["S1"]}]}',
            "cards",
        ),
        (
            "quiz",
            '{"title":"Quiz","questions":[{"question":"RAG?","choices":["A","B"],'
            '"correct_index":0,"explanation":"Grounded","citations":["S1"]}]}',
            "questions",
        ),
        (
            "infographic",
            '{"title":"Info","sections":[{"heading":"RAG","body":"Grounded",'
            '"metric":"1","citations":["S1"]}],"key_takeaway":"Evidence"}',
            "sections",
        ),
        (
            "data_table",
            '{"title":"Table","columns":["Topic"],"rows":[{"Topic":"RAG"}],'
            '"notes":"Grounded","citations":["S1"]}',
            "rows",
        ),
        (
            "chart",
            '{"title":"Chart","chart_type":"bar","labels":["RAG"],"datasets":['
            '{"label":"Evidence","data":[1]}],"citations":["S1"]}',
            "datasets",
        ),
        (
            "timeline",
            '{"title":"Timeline","events":[{"date":"Stage 1","title":"Retrieve",'
            '"description":"Retrieve evidence","significance":"Grounding",'
            '"uncertain":false,"citations":["S1"]}]}',
            "events",
        ),
        (
            "comparison",
            "# Comparison\n\n| Source | Finding |\n|---|---|\n| S1 | RAG |"
            "\n\nThe sources agree on grounded retrieval [S1]."
            "\n\n<!-- PAPERLENS_COMPLETE -->",
            None,
        ),
        (
            "research_gap",
            "# Research gaps\n\nMore evaluation is needed [S1]."
            "\n\n<!-- PAPERLENS_COMPLETE -->",
            None,
        ),
    ],
)
def test_every_non_audio_studio_action_produces_renderable_output(
    tmp_path: Path,
    artifact_type: str,
    response: str,
    data_key: str | None,
) -> None:
    class FixedOutputModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(text=response, finish_reason="stop")

    workspace_store = WorkspaceStore(tmp_path / artifact_type / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Retrieval augmented generation uses source evidence.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=FixedOutputModel(),
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type=artifact_type,
            document_ids=[document.document_id],
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    if data_key:
        assert result.data[data_key]
    else:
        assert result.content_markdown.strip()


class StudioModel:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def run(
        self,
        *,
        instructions: str,
        user_input: str,
        tools: Sequence[ToolSpec] = (),
        execute_tool=None,
        tool_choice="auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        self.requests.append(user_input)
        return GenerationResult(
            text=(
                '{"title":"Retrieval quiz","questions":['
                '{"question":"What does the selected source describe?",'
                '"choices":["Retrieval","Rendering","Routing","Recording"],'
                '"correct_index":0,"explanation":"The source states it.",'
                '"citations":["S1"]}]}'
            )
        )

    def failover_status(self) -> dict[str, object]:
        return {"provider": "test", "active_model": "studio-test"}


class StudioKnowledgeBase:
    def __init__(self, selected: str, excluded: str) -> None:
        self.selected = selected
        self.excluded = excluded
        self.calls: list[tuple[str, ...]] = []

    def search_balanced(
        self,
        workspace_id: str,
        query: str,
        document_ids: Sequence[str],
        *,
        top_k: int,
        per_document: int,
        section_types=None,
    ) -> list[Evidence]:
        self.calls.append(tuple(document_ids))
        return [
            Evidence(
                chunk_id="selected-chunk",
                paper_id=self.selected,
                paper_title="Selected source",
                section="Results",
                section_type="results",
                page_start=3,
                page_end=3,
                text="Selected source evidence about retrieval quality.",
                similarity=0.91,
                workspace_id=workspace_id,
                document_id=self.selected,
                filename="selected.pdf",
            )
        ]


def test_studio_generation_is_persisted_and_strictly_source_scoped(tmp_path: Path) -> None:
    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    selected = workspace_store.add_document(
        workspace.workspace_id,
        b"Selected source evidence",
        "selected.txt",
        "text/plain",
    )
    excluded = workspace_store.add_document(
        workspace.workspace_id,
        b"Private excluded evidence",
        "excluded.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, selected.document_id, status="ready")
    workspace_store.update_document(workspace.workspace_id, excluded.document_id, status="ready")
    knowledge_base = StudioKnowledgeBase(selected.document_id, excluded.document_id)
    model = StudioModel()
    artifacts = StudioArtifactStore(workspace_store.root)
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=knowledge_base,
        language_model=model,
        artifacts=artifacts,
    )

    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="quiz",
            document_ids=[selected.document_id],
            language="en",
            title="My custom quiz title",
            options={"question_count": 5},
        )
    )
    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert result.title == "My custom quiz title"
    assert result.document_ids == [selected.document_id]
    assert result.data["questions"][0]["correct_index"] == 0
    assert knowledge_base.calls == [(selected.document_id,)]
    assert "selected.pdf" in model.requests[0]
    assert "excluded.txt" not in model.requests[0]
    assert artifacts.get(workspace.workspace_id, result.artifact_id).status == "ready"


def test_studio_rejects_sources_outside_the_workspace(tmp_path: Path) -> None:
    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase("missing", "missing"),
        language_model=StudioModel(),
        artifacts=StudioArtifactStore(workspace_store.root),
    )

    try:
        service.create(
            StudioGenerationRequest(
                workspace_id=workspace.workspace_id,
                artifact_type="summary",
                document_ids=["missing-document"],
            )
        )
    except ValueError as error:
        assert "Unknown selected document" in str(error)
    else:
        raise AssertionError("Unknown source selection must be rejected")


def test_studio_continues_a_token_limited_markdown_deliverable(tmp_path: Path) -> None:
    class TruncatingModel(StudioModel):
        def __init__(self) -> None:
            super().__init__()
            self.responses = [
                GenerationResult(
                    text="## Core concepts\n\n**Epsilon-greedy works as follows",
                    finish_reason="length",
                ),
                GenerationResult(
                    text=(
                        "**: it balances exploration and exploitation [S1].\n\n"
                        "## Review questions\n\n1. Why is exploration necessary? [S1]\n\n"
                        "<!-- PAPERLENS_COMPLETE -->"
                    ),
                    finish_reason="stop",
                ),
            ]

        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return self.responses.pop(0)

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Evidence about reinforcement learning",
        "rl.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = TruncatingModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="study_guide",
            document_ids=[document.document_id],
            language="en",
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "ready"
    assert len(model.requests) == 2
    assert "**Epsilon-greedy works as follows**" in result.content_markdown
    assert result.content_markdown.endswith("[S1]")


def test_studio_never_marks_a_still_truncated_markdown_deliverable_ready(tmp_path: Path) -> None:
    class PersistentlyTruncatingModel(StudioModel):
        def __init__(self) -> None:
            super().__init__()
            self.responses = [
                GenerationResult(text="# Report\n\nThe first unfinished", finish_reason="length"),
                GenerationResult(text=" section remains unfinished", finish_reason="length"),
                GenerationResult(text=" and still ends mid sentence", finish_reason="stop"),
            ]

        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return self.responses.pop(0)

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Evidence for a long report",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = PersistentlyTruncatingModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="report",
            document_ids=[document.document_id],
            language="en",
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "failed"
    assert "incomplete" in (result.error or "").lower()
    assert len(model.requests) == 3


def test_empty_markdown_deliverable_never_becomes_ready(tmp_path: Path) -> None:
    class EmptyModel(StudioModel):
        def run(self, **kwargs) -> GenerationResult:
            self.requests.append(kwargs["user_input"])
            return GenerationResult(text="", finish_reason="stop")

    workspace_store = WorkspaceStore(tmp_path / "workspaces")
    workspace = workspace_store.create_workspace("Studio")
    document = workspace_store.add_document(
        workspace.workspace_id,
        b"Evidence about retrieval.",
        "source.txt",
        "text/plain",
    )
    workspace_store.update_document(workspace.workspace_id, document.document_id, status="ready")
    model = EmptyModel()
    service = StudioService(
        workspaces=workspace_store,
        knowledge_base=StudioKnowledgeBase(document.document_id, "none"),
        language_model=model,
        artifacts=StudioArtifactStore(workspace_store.root),
    )
    queued = service.create(
        StudioGenerationRequest(
            workspace_id=workspace.workspace_id,
            artifact_type="report",
            document_ids=[document.document_id],
            language="en",
        )
    )

    result = service.generate(queued.artifact_id)

    assert result.status == "failed"
    assert "empty" in (result.error or "").lower()
    assert len(model.requests) == 1
