from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

from paperlens import webapp
from paperlens.fonts import FontRegistry

WEB_ROOT = Path(__file__).parents[1] / "src" / "paperlens" / "web"


def test_browser_module_is_served_as_javascript() -> None:
    response = TestClient(webapp.app).get("/studio_live.mjs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.ids.extend(value for key, value in attrs if key == "id" and value is not None)


def test_first_screen_is_workspace_chat_and_ids_are_unique() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    parser = _IdCollector()
    parser.feed(html)

    assert len(parser.ids) == len(set(parser.ids))
    assert html.index('id="view-chat"') < html.index('id="view-documents"')
    for element_id in (
        "top-workspace-select",
        "top-new-workspace",
        "chat-document-filter",
        "messages",
        "question-input",
        "evidence-panel",
        "trace-shortcut",
    ):
        assert parser.ids.count(element_id) == 1


def test_collapsed_primary_navigation_keeps_accessible_names() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    navigation = html.split('<nav class="nav-list"', maxsplit=1)[1].split(
        "</nav>", maxsplit=1
    )[0]

    assert navigation.count('class="nav-item') == 8
    assert navigation.count('data-i18n-aria="nav.') == 8
    new_chat = html.split('id="new-chat"', maxsplit=1)[1].split(">", maxsplit=1)[0]
    assert 'aria-label="New conversation"' in new_chat
    assert 'data-i18n-aria="nav.newChat"' in new_chat
    prompt_file_input = html.split('id="prompt-image-input"', maxsplit=1)[1].split(
        ">", maxsplit=1
    )[0]
    assert 'data-i18n-aria="chat.attachImage"' in prompt_file_input


def test_document_upload_and_extraction_report_controls_are_accessible() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="pdf-input"' in html
    for source in (".pdf", ".txt", ".png", ".mp3"):
        assert source in html
    assert "multiple" in html
    assert 'id="upload-status"' in html
    assert 'aria-live="polite"' in html
    for element_id in (
        "documents-list",
        "processing-summary",
        "extraction-distribution",
        "top-locations",
        "chunking-strategy",
        "rebuild-vector",
        "paste-text-form",
        "paste-content",
    ):
        assert f'id="{element_id}"' in html


def test_frontend_scopes_chat_vector_and_trace_requests_by_workspace() -> None:
    script = "\n".join(
        (WEB_ROOT / name).read_text(encoding="utf-8") for name in ("app.js", "state.mjs")
    )

    for field in (
        "activeWorkspaceId",
        "selectedDocumentIds",
        "workspace_id",
        "document_ids",
        "paperlens.sessions.v2",
    ):
        assert field in script
    for route in (
        "/api/workspaces",
        "/documents",
        "/vector-store",
        "/api/chat",
        "/api/traces?workspace_id=",
    ):
        assert route in script
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    assert "tool_choice=auto" not in html


def test_evidence_drawer_supports_text_image_and_clickable_pages() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert 'id="evidence-text-preview"' in html
    assert 'id="evidence-visual-preview"' in html
    assert 'id="evidence-warnings"' in html
    assert 'class="evidence-card"' in script
    assert "page_preview_url" in script
    assert "pdf_url" in script
    assert "citationButtons" in script


def test_runtime_architecture_and_trace_fields_are_factual() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    for phrase in (
        "PDF، متن، تصویر و صوت",
        "استخراج هوشمند",
        "جست‌وجوی معنایی و واژگانی",
        "پاسخ و گزارش",
    ):
        assert phrase in html
    for field in (
        "model_routes",
        "llm_failover",
        "embedding_failover",
        "embedding_dimensions",
        "last_backend",
        "selected_evidence",
        "token_usage",
        "cost_estimate",
    ):
        assert field in script
    assert "global_requests_per_minute" in script
    assert "worker_count" in script
    assert "status-spinner" in script
    assert "processing-events" in script


def test_readable_rtl_typography_and_font_assets_are_present() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert '<html lang="fa" dir="rtl">' in html
    assert "font-size:16px" in css
    assert ".message-bubble{max-width:88%" in css
    assert "font-size:15.5px" in css
    assert ".evidence-card p{font-size:13px" in css
    assert "prefers-reduced-motion:reduce" in css
    assert ".visually-hidden" in css
    for filename in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf"):
        path = FontRegistry.default_bundled_root() / "Vazirmatn" / filename
        assert path.is_file()
        assert path.stat().st_size > 10_000
        assert f"/fonts/{filename}" in css


def test_polished_workspace_navigation_and_upload_feedback_are_wired() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for element_id in ("sidebar-scrim", "sidebar-close", "selected-files", "composer-scope"):
        assert f'id="{element_id}"' in html
    for behavior in (
        "renderSelectedFiles",
        'addEventListener("change"',
        "setUploadPending",
        "location.hash",
        "newSession({activateView: false})",
    ):
        assert behavior in script
    for selector in (
        ".selected-file",
        ".sidebar-scrim",
        ".composer-shell",
        ".welcome-visual",
    ):
        assert selector in css


def test_notebook_workspace_has_bilingual_sources_chat_and_studio_controls() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for element_id in (
        "language-toggle",
        "notebook-sources",
        "notebook-studio",
        "prompt-image-input",
        "prompt-attachment-preview",
        "studio-actions",
        "studio-artifacts",
    ):
        assert f'id="{element_id}"' in html
    for behavior in (
        "/api/chat/image",
        "/studio/artifacts",
        "setLocale",
        "renderStudioArtifacts",
        "promptImage",
    ):
        assert behavior in script
    for selector in (
        ".notebook-grid",
        ".sources-rail",
        ".studio-rail",
        ".prompt-attachment",
        ".studio-action",
    ):
        assert selector in css


def test_desktop_workspace_gives_evidence_a_full_context_panel() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for element_id in (
        "top-workspace-select",
        "context-tab-studio",
        "context-tab-evidence",
        "context-panel-studio",
        "context-panel-evidence",
    ):
        assert f'id="{element_id}"' in html
    assert '<details class="evidence-peek">' not in html
    assert 'activateContextPanel("evidence")' in script
    assert ".context-panel.active" in css
    assert "--sidebar-width:88px" in css
    assert "minmax(340px, 380px)" in css


def test_markdown_outputs_have_readable_document_styles() -> None:
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for selector in (
        ".message-bubble ul",
        ".message-bubble blockquote",
        ".studio-dialog-body table",
        ".math-expression",
    ):
        assert selector in css
    assert "width:min(1040px" in css


def test_sourcelens_reference_design_covers_the_complete_product_workspace() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "SourceLens" in html
    for view in (
        "chat",
        "workspaces",
        "documents",
        "history",
        "vector",
        "traces",
        "providers",
        "settings",
    ):
        assert f'id="view-{view}"' in html
    for element_id in (
        "global-search",
        "workspace-cards",
        "history-library",
        "provider-settings",
        "provider-dialog",
        "provider-key-input",
    ):
        assert f'id="{element_id}"' in html
    for behavior in (
        "loadProviderSettings",
        "saveProviderCredentials",
        "/api/provider-settings",
        "renderWorkspaceCards",
        "renderHistoryLibrary",
    ):
        assert behavior in script
    for design_token in (
        "--sidebar-width: 226px",
        "--font-display",
        "oklch(",
        ".provider-card",
        ".workspace-cards",
    ):
        assert design_token in css


def test_product_shell_covers_overlays_activity_and_context_rails() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for element_id in (
        "activity-overview",
        "activity-timeline",
        "activity-detail-dialog",
        "provider-create",
        "provider-base-url",
        "provider-adapter",
        "provider-model-editor",
        "provider-runtime-settings",
    ):
        assert f'id="{element_id}"' in html
    for contract in (
        "--palette-bg:",
        "--palette-surface:",
        "--palette-accent:",
        ".modal-shell",
        ".activity-timeline",
        ".context-rail",
        ".studio-dialog-card",
        ".evidence-drawer-card",
    ):
        assert contract in css


def test_provider_workspace_supports_custom_connections_and_priority_controls() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "provider-create",
        "provider-enabled",
        "provider-base-url",
        "provider-env-variable",
        "provider-capabilities",
        "provider-model-editor",
        "provider-add-model",
        "provider-delete",
        "provider-runtime-form",
    ):
        assert f'id="{element_id}"' in html
    for behavior in (
        'api("/api/provider-settings", {method: "POST"',
        'api("/api/provider-settings", {method: "PATCH"',
        'method: "DELETE"',
        "moveProvider",
        "moveProviderModel",
        "renderProviderModelEditor",
        "saveProviderRuntimeSettings",
    ):
        assert behavior in script


def test_provider_models_share_one_interleaved_global_priority_chain() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert 'id="global-model-priority"' in html
    assert 'id="global-model-priority-list"' in html
    assert "state.modelRoutes" in script
    assert "route.route_id" in script
    assert "moveGlobalModel" in script
    assert "data-move-global-model" in script
    assert ".global-model-priority" in css
    assert ".global-model-route" in css


def test_source_cards_are_spacious_readable_and_responsive() -> None:
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "repeat(auto-fit,minmax(min(100%,480px),1fr))" in css
    assert ".document-card>header" in css
    assert ".extraction-metrics" in css
    assert ".extraction-metric" in css
    assert 'class="extraction-metric text"' in script
    assert 'class="extraction-metric ocr"' in script
    assert 'class="extraction-metric hybrid"' in script
    assert 'class="extraction-metric vlm"' in script
    assert 'class="extraction-metric failed"' in script
    assert ".processing-event{" in css and "minmax(0,1fr)" in css
    assert ".upload-copy>div{display:grid" in css


def test_english_locale_uses_a_latin_numeral_font_stack() -> None:
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert '.locale-en{--font-ui:"Segoe UI",Arial,sans-serif}' in css


def test_dialogs_close_without_submitting_and_keep_scrolling_inside_the_frame() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert 'id="provider-dialog-close" class="icon-button" type="button"' in html
    assert 'id="provider-dialog-cancel" type="button"' in html
    assert 'id="workspace-dialog-close"' in html
    assert 'id="confirm-dialog-close"' in html
    assert 'data-close-dialog="provider-dialog"' in html
    assert "closeDialog" in script
    assert '$("#workspace-form").addEventListener("submit"' in script
    assert 'event.submitter?.value === "cancel"' not in script
    assert ".modal-shell,dialog{" in css and "overflow:hidden" in css
    assert ".provider-dialog-card{" in css and "min-height:0" in css
    assert ".provider-model-editor{" in css and "overscroll-behavior:contain" in css
    assert "scrollbar-gutter:stable" in css


def test_sidebar_has_one_workspace_switcher_and_all_navigation_remains_scrollable() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert 'class="workspace-block"' not in html
    assert 'id="workspace-select"' not in html
    assert 'id="workspace-summary"' not in html
    assert html.count('id="top-workspace-select"') == 1
    assert '$("#workspace-select")' not in script
    assert ".sidebar{" in css and "overflow-y:auto" in css
    assert "overscroll-behavior:contain" in css


def test_product_shell_uses_a_muted_green_and_cream_palette() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    favicon = (WEB_ROOT / "favicon.svg").read_text(encoding="utf-8")

    assert '<meta name="theme-color" content="#0d3b36">' in html
    for token in (
        "--palette-bg:#f4f1e7",
        "--palette-surface:#fffdf7",
        "--palette-raised:#e8ebe0",
        "--palette-accent:#1f6a5d",
        "--sidebar-bg:#0d3b36",
        "--chart-2:#b9a66f",
    ):
        assert token in css
    favicon_link = (
        '<link rel="icon" href="/favicon.svg?v=20260802-visual-research-v1" '
        'type="image/svg+xml">'
    )
    assert favicon_link in html
    assert (WEB_ROOT / "favicon.svg").is_file()
    assert "#b86448" not in favicon
    assert '#f4f1e7' in favicon
    assert '#1f6a5d' in favicon


def test_button_backgrounds_do_not_reintroduce_the_old_pink_palette() -> None:
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "background:color-mix(in oklch,var(--danger)" not in css
    secondary_controls = (
        ".secondary-button,.trace-button,.top-upload{border-color:var(--line);"
        "background:var(--surface-soft)"
    )
    danger_controls = (
        ".danger-button{border-color:color-mix(in oklch,var(--danger) 35%,var(--line));"
        "background:var(--surface-soft)"
    )
    assert secondary_controls in css
    assert danger_controls in css
    assert ".danger-button:hover{background:var(--surface-muted)}" in css


def test_chat_shell_omits_demo_quick_prompts_top_status_and_evidence_badge() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    for marker in (
        'data-view="demo"',
        'id="view-demo"',
        'id="seed-demo"',
        'id="api-status"',
        'id="evidence-count"',
        'class="suggestions"',
    ):
        assert marker not in html + app
    assert "bindSuggestionButtons" not in app
    assert 'if (name === "demo")' not in app


def test_sources_have_separated_cards_and_architecture_reads_saved_runtime_settings() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for element_id in (
        "arch-extraction-state",
        "arch-retrieval-state",
        "arch-scope-state",
        "arch-answer-state",
    ):
        assert f'id="{element_id}"' in html
    assert ".source-scope-list{display:grid" in css
    assert "gap:9px" in css
    assert ".source-scope-item{min-height:68px" in css
    assert ".source-scope-item strong{display:block" in css
    assert 'api("/api/provider-settings")' in app
    for setting in (
        "providerSettings.runtime",
        "llm_timeout_seconds",
        "llm_pool_retry_rounds",
        "retrieval_top_k",
        "retrieval_min_similarity",
    ):
        assert setting in app


def test_studio_actions_open_a_customization_dialog_and_do_not_run_immediately() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="studio-create-dialog"' in html
    assert 'id="studio-create-form"' in html
    assert html.count("data-artifact-type=") == 13
    for artifact_type in (
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
    ):
        assert f'data-artifact-type="{artifact_type}"' in html
    assert "openStudioCustomization" in app
    assert '$("#studio-create-form").addEventListener("submit"' in app


def test_chat_uses_server_conversation_identity_and_restores_remote_turns() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    assert "/conversations`" in app
    assert "loadSessionDetails" in app
    assert "conversation_id: session.id" in app
    assert 'form.append("conversation_id", session.id)' in app


def test_studio_viewer_falls_back_to_legacy_markdown_and_select_options_are_readable() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "structuredStudioMarkup" in app
    assert "item.html" in app
    assert "select option" in css
    assert "select option" in css


def test_every_studio_customization_has_localized_field_and_choice_labels() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    for field in (
        "custom_report_type",
        "detail_level",
        "relationship_detail",
        "card_style",
        "passing_score",
        "visual_emphasis",
        "missing_value_policy",
        "chart_type",
        "granularity",
        "comparison_style",
        "gap_scope",
    ):
        assert f"{field}: [" in app
    for choice in (
        "general_overview",
        "academic_review",
        "technical_analysis",
        "literature_review",
        "definition",
        "application",
        "mark_unknown",
        "reverse_chronological",
        "evidence_inferred",
    ):
        assert f"{choice}: [" in app
    assert "syncStudioConditionalFields" in app
    assert (
        '[data-studio-option="deck_format"], '
        '[data-studio-option="slide_count_strategy"]'
    ) in app
    assert '[data-studio-option="scene_count_strategy"]' in app


def test_quiz_and_all_chart_types_have_dedicated_interactive_viewers() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "data-quiz-choice" in app
    assert "answerQuizChoice" in app
    assert "chartBarsMarkup" in app
    assert "chartPlotMarkup" in app
    assert "chartPieMarkup" in app
    assert ".quiz-question.answered" in css
    assert ".chart-legend" in css
    assert "studioRichText" in app


def test_audio_overview_has_full_configuration_review_progress_and_playback_ui() -> None:
    html = WEB_ROOT.joinpath("index.html").read_text(encoding="utf-8")
    app = WEB_ROOT.joinpath("app.js").read_text(encoding="utf-8")
    css = WEB_ROOT.joinpath("styles.css").read_text(encoding="utf-8")

    assert 'data-artifact-type="audio_overview"' in html
    for marker in (
        "podcast-participants-editor",
        "data-podcast-add-participant",
        "data-podcast-save-script",
        "data-podcast-generate",
        "data-podcast-cancel",
        "podcast-progress-timeline",
        "podcast-audio-player",
        "podcast-script-editor",
        "podcastTranscriptMarkup",
    ):
        assert marker in app
    assert ".podcast-studio" in css
    assert ".podcast-script-turn" in css
    assert ".podcast-progress-timeline" in css
    assert "overflow-y:auto" in css
    assert "20260802-visual-research-v1" in html
    assert 'const reviewable = config.production_mode === "scripted";' in app
    assert "generatedCount" in app
    assert "backend قدیمی است" in app
    assert 'state.studioCatalog = await api("/api/studio/catalog")' in app


def test_flashcards_are_a_focused_accessible_deck_and_detail_dialogs_scroll() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for contract in (
        "flashcard-deck",
        "flashcard-slide",
        "data-flashcard-prev",
        "data-flashcard-flip",
        "data-flashcard-next",
        'aria-live="polite"',
        'aria-pressed="false"',
        "Reveal flashcard answer",
        "rendered_data",
        "studioMarkdown",
        "updateFlashcardDeck",
        "navigateFlashcards",
        "toggleFlashcard",
    ):
        assert contract in app
    assert "studio-create-card{gap:20px;padding:0;overflow-y:auto" in css
    assert ".studio-dialog-card{display:flex;height:min(92dvh,900px);min-height:0" in css
    assert 'id="studio-dialog-body"' in html


def test_mind_map_is_an_accessible_collapsible_diagram_with_node_details() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for contract in (
        "buildConceptMapTree",
        "data-mind-map-node",
        'aria-expanded="',
        'data-mind-map-action="expand-all"',
        'data-mind-map-action="collapse-all"',
        "data-mind-map-detail",
        "toggleMindMapNode",
        "selectMindMapNode",
        "initializeMindMapViewport",
        "fitMindMap",
        'addEventListener("pointerdown"',
        'addEventListener("wheel"',
        "setPointerCapture",
    ):
        assert contract in app
    assert app.index('class="mind-map-detail"') < app.index('class="mind-map-viewport"')
    for selector in (
        ".mind-map-workbench",
        ".mind-map-viewport",
        ".mind-map-tree",
        ".mind-map-node",
        ".mind-map-children",
        ".mind-map-detail",
    ):
        assert selector in css
    assert "touch-action:none" in css
    assert ".mind-map-canvas" in css
    assert ".mind-map-children[hidden]{display:none!important}" in css
    assert "const expanded = hasChildren && depth === 0;" in app


def test_workspace_and_source_deletion_and_chat_status_contracts() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'id="hero-ready-state"' not in html
    assert "workspace-ready" not in html
    assert '$(".demo-nav")' not in app
    for contract in (
        "data-delete-workspace",
        "data-delete-document",
        "deleteWorkspace",
        "deleteDocument",
    ):
        assert contract in app


def test_source_diagnostics_and_trace_details_are_not_hidden_or_truncated() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")

    for contract in (
        "document-diagnostics",
        "processing-event-details",
        "error_detail",
        "traceback",
        "JSON.stringify(stage, null, 2)",
    ):
        assert contract in app


def test_studio_outputs_isolate_mixed_persian_english_direction_and_render_timeline() -> None:
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    assert "function timelineMarkup" in app
    assert 'class="studio-bidi-${mode}" dir="auto"' in app
    assert 'class="studio-markdown-output" dir="auto"' in app
    assert ".timeline-preview" in css
    assert ".studio-bidi-block" in css
    assert "unicode-bidi:plaintext" in css


def test_web_discovery_and_visual_studio_have_complete_interactive_contracts() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")

    for contract in (
        'data-artifact-type="slide_deck"',
        'data-artifact-type="video_overview"',
        'id="web-source-dialog"',
        'id="web-source-mode"',
        "data-web-source-advanced",
        'id="web-source-results"',
        'id="integration-settings-form"',
        'data-clear-integration="exa_api_keys"',
        'data-clear-integration="tavily_api_keys"',
        'data-clear-integration="pexels_api_keys"',
        'data-clear-integration="jamendo_client_ids"',
    ):
        assert contract in html
    for contract in (
        "searchWebSources",
        "syncWebSourceMode",
        "importSelectedWebSources",
        "renderWebSourceResults",
        "slideDeckMarkup",
        "videoOverviewMarkup",
        'api("/api/integrations"',
        "presentation_url",
        "captions_url",
        "timeline_url",
    ):
        assert contract in app
    for selector in (
        ".web-source-result",
        ".integration-grid",
        ".deck-slide-preview",
        ".video-player-shell",
        ".video-scene-card",
    ):
        assert selector in css


def test_frontend_can_cancel_inflight_requests_and_active_studio_jobs() -> None:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app = "\n".join(
        (WEB_ROOT / name).read_text(encoding="utf-8") for name in ("app.js", "api.mjs")
    )

    assert 'id="cancel-active-requests"' in html
    for contract in (
        "AbortController",
        "cancelActiveFrontendRequests",
        "data-studio-cancel",
        "cancelStudioArtifact",
        "/studio/artifacts/${encodeURIComponent(artifactId)}/cancel",
    ):
        assert contract in app
