from paperlens.visual_direction import direct_presentation, direct_video


def test_presentation_director_builds_a_semantic_varied_deck_in_auto_mode():
    plan = {
        "title": "Reliable retrieval",
        "slides": [
            {
                "title": "Reliable retrieval",
                "subtitle": "A grounded story",
                "image_queries": ["researcher validating information retrieval evidence"],
            },
            {"title": "Three recurring failure modes", "bullets": ["Recall", "Noise", "Trust"]},
            {
                "title": "Accuracy improves with evidence",
                "chart": {"labels": ["Before", "After"], "values": [61, 84]},
            },
            {"title": "A repeatable workflow", "bullets": ["Search", "Rerank", "Answer"]},
            {"title": "Start with one measurable question"},
        ],
    }

    directed = direct_presentation(plan, {"design_control": "auto"}, seed="deck-test")

    assert directed["direction"]["mode"] == "autonomous"
    assert directed["slides"][0]["layout"] == "cover_cinematic"
    assert directed["slides"][-1]["layout"] == "closing_actions"
    assert directed["slides"][2]["layout"] in {"chart_focus", "dashboard"}
    assert all(
        current["layout"] != following["layout"]
        for current, following in zip(directed["slides"], directed["slides"][1:], strict=False)
    )
    assert all(
        slide["mode"] in {"light", "dark", "accent", "photo"} for slide in directed["slides"]
    )


def test_presentation_director_reflows_missing_dependencies_without_inventing_content():
    plan = {
        "title": "یادگیری تقویتی",
        "theme": {"imageTreatment": "duotone", "radius": "small"},
        "slides": [
            {"title": "یادگیری تقویتی", "subtitle": "Reinforcement Learning"},
            {
                "layout": "comparison_dual",
                "title": "اکتشاف در برابر بهره‌برداری",
                "left_items": [{"title": "Exploration"}],
                "right_items": [{"title": "Exploitation"}],
            },
            {
                "layout": "chart_focus",
                "title": "روند مفهومی یادگیری",
                "chart": {"labels": ["شروع"], "series": []},
            },
            {"title": "جمع‌بندی"},
        ],
    }

    directed = direct_presentation(plan, {"design_control": "auto"}, seed="repair-test")

    cover = directed["slides"][0]
    comparison = directed["slides"][1]
    chart = directed["slides"][2]
    assert cover["image_queries"] == []
    assert cover["layout"] == "big_statement"
    assert comparison["left_items"] == []
    assert comparison["right_items"] == []
    assert comparison["layout"] != "comparison_dual"
    assert chart["layout"] == "big_statement"
    assert chart["chart"] == {}
    assert "conceptual" not in chart["footnote"].casefold()
    assert directed["theme"]["image_treatment"] == "duotone"
    assert directed["theme"]["radius"] == "small"
    assert directed["direction"]["contract_version"] == "paperlens.visual-plan.v1"


def test_presentation_director_keeps_real_item_counts_and_removes_empty_placeholders():
    plan = {
        "title": "راهنمای تصمیم‌گیری",
        "slides": [
            {"title": "راهنمای تصمیم‌گیری"},
            {
                "layout": "process_flow",
                "title": "فرایند تصمیم‌گیری",
                "items": [
                    {"title": "مشاهده", "body": "داده‌های محیط را دریافت کنید."},
                    {"title": "اقدام", "body": "بهترین کنش معتبر را اجرا کنید."},
                    {"title": "Decision", "value": "03", "body": ""},
                ],
            },
            {
                "title": "گام‌های بعدی",
                "items": [
                    {"title": "آزمایش", "body": "با یک نمونه واقعی شروع کنید."},
                    {"title": "اندازه‌گیری", "body": "نتیجه را با معیار معتبر بسنجید."},
                ],
            },
        ],
    }

    directed = direct_presentation(plan, {"design_control": "auto"}, seed="rtl-items")

    process = directed["slides"][1]
    closing = directed["slides"][2]
    assert process["reading_direction"] == "rtl"
    assert [item["title"] for item in process["items"]] == ["اقدام", "مشاهده"]
    assert [item["title"] for item in closing["items"]] == ["اندازه‌گیری", "آزمایش"]
    assert all(
        item["title"].casefold() not in {"context", "evidence", "decision", "action"}
        for slide in directed["slides"]
        for item in slide["items"]
    )


def test_presentation_director_never_fabricates_a_chart_when_numeric_data_is_invalid():
    plan = {
        "title": "Evidence quality",
        "slides": [
            {"title": "Evidence quality"},
            {
                "layout": "chart_focus",
                "title": "Measured outcomes",
                "items": [
                    {"title": "Coverage", "body": "Reported by the source."},
                    {"title": "Latency", "body": "Measured in the evaluation."},
                ],
                "chart": {"labels": ["Before", "After"], "values": [71]},
            },
            {"title": "Use verified evidence"},
        ],
    }

    directed = direct_presentation(plan, {"design_control": "auto"}, seed="real-chart")
    repaired = directed["slides"][1]

    assert repaired["layout"] == "cards_icon"
    assert repaired["chart"] == {}
    assert len(repaired["items"]) == 2
    assert "conceptual decision path" not in str(directed).casefold()


def test_presentation_director_rejects_legacy_conceptual_chart_signature():
    plan = {
        "title": "Evidence quality",
        "slides": [
            {"title": "Evidence quality"},
            {
                "layout": "chart_focus",
                "title": "Decision path",
                "chart": {
                    "type": "bar",
                    "title": "Conceptual decision path",
                    "labels": ["Context", "Evidence", "Decision"],
                    "values": [1, 2, 3],
                },
            },
            {"title": "Use verified evidence"},
        ],
    }

    directed = direct_presentation(plan, {"design_control": "auto"}, seed="legacy-chart")
    repaired = directed["slides"][1]

    assert repaired["layout"] == "big_statement"
    assert repaired["chart"] == {}


def test_presentation_director_strips_source_ids_from_media_queries():
    plan = {
        "title": "Reinforcement learning [S22]",
        "slides": [
            {
                "title": "Reinforcement learning [S22]",
                "image_queries": [
                    "S22 S11 people",
                    "[14S] reinforcement learning agent environment",
                ],
            },
            {"title": "Summary"},
        ],
    }

    directed = direct_presentation(plan, {"design_control": "auto"}, seed="media-query")
    queries = directed["slides"][0]["image_queries"]

    assert queries == ["reinforcement learning agent environment"]
    assert all("s22" not in query.casefold() and "14s" not in query.casefold() for query in queries)


def test_video_director_creates_scene_composition_and_respects_explicit_custom_choices():
    plan = {
        "title": "How retrieval becomes evidence",
        "scenes": [
            {
                "title": "The question",
                "on_screen_text": "Start with intent",
                "visual_intent": "statement",
                "media_queries": ["researcher formulating evidence question"],
            },
            {
                "title": "Search and rerank",
                "on_screen_text": "Search, rerank, verify",
                "visual_intent": "process flow",
                "media_queries": ["scientist reviewing search results laboratory"],
            },
            {
                "title": "Compare the evidence",
                "on_screen_text": "Before vs after",
                "visual_intent": "comparison",
                "media_queries": ["analyst comparing model evaluation results"],
            },
            {
                "title": "The result",
                "on_screen_text": "Evidence you can inspect",
                "visual_intent": "statement",
                "media_queries": ["research team validating documented evidence"],
            },
        ],
    }

    directed = direct_video(
        plan,
        {
            "design_control": "custom",
            "video_style": "cinematic",
            "pace": "dynamic",
            "transition_style": "auto",
            "media_mode": "auto",
        },
        seed="video-test",
    )

    assert directed["direction"]["style"] == "cinematic"
    assert directed["direction"]["energy"] == "dynamic"
    assert directed["scenes"][0]["visual"]["layout"] == "hero_cinematic"
    assert directed["scenes"][-1]["visual"]["layout"] == "closing_statement"
    assert directed["scenes"][1]["visual"]["layout"] == "process"
    assert directed["scenes"][2]["visual"]["layout"] == "comparison"
    assert all(len(scene["media_queries"]) >= 1 for scene in directed["scenes"])
    assert all("transition" in scene["visual"] for scene in directed["scenes"])


def test_video_director_preserves_the_complete_direction_contract():
    plan = {
        "title": "Digital focus and notification addiction",
        "scenes": [
            {"title": "The interruption loop", "on_screen_text": "وقفه مداوم"},
            {
                "title": "Measure the cost",
                "on_screen_text": "داده، تصمیم، اقدام",
                "visual_intent": "data",
            },
            {
                "title": "Build a focus ritual",
                "on_screen_text": "مشاهده، آزمایش، بهبود",
                "visual_intent": "process",
            },
            {"title": "Protect deep work", "on_screen_text": "تمرکز را طراحی کنید"},
        ],
    }

    directed = direct_video(plan, {"video_style": "auto", "pace": "auto"}, seed="video")

    assert directed["direction"]["contract_version"] == "paperlens.visual-plan.v1"
    assert directed["direction"]["version"] == "7.0.4-paperlens"
    assert directed["direction"]["profile"] == "technology"
    assert directed["direction"]["music"]["purpose"] == "voiceover-bed"
    assert directed["scenes"][1]["visual"]["layout"] != "data_bars"
    assert directed["scenes"][1]["visual"]["chart"] is None
    for index, scene in enumerate(directed["scenes"], start=1):
        visual = scene["visual"]
        assert visual["icon"]
        assert visual["number"] == f"{index:02d}"
        assert visual["decorative_seed"]
        assert set(("motion", "transition", "transition_duration", "treatment", "typing")) <= set(
            visual
        )
        assert set(("enabled", "cps", "variance", "cursor", "cursor_hold", "initial_delay")) <= set(
            visual["typing"]
        )
        assert scene["media_policy"] == ["video", "photo", "card"]


def test_video_director_uses_only_authored_numeric_chart_data():
    plan = {
        "title": "Measured performance",
        "scenes": [
            {"title": "The question", "on_screen_text": "What changed?"},
            {
                "title": "Measured result",
                "on_screen_text": "A sourced comparison",
                "visual_intent": "data",
                "chart": {
                    "type": "bar",
                    "title": "Evaluation score",
                    "labels": ["Baseline", "Proposed"],
                    "values": [71, 86],
                },
                "media_queries": ["S14 S22 people", "model evaluation laboratory"],
            },
            {"title": "The conclusion", "on_screen_text": "Use measured evidence."},
        ],
    }

    directed = direct_video(plan, {"video_style": "auto", "pace": "auto"}, seed="video-data")
    result = directed["scenes"][1]

    assert result["visual"]["layout"] == "data_bars"
    assert result["visual"]["chart"]["values"] == [71, 86]
    assert result["visual"]["chart"]["illustrative"] is False
    assert result["media_queries"][0] == "model evaluation laboratory"
