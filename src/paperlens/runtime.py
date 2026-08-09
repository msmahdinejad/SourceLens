from __future__ import annotations

from pathlib import Path

from paperlens.assistant import ResearchAssistant
from paperlens.config import Settings
from paperlens.corpus.index import (
    GeminiEmbedder,
    MultilingualE5Embedder,
    OpenAICompatibleEmbedder,
)
from paperlens.ingestion import DocumentIngestionPipeline
from paperlens.knowledge_base import WorkspaceCorpus, WorkspaceKnowledgeBase
from paperlens.language_models.base import LanguageModel
from paperlens.language_models.failover import FailoverLanguageModel, ModelFallbackChain
from paperlens.language_models.openai_chat import OpenAIChatCompletionsModel
from paperlens.language_models.openai_responses import OpenAIResponsesModel
from paperlens.model_routes import configured_model_routes, route_api_keys
from paperlens.ocr import FallbackOCR, OCRSpaceClient, PooledOCR, TesseractOCR
from paperlens.tools.registry import ResearchTools
from paperlens.vlm import OpenAICompatibleVLM, PooledVLM
from paperlens.voice import (
    FallbackTranscriber,
    GroqTranscriber,
    PooledTranscriber,
    WhisperTranscriber,
)
from paperlens.workspaces import WorkspaceStore


def create_embedder(settings: Settings):
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder(
            settings.embedding_model,
            settings.embedding_api_keys(),
            dimensions=settings.embedding_dimensions,
        )
    if settings.embedding_provider == "openai":
        return OpenAICompatibleEmbedder(
            settings.embedding_model,
            settings.embedding_api_keys(),
            base_url=settings.embedding_base_url,
            dimensions=settings.embedding_dimensions,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    return MultilingualE5Embedder(settings.embedding_model)


def create_workspace_store(settings: Settings, project_root: Path) -> WorkspaceStore:
    store = WorkspaceStore(
        settings.resolve_path(settings.workspaces_path, project_root),
        papers_path=settings.resolve_path(settings.papers_path, project_root),
    )
    if settings.seed_demo_workspace:
        seed_demo_workspace(settings, project_root, store)
    else:
        store.ensure_default_workspace()
    return store


def seed_demo_workspace(
    settings: Settings,
    project_root: Path,
    store: WorkspaceStore | None = None,
) -> WorkspaceStore:
    """Install the optional Charge While You Shop workspace."""
    from paperlens.corpus.demo_assets import ensure_demo_papers
    from paperlens.workspace_fixtures import install_demo_fixture

    papers_path = settings.resolve_path(settings.papers_path, project_root)
    ensure_demo_papers(papers_path)
    store = store or WorkspaceStore(
        settings.resolve_path(settings.workspaces_path, project_root),
        papers_path=papers_path,
    )
    install_demo_fixture(store, papers_path)
    return store


def create_knowledge_base(
    settings: Settings,
    project_root: Path,
    *,
    store: WorkspaceStore | None = None,
) -> WorkspaceKnowledgeBase:
    ocr_clients = [
        OCRSpaceClient(
            key,
            timeout_seconds=settings.ocr_timeout_seconds,
            retries=settings.ocr_retries,
        )
        for key in settings.ocr_space_keys()
    ]
    primary_ocr = (
        PooledOCR(
            ocr_clients,
            requests_per_minute=settings.ocr_requests_per_minute,
            global_requests_per_minute=settings.ocr_global_requests_per_minute,
            cooldown_seconds=settings.ocr_cooldown_seconds,
        )
        if ocr_clients
        else None
    )
    local_ocr = TesseractOCR() if settings.local_ocr_enabled else None
    if local_ocr is not None and not local_ocr.available:
        local_ocr = None
    ocr = (
        FallbackOCR(primary_ocr, local_ocr)
        if primary_ocr is not None and local_ocr is not None
        else primary_ocr or local_ocr
    )
    vlm = None
    vision_routes = configured_model_routes(settings, vision_only=True)
    if settings.vlm_enabled and vision_routes:
        vision_keys = [(route, route_api_keys(settings, route)) for route in vision_routes]
        adapters = []
        for route, keys in vision_keys:
            for key in keys:
                adapters.append(
                    OpenAICompatibleVLM(
                        model=route.model,
                        api_key=key,
                        base_url=route.base_url,
                        label=f"{route.label} · {route.provider}",
                        timeout_seconds=settings.vlm_timeout_seconds,
                    )
                )
        vlm = PooledVLM(
            adapters,
            requests_per_minute=settings.vlm_requests_per_minute,
            global_requests_per_minute=settings.vlm_global_requests_per_minute,
            cooldown_seconds=settings.vlm_cooldown_seconds,
        )
    return WorkspaceKnowledgeBase(
        store=store or create_workspace_store(settings, project_root),
        vector_path=settings.resolve_path(settings.chroma_path, project_root),
        embedder=create_embedder(settings),
        pipeline=DocumentIngestionPipeline(
            ocr=ocr,
            vlm=vlm,
            transcriber=create_transcriber(settings),
        ),
    )


def create_transcriber(settings: Settings):
    local = WhisperTranscriber(
        settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    speech_keys = settings.groq_speech_keys()
    if settings.speech_primary_provider == "groq" and speech_keys:
        primary = PooledTranscriber(
            [
                GroqTranscriber(
                    key,
                    model_name=settings.groq_whisper_model,
                )
                for key in speech_keys
            ],
            requests_per_minute=settings.speech_requests_per_minute,
            global_requests_per_minute=settings.speech_global_requests_per_minute,
            cooldown_seconds=settings.speech_cooldown_seconds,
        )
        return FallbackTranscriber(
            primary,
            local,
        )
    return local


def create_workspace_assistant(
    settings: Settings,
    project_root: Path,
    workspace_id: str,
    document_ids: set[str] | None = None,
) -> ResearchAssistant:
    store = create_workspace_store(settings, project_root)
    knowledge_base = create_knowledge_base(settings, project_root, store=store)
    corpus = WorkspaceCorpus(knowledge_base, workspace_id, document_ids)
    language_model = create_language_model(settings)
    tools = ResearchTools(
        corpus=corpus,
        language_model=language_model,
        verified_results_path=project_root / "fixtures" / "verified_results.json",
        outputs_path=settings.resolve_path(settings.outputs_path, project_root),
        document_context=knowledge_base,
        workspace_id=workspace_id,
        selected_document_ids=document_ids,
    )
    return ResearchAssistant(
        corpus=corpus,
        language_model=language_model,
        tools=tools,
        minimum_similarity=settings.retrieval_min_similarity,
        top_k=settings.retrieval_top_k,
        document_ids=(
            sorted(document_ids)
            if document_ids
            else [item.document_id for item in store.get(workspace_id).documents]
        ),
        demo_mode=False,
    )


def create_language_model(settings: Settings) -> LanguageModel:
    routed = _create_routed_language_model(settings)
    if routed is not None:
        return routed

    api_key = settings.llm_api_key
    if settings.llm_provider == "ollama" and not api_key:
        api_key = "ollama"
    if "generativelanguage.googleapis.com" in settings.llm_base_url.casefold():
        keys = settings.google_llm_keys()
        models = [
            OpenAIChatCompletionsModel(
                model=settings.llm_model,
                api_key=key,
                base_url=settings.llm_base_url,
                max_retries=0,
                timeout_seconds=settings.llm_timeout_seconds,
            )
            for key in keys
        ]
        if not models:
            raise ValueError("LLM_API_KEY or GEMINI_API_KEYS is not configured")
        fallback: LanguageModel | None = None
        if settings.llm_fallback_model.strip():
            fallback_models = [
                OpenAIChatCompletionsModel(
                    model=settings.llm_fallback_model,
                    api_key=key,
                    base_url=settings.llm_base_url,
                    max_retries=0,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
                for key in keys
            ]
            fallback = FailoverLanguageModel(
                fallback_models,
                provider="Gemini fallback model API",
            )
        if len(models) == 1 and fallback is None:
            return models[0]
        return FailoverLanguageModel(
            models,
            provider="Gemini LLM API",
            fallback=fallback,
            fallback_label=settings.llm_fallback_model or None,
        )
    return OpenAIResponsesModel(
        model=settings.llm_model,
        api_key=api_key,
        base_url=settings.llm_base_url,
        compatibility_mode=settings.llm_provider,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _create_routed_language_model(settings: Settings) -> LanguageModel | None:
    if not settings.llm_routing_enabled:
        return None

    tiers: list[tuple[str, LanguageModel]] = []

    def add_tier(
        label: str,
        model: str,
        keys: list[str],
        base_url: str,
        provider: str,
    ) -> None:
        if not keys:
            return
        pool = [
            OpenAIChatCompletionsModel(
                model=model,
                api_key=key,
                base_url=base_url,
                max_output_tokens=10_000,
                max_retries=0,
                timeout_seconds=settings.llm_timeout_seconds,
            )
            for key in keys
        ]
        tiers.append(
            (
                label,
                FailoverLanguageModel(
                    pool,
                    provider=provider,
                    cooldown_seconds=settings.llm_pool_cooldown_seconds,
                    retry_rounds=settings.llm_pool_retry_rounds,
                ),
            )
        )

    if (
        settings.llm_provider == "openai"
        and settings.llm_api_keys()
        and "generativelanguage.googleapis.com" not in settings.llm_base_url.casefold()
    ):
        add_tier(
            f"{settings.llm_model} · primary",
            settings.llm_model,
            settings.llm_api_keys(),
            settings.llm_base_url,
            "Primary OpenAI-compatible API",
        )

    for route in configured_model_routes(settings):
        add_tier(
            route.label,
            route.model,
            route_api_keys(settings, route),
            route.base_url,
            route.provider,
        )
    return (
        ModelFallbackChain(
            tiers,
            pool_cooldown_seconds=settings.llm_pool_cooldown_seconds,
            retry_rounds=settings.llm_pool_retry_rounds,
        )
        if tiers
        else None
    )


def create_assistant(settings: Settings, project_root: Path) -> ResearchAssistant:
    """Compatibility alias for the default workspace assistant."""
    return create_workspace_assistant(settings, project_root, "default")
