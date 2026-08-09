from __future__ import annotations

from dataclasses import replace

import pytest

from paperlens.video_agent import (
    ChartEvidence,
    ChartSeries,
    EvidenceKind,
    NarrativePhase,
    Pace,
    PlanningMode,
    VideoEvidence,
    VideoPlanningError,
    plan_video,
)


def _evidence(
    index: int,
    *,
    topic: str = "Reinforcement learning",
    kind: EvidenceKind = EvidenceKind.INSIGHT,
    complexity: float | None = None,
    detail: str = "",
    chart: ChartEvidence | None = None,
) -> VideoEvidence:
    return VideoEvidence(
        evidence_id=f"S{index}",
        topic=topic,
        claim=f"Grounded finding {index} changes how an agent selects an action.",
        citations=(f"S{index}",),
        kind=kind,
        importance=0.5,
        complexity=complexity,
        detail=detail,
        chart=chart,
    )


def _chart(*, citations: tuple[str, ...] = ("S1",)) -> ChartEvidence:
    return ChartEvidence(
        chart_type="line",
        title="Measured cumulative reward",
        labels=("Baseline", "Trained agent"),
        series=(ChartSeries("Reward", (41.0, 67.0)),),
        citations=citations,
        unit="points",
    )


def test_autonomous_mode_derives_scene_count_and_honors_seconds_runtime() -> None:
    plan = plan_video(
        [_evidence(index) for index in range(1, 6)],
        {
            "design_control": "auto",
            "scene_count": 8,
            "duration_seconds": 180,
            "evidence_depth": "balanced",
        },
    )

    assert plan.mode is PlanningMode.AUTONOMOUS
    assert plan.scene_count == 3
    assert plan.scene_count != 8
    assert plan.total_duration_seconds == 180


def test_minimum_duration_stretches_real_scene_instead_of_adding_filler() -> None:
    plan = plan_video([_evidence(1)])

    assert plan.scene_count == 1
    assert plan.total_duration_seconds == 30
    assert plan.scenes[0].phase is NarrativePhase.COMPLETE
    assert plan.scenes[0].evidence_ids == ("S1",)
    assert any(
        warning.code == "minimum_duration_uses_visual_hold" for warning in plan.warnings
    )


def test_narrative_complexity_increases_natural_runtime() -> None:
    simple = plan_video(
        [
            _evidence(index, topic=f"Topic {index}", complexity=0.05)
            for index in range(1, 5)
        ]
    )
    complex_plan = plan_video(
        [
            _evidence(index, topic=f"Topic {index}", complexity=1.0)
            for index in range(1, 5)
        ]
    )

    assert complex_plan.pace is Pace.CALM
    assert complex_plan.total_duration_seconds > simple.total_duration_seconds


def test_comprehensive_evidence_creates_more_scenes_without_empty_padding() -> None:
    evidence = [_evidence(index) for index in range(1, 7)]

    focused = plan_video(evidence, {"evidence_depth": "focused"})
    comprehensive = plan_video(evidence, {"evidence_depth": "comprehensive"})

    assert focused.scene_count == 2
    assert comprehensive.scene_count == 6
    assert all(scene.evidence_ids and scene.citations for scene in comprehensive.scenes)


def test_auto_scene_count_ignores_a_stale_target_and_never_pads_evidence() -> None:
    short = plan_video(
        [_evidence(1), _evidence(2)],
        {"design_control": "custom", "scene_count": 8},
    )
    long = plan_video(
        [_evidence(index, topic=f"Topic {index}") for index in range(1, 6)],
        {
            "design_control": "custom",
            "scene_count": 2,
            "evidence_depth": "comprehensive",
        },
    )

    assert short.scene_count == 1
    assert long.scene_count == 5
    assert {item for scene in long.scenes for item in scene.evidence_ids} == {
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
    }
    assert not any(warning.code == "soft_scene_target_not_forced" for warning in long.warnings)


def test_guided_maximum_scene_count_keeps_only_real_high_priority_scenes() -> None:
    plan = plan_video(
        [_evidence(index, topic=f"Topic {index}") for index in range(1, 6)],
        {
            "design_control": "custom",
            "scene_count_strategy": "maximum",
            "scene_count": 2,
            "evidence_depth": "comprehensive",
        },
    )

    assert plan.scene_count == 2
    assert all(scene.evidence_ids for scene in plan.scenes)
    assert any(warning.code == "scene_capacity_reached" for warning in plan.warnings)
    assert set(plan.rejected_evidence_ids) == {"S3", "S4", "S5"}


def test_duration_seconds_is_exact_without_filler_scenes() -> None:
    plan = plan_video(
        [_evidence(1), _evidence(2)],
        {
            "design_control": "custom",
            "duration_seconds": 1_200,
            "scene_count": 2,
        },
    )

    assert plan.total_duration_seconds == 1_200
    assert plan.scene_count == 1
    assert any(warning.code == "duration_target_uses_visual_hold" for warning in plan.warnings)


def test_short_duration_seconds_keeps_the_requested_runtime() -> None:
    detail = " ".join(["documented explanation"] * 80)
    plan = plan_video(
        [
            _evidence(index, topic=f"Topic {index}", detail=detail)
            for index in range(1, 5)
        ],
        {"design_control": "custom", "duration_seconds": 30},
    )

    assert plan.total_duration_seconds == 30
    assert any(warning.code == "duration_target_compresses_evidence" for warning in plan.warnings)


def test_placeholder_uncited_and_duplicate_claims_are_quarantined() -> None:
    plan = plan_video(
        [
            _evidence(1),
            {"id": "empty", "topic": "Topic", "claim": "", "citations": ["S2"]},
            {
                "id": "placeholder",
                "topic": "Topic",
                "claim": "Scene",
                "citations": ["S3"],
            },
            {
                "id": "uncited",
                "topic": "Topic",
                "claim": "Unsupported statement",
                "citations": [],
            },
            replace(_evidence(4), claim=_evidence(1).claim),
        ]
    )

    assert set(plan.rejected_evidence_ids) == {"empty", "placeholder", "uncited", "S4"}
    assert {item for scene in plan.scenes for item in scene.evidence_ids} == {"S1"}


def test_planning_fails_when_no_grounded_evidence_remains() -> None:
    with pytest.raises(VideoPlanningError, match="No cited"):
        plan_video(
            [{"id": "bad", "topic": "Context", "claim": "Filler", "citations": []}]
        )


def test_illustrative_or_uncited_chart_is_removed_without_replacement() -> None:
    chart = replace(_chart(citations=("S99",)), illustrative=True)
    plan = plan_video([_evidence(1, kind=EvidenceKind.METRIC, chart=chart)])

    scene = plan.scenes[0]
    assert scene.chart is None
    assert scene.visual_intent != "data"
    assert scene.media_request is not None
    assert any(warning.code == "ungrounded_chart_rejected" for warning in plan.warnings)


def test_only_documented_numeric_evidence_produces_data_visual_intent() -> None:
    plan = plan_video([_evidence(1, kind=EvidenceKind.METRIC, chart=_chart())])

    scene = plan.scenes[0]
    assert scene.chart == _chart()
    assert scene.visual_intent == "data"
    assert scene.media_request is None
    assert set(scene.chart.citations).issubset(scene.citations)


def test_valid_charts_can_be_disabled_without_fabricated_visuals() -> None:
    plan = plan_video(
        [_evidence(1, kind=EvidenceKind.METRIC, chart=_chart())],
        {"include_charts": False},
    )

    assert plan.scenes[0].chart is None
    assert plan.scenes[0].visual_intent != "data"
    assert plan.scenes[0].media_request is not None


def test_media_query_contains_only_source_subject_and_explicit_provenance() -> None:
    evidence = {
        "id": "fact-1",
        "topic": "[S22] Autonomous underwater vehicle S11",
        "claim": "The vehicle corrects depth after a documented disturbance [S22].",
        "citations": ["S22"],
        "kind": "case_study",
    }
    plan = plan_video([evidence], {"media_mode": "video_first"})

    request = plan.scenes[0].media_request
    assert request is not None
    assert "Autonomous underwater vehicle" in request.query
    assert "corrects depth" in request.query
    assert "S22" not in request.query
    assert "S11" not in request.query
    assert request.source_evidence_ids == ("fact-1",)
    assert request.citations == ("S22",)
    assert request.require_api is True
    assert request.allow_generated is False


def test_motion_graphics_requests_only_source_extracted_figures() -> None:
    plan = plan_video(
        [_evidence(1, kind=EvidenceKind.CONCEPT)],
        {"media_mode": "motion_graphics"},
    )

    request = plan.scenes[0].media_request
    assert request is not None
    assert request.asset_types == ("source_figure", "source_diagram")
    assert request.provider_policy == ("source-extraction-api",)


def test_problem_and_action_evidence_select_problem_solution_arc() -> None:
    plan = plan_video(
        [
            _evidence(1, topic="Training cost", kind=EvidenceKind.LIMITATION),
            _evidence(2, topic="Efficient replay", kind=EvidenceKind.ACTION),
        ]
    )

    assert plan.narrative_arc == "problem_solution"


def test_existing_scenes_receive_hook_and_close_phases_without_synthetic_scenes() -> None:
    plan = plan_video(
        [_evidence(index, topic=f"Topic {index}") for index in range(1, 4)],
        {"evidence_depth": "comprehensive"},
    )

    assert [scene.phase for scene in plan.scenes] == [
        NarrativePhase.HOOK,
        NarrativePhase.DEVELOPMENT,
        NarrativePhase.CLOSE,
    ]
    assert [scene.evidence_ids for scene in plan.scenes] == [("S1",), ("S2",), ("S3",)]


def test_process_and_timeline_intents_require_enough_grounded_items() -> None:
    one_process = plan_video([_evidence(1, kind=EvidenceKind.PROCESS)])
    real_process = plan_video(
        [_evidence(1, kind=EvidenceKind.PROCESS), _evidence(2, kind=EvidenceKind.PROCESS)]
    )
    short_timeline = plan_video(
        [_evidence(1, kind=EvidenceKind.TIMELINE), _evidence(2, kind=EvidenceKind.TIMELINE)]
    )
    real_timeline = plan_video(
        [_evidence(index, kind=EvidenceKind.TIMELINE) for index in range(1, 4)]
    )

    assert one_process.scenes[0].visual_intent == "footage"
    assert real_process.scenes[0].visual_intent == "process"
    assert short_timeline.scenes[0].visual_intent == "footage"
    assert real_timeline.scenes[0].visual_intent == "timeline"


def test_auto_language_keeps_objectives_in_persian() -> None:
    plan = plan_video(
        [
            {
                "id": "S1",
                "topic": "یادگیری تقویتی",
                "claim": "عامل براساس پاداش، سیاست تصمیم‌گیری خود را اصلاح می‌کند.",
                "citations": ["S1"],
                "kind": "concept",
            }
        ]
    )

    assert "توضیح" in plan.scenes[0].objective


def test_formula_damaged_ocr_never_becomes_a_video_title_or_media_query() -> None:
    plan = plan_video(
        [
            {
                "id": "S1",
                "topic": "0 sin t z z zdt z = θ = + ∫ n× x q× p×",
                "claim": (
                    "0 sin t z z zdt z = θ = + ∫ n× x q× p× "
                    "عامل هوشمند با آزمون و خطا، سیاست تصمیم‌گیری خود را اصلاح می‌کند."
                ),
                "citations": ["S1"],
                "kind": "concept",
            }
        ],
        {"language": "fa", "media_mode": "video_first"},
    )

    scene = plan.scenes[0]
    assert scene.title.startswith("عامل هوشمند")
    assert "=" not in scene.title
    assert scene.media_request is not None
    assert "zdt" not in scene.media_request.query.casefold()
    assert "=" not in scene.media_request.query


def test_planning_is_deterministic_and_does_not_mutate_input() -> None:
    raw = [
        {
            "id": "S1",
            "topic": "Policy learning",
            "claim": "The cited policy maps states to actions.",
            "citations": ["S1"],
        }
    ]
    original = [dict(raw[0], citations=list(raw[0]["citations"]))]

    first = plan_video(raw)
    second = plan_video(raw)

    assert first == second
    assert raw == original
