from __future__ import annotations

from dataclasses import replace

import pytest

from paperlens.deck_agent import (
    ChartEvidence,
    ChartSeries,
    ContentDensity,
    DeckPlanningError,
    EvidenceKind,
    EvidenceUnit,
    MediaAsset,
    MediaDensity,
    PlanningMode,
    plan_deck,
)


def _evidence(
    index: int,
    *,
    topic: str = "Reinforcement learning",
    kind: EvidenceKind = EvidenceKind.INSIGHT,
    importance: float = 0.5,
    chart: ChartEvidence | None = None,
    media: tuple[MediaAsset, ...] = (),
) -> EvidenceUnit:
    return EvidenceUnit(
        evidence_id=f"S{index}",
        topic=topic,
        claim=f"Grounded claim number {index} explains a distinct finding.",
        citations=(f"S{index}",),
        kind=kind,
        importance=importance,
        chart=chart,
        media_candidates=media,
    )


def _api_media(index: int = 1) -> MediaAsset:
    return MediaAsset(
        provider="pexels",
        asset_id=f"photo-{index}",
        download_url=f"https://images.example.test/photo-{index}.jpg",
        source_url=f"https://pexels.example.test/photo/{index}",
        mime_type="image/jpeg",
        origin="api",
        verified=True,
        attribution="Documentary Photographer",
    )


def _chart(*, citations: tuple[str, ...] = ("S1",)) -> ChartEvidence:
    return ChartEvidence(
        chart_type="line",
        title="Measured reward",
        labels=("Baseline", "Agent"),
        series=(ChartSeries("Reward", (42.0, 68.0)),),
        citations=citations,
        unit="points",
    )


def test_autonomous_mode_derives_count_and_ignores_legacy_default_twelve() -> None:
    evidence = [_evidence(index) for index in range(1, 6)]

    plan = plan_deck(
        evidence,
        {
            "design_control": "auto",
            "slide_count": 12,
            "image_strategy": "none",
            "evidence_depth": "balanced",
        },
    )

    assert plan.mode is PlanningMode.AUTONOMOUS
    assert plan.slide_count == 5
    assert plan.slide_count != 12
    assert all(slide.title for slide in plan.slides)
    assert all(slide.objective for slide in plan.slides)
    assert all(slide.evidence_ids for slide in plan.slides[1:])


def test_content_density_changes_derived_count_without_padding() -> None:
    evidence = [_evidence(index) for index in range(1, 7)]

    concise = plan_deck(
        evidence,
        {"content_density": "concise", "image_strategy": "none"},
    )
    deep = plan_deck(
        evidence,
        {"content_density": "deep", "image_strategy": "none"},
    )

    assert concise.content_density is ContentDensity.CONCISE
    assert deep.content_density is ContentDensity.DEEP
    assert concise.slide_count == 3
    assert deep.slide_count == 8


def test_guided_slide_count_is_a_soft_target_and_never_creates_filler() -> None:
    short = plan_deck(
        [_evidence(1), _evidence(2)],
        {
            "design_control": "custom",
            "slide_count": 12,
            "content_density": "balanced",
            "image_strategy": "none",
        },
    )
    long = plan_deck(
        [
            _evidence(index, topic=f"Distinct topic {index}")
            for index in range(1, 7)
        ],
        {
            "design_control": "custom",
            "slide_count": 4,
            "content_density": "deep",
            "image_strategy": "none",
        },
    )

    assert short.mode is PlanningMode.GUIDED
    assert short.slide_count == 2
    assert long.slide_count == 7
    assert any(warning.code == "soft_slide_target_exceeded" for warning in long.warnings)
    placeholders = {"Context", "Evidence", "Decision", "Action"}
    assert all(slide.title not in placeholders for slide in long.slides)


def test_empty_placeholder_uncited_and_duplicate_evidence_never_enter_plan() -> None:
    plan = plan_deck(
        [
            _evidence(1),
            {
                "id": "bad-empty",
                "topic": "Topic",
                "claim": "",
                "citations": ["S2"],
            },
            {
                "id": "bad-placeholder",
                "topic": "Topic",
                "claim": "Decision",
                "citations": ["S3"],
            },
            {
                "id": "bad-uncited",
                "topic": "Topic",
                "claim": "A plausible but unsupported claim",
                "citations": [],
            },
            replace(_evidence(4), claim=_evidence(1).claim),
        ],
        {"image_strategy": "none"},
    )

    assert set(plan.rejected_evidence_ids) == {
        "bad-empty",
        "bad-placeholder",
        "bad-uncited",
        "S4",
    }
    assert {item for slide in plan.slides for item in slide.evidence_ids} == {"S1"}
    assert {warning.code for warning in plan.warnings} >= {
        "invalid_evidence_rejected",
        "duplicate_evidence_rejected",
    }


def test_automatic_deck_rejects_document_noise_and_caps_a_long_retrieval_set() -> None:
    useful = [
        EvidenceUnit(
            evidence_id=f"useful-{index}",
            topic=f"Reinforcement learning application {index}",
            claim=(
                f"The documented reinforcement learning finding {index} explains a distinct "
                "decision, implementation tradeoff, and practical implication."
            ),
            citations=(f"useful-{index}",),
            importance=0.8,
        )
        for index in range(1, 22)
    ]
    noise = [
        EvidenceUnit(
            evidence_id=f"noise-{index}",
            topic=topic,
            claim=f"{topic} for the source record and publication metadata.",
            citations=(f"noise-{index}",),
        )
        for index, topic in enumerate(
            [
                "Authors",
                "References",
                "Data Availability Statement",
                "Front Matter",
                "Making sure you're not a bot!",
                "Acknowledgements",
                "Citation information",
                "Supplementary material",
                "Sato, M.",
            ],
            start=1,
        )
    ]

    plan = plan_deck(
        [*noise, *useful],
        {"design_control": "auto", "image_strategy": "none"},
    )

    assert plan.mode is PlanningMode.AUTONOMOUS
    assert plan.slide_count <= 14
    assert set(f"noise-{index}" for index in range(1, 10)).issubset(plan.rejected_evidence_ids)
    assert all(
        "reference" not in slide.title.casefold()
        and "author" not in slide.title.casefold()
        and "bot" not in slide.title.casefold()
        for slide in plan.slides
    )


def test_planning_fails_when_every_claim_is_invalid() -> None:
    with pytest.raises(DeckPlanningError, match="No cited"):
        plan_deck(
            [{"id": "bad", "topic": "Context", "claim": "Decision", "citations": []}]
        )


def test_ungrounded_or_illustrative_chart_is_removed_without_fake_replacement() -> None:
    invalid = replace(_chart(citations=("S99",)), illustrative=True)

    plan = plan_deck(
        [_evidence(1, kind=EvidenceKind.METRIC, chart=invalid)],
        {"include_charts": True, "image_strategy": "none"},
    )

    content_slide = plan.slides[1]
    assert content_slide.chart is None
    assert content_slide.layout != "chart_focus"
    assert any(warning.code == "ungrounded_chart_rejected" for warning in plan.warnings)


def test_documented_numeric_evidence_is_the_only_path_to_chart_layout() -> None:
    plan = plan_deck(
        [_evidence(1, kind=EvidenceKind.METRIC, chart=_chart())],
        {"include_charts": True, "image_strategy": "none"},
    )

    content_slide = plan.slides[1]
    assert content_slide.layout == "chart_focus"
    assert content_slide.chart == _chart()
    assert set(content_slide.chart.citations).issubset(content_slide.citations)


def test_user_can_disable_even_valid_charts() -> None:
    plan = plan_deck(
        [_evidence(1, kind=EvidenceKind.METRIC, chart=_chart())],
        {"include_charts": False, "image_strategy": "none"},
    )

    assert plan.slides[1].chart is None
    assert plan.slides[1].layout != "chart_focus"


def test_only_verified_api_media_can_reach_a_slide() -> None:
    fake = replace(
        _api_media(),
        provider="placeholder",
        download_url="data:image/png;base64,AAAA",
        verified=False,
    )
    plan = plan_deck(
        [
            _evidence(
                1,
                kind=EvidenceKind.CASE_STUDY,
                media=(fake, _api_media(2)),
            )
        ],
        {"media_density": "rich"},
    )

    content_slide = plan.slides[1]
    assert content_slide.media == _api_media(2)
    assert content_slide.layout == "case_study"
    assert any(warning.code == "unverified_media_rejected" for warning in plan.warnings)


def test_missing_media_stays_an_api_request_and_never_becomes_fake_asset() -> None:
    plan = plan_deck(
        [
            _evidence(
                1,
                topic=(
                    "[S22] [Reinforcement Learning: A Computational Perspective]"
                    "(publication/230504820_Reinforcement_Learning) S11"
                ),
                kind=EvidenceKind.CASE_STUDY,
            )
        ],
        {"media_density": "rich", "image_strategy": "pexels"},
    )

    slide = plan.slides[1]
    assert slide.media is None
    assert slide.media_request is not None
    assert slide.media_request.require_api is True
    assert slide.media_request.allow_generated is False
    assert "S22" not in slide.media_request.query
    assert "S11" not in slide.media_request.query
    assert "publication/" not in slide.media_request.query
    assert "[Reinforcement" not in slide.media_request.query
    assert slide.layout != "case_study"
    assert any(warning.code == "api_media_resolution_required" for warning in plan.warnings)


def test_media_density_is_an_explicit_deck_level_coverage_decision() -> None:
    evidence = [
        _evidence(index, topic=f"Topic {index}", kind=EvidenceKind.CASE_STUDY)
        for index in range(1, 9)
    ]

    none = plan_deck(evidence, {"media_density": "none"})
    rich = plan_deck(evidence, {"media_density": "rich"})

    assert none.media_density is MediaDensity.NONE
    assert not any(slide.media or slide.media_request for slide in none.slides)
    assert rich.media_density is MediaDensity.RICH
    assert sum(slide.media_request is not None for slide in rich.slides) == 6


@pytest.mark.parametrize(
    ("kind", "count", "expected"),
    [
        (EvidenceKind.COMPARISON, 1, "big_statement"),
        (EvidenceKind.COMPARISON, 2, "comparison_dual"),
        (EvidenceKind.PROCESS, 1, "big_statement"),
        (EvidenceKind.PROCESS, 2, "process_flow"),
        (EvidenceKind.TIMELINE, 2, "cards_icon"),
        (EvidenceKind.TIMELINE, 3, "timeline_story"),
    ],
)
def test_layouts_are_selected_only_when_their_semantic_inputs_exist(
    kind: EvidenceKind,
    count: int,
    expected: str,
) -> None:
    plan = plan_deck(
        [_evidence(index, topic="One narrative", kind=kind) for index in range(1, count + 1)],
        {"image_strategy": "none", "content_density": "balanced"},
    )

    assert plan.slides[1].layout == expected


def test_planning_is_deterministic_and_does_not_mutate_input() -> None:
    raw = [
        {
            "id": "fact-1",
            "topic": "Agent policy",
            "claim": "A policy maps observed states to documented actions.",
            "citations": ["S1"],
            "importance": 0.8,
        }
    ]
    original = [dict(raw[0], citations=list(raw[0]["citations"]))]

    first = plan_deck(raw, {"media_density": "balanced"})
    second = plan_deck(raw, {"media_density": "balanced"})

    assert first == second
    assert raw == original


def test_auto_defaults_adapt_to_deck_format() -> None:
    executive = plan_deck(
        [_evidence(index) for index in range(1, 5)],
        {"deck_format": "executive_brief", "image_strategy": "none"},
    )
    teaching = plan_deck(
        [_evidence(index) for index in range(1, 5)],
        {"deck_format": "teaching_deck", "image_strategy": "none"},
    )

    assert executive.content_density is ContentDensity.CONCISE
    assert teaching.content_density is ContentDensity.DEEP
    assert executive.slide_count < teaching.slide_count


def test_auto_language_keeps_agent_generated_objectives_in_persian() -> None:
    plan = plan_deck(
        [
            {
                "id": "S1",
                "topic": "یادگیری تقویتی",
                "claim": "عامل با دریافت پاداش، سیاست تصمیم‌گیری خود را اصلاح می‌کند.",
                "citations": ["S1"],
                "kind": "concept",
            }
        ],
        {"language": "auto", "image_strategy": "none"},
    )

    assert "توضیح" in plan.slides[1].objective
