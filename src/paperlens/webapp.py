from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import threading
import time
import traceback
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import chromadb
from fastapi import File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi import status as http_status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from paperlens.config import Settings
from paperlens.conversations import ConversationMemory
from paperlens.domain import Evidence
from paperlens.fonts import FontRegistry
from paperlens.ingestion import DocumentIngestionResult
from paperlens.model_routes import (
    MODEL_ROUTES,
    all_model_routes,
    configured_model_routes,
    credential_env_keys,
)
from paperlens.multimodal import PromptImageAnalyzer, prompt_image_context
from paperlens.pdf_backend import render_pdf_page
from paperlens.podcast import (
    GeminiLivePodcastRenderer,
    GroqOrpheusPodcastRenderer,
    PiperPodcastRenderer,
    PodcastScriptTurn,
    PodcastService,
    ResilientPodcastRenderer,
)
from paperlens.provider_config import (
    ProviderCatalogConfig,
    ProviderEndpointConfig,
    ProviderModelConfig,
    load_provider_catalog,
    save_provider_catalog,
)
from paperlens.rendering import render_markdown, render_markdown_tree
from paperlens.runtime import (
    create_assistant,
    create_knowledge_base,
    create_language_model,
    create_workspace_assistant,
    create_workspace_store,
    seed_demo_workspace,
)
from paperlens.studio import (
    StudioArtifact,
    StudioArtifactStore,
    StudioArtifactType,
    StudioGenerationRequest,
    StudioLanguage,
    StudioService,
    parse_legacy_flashcards,
    studio_creation_catalog,
)
from paperlens.tools.registry import ResearchTools, ToolArgumentError
from paperlens.visual_artifacts import create_visual_artifact_service
from paperlens.web.application import ApplicationContainer, create_application
from paperlens.web.routers import install_domain_routers
from paperlens.web_sources import (
    WebSearchOptions,
    WebSourceService,
    create_web_source_service,
)
from paperlens.workspaces import ProcessingEvent, ProcessingProgress, WorkspaceRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = Path(__file__).with_name("web")
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"
PUBLIC_WEB_FONTS = {
    name: FontRegistry.default_bundled_root() / "Vazirmatn" / name
    for name in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf")
}
_assistant_lock = threading.Lock()
_voice_lock = threading.Lock()
_trace_lock = threading.Lock()
_rebuild_lock = threading.Lock()
_traces: deque[dict[str, Any]] = deque(maxlen=80)
_workspace_rebuild_states: dict[str, dict[str, Any]] = {}
_processing_lock = threading.Lock()
_processing_jobs: set[tuple[str, str]] = set()

_PROVIDER_CREDENTIALS: dict[str, dict[str, Any]] = {
    "google": {
        "label": "Google Gemini",
        "environment_variable": "GEMINI_API_KEYS",
        "capabilities": ["chat", "vision", "embeddings", "audio"],
    },
    "groq": {
        "label": "Groq",
        "environment_variable": "GROQ_CHAT_API_KEYS",
        "capabilities": ["chat", "vision", "transcription"],
    },
    "mistral": {
        "label": "Mistral AI",
        "environment_variable": "MISTRAL_API_KEYS",
        "capabilities": ["chat", "vision"],
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "environment_variable": "NVIDIA_API_KEYS",
        "capabilities": ["chat", "vision"],
    },
    "ocr_space": {
        "label": "OCR.Space",
        "environment_variable": "OCR_SPACE_API_KEYS",
        "capabilities": ["ocr"],
    },
}

container = ApplicationContainer(
    project_root=PROJECT_ROOT,
    web_root=WEB_ROOT,
    outputs_root=OUTPUTS_ROOT,
    startup=lambda: startup_application(),
)
app = create_application(container)


def startup_application() -> None:
    """Run explicit, idempotent migrations before recovering background work."""
    for workspace in workspace_store().list():
        if not workspace.vector_collection_id or not any(
            document.status == "ready"
            and (document.chunk_count == 0 or not document.language_hints)
            for document in workspace.documents
        ):
            continue
        with suppress(
            chromadb.errors.ChromaError, KeyError, OSError, RuntimeError, ValueError
        ):
            knowledge_base().reconcile_document_metadata(workspace.workspace_id)
    recover_studio_jobs()


def recover_studio_jobs() -> None:
    store = studio_artifact_store()
    recovery = store.reconcile_startup()
    for artifact_id in recovery["resume"]:
        artifact = next(
            (
                item
                for workspace in workspace_store().list()
                for item in store.list(workspace.workspace_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None or artifact.artifact_type != "audio_overview":
            continue
        keys = settings().google_llm_keys()
        required = len(artifact.options.get("participants") or [])
        if len(keys) < required:
            continue
        trace = _new_trace("studio", "Resume audio overview", workspace_id=artifact.workspace_id)
        podcast_executor().submit(
            _generate_podcast_audio_job,
            artifact.workspace_id,
            artifact.artifact_id,
            trace,
        )


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=2, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    workspace_id: str = Field(default="default", min_length=1, max_length=81)
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    conversation_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{7,100}$",
    )


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str = Field(default="default", min_length=1, max_length=81)


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)


class TextDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(default="Pasted text", min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=2_000_000)


class WebSourceSearchRequest(WebSearchOptions):
    pass


class WebSourceImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(min_length=1, max_length=100)
    process: bool = True


class IntegrationCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    exa_api_keys: str | None = Field(default=None, max_length=20_000, repr=False)
    tavily_api_keys: str | None = Field(default=None, max_length=20_000, repr=False)
    pexels_api_keys: str | None = Field(default=None, max_length=20_000, repr=False)
    jamendo_client_ids: str | None = Field(default=None, max_length=20_000, repr=False)
    research_api_timeout_seconds: float | None = Field(default=None, ge=5.0, le=120.0)
    research_api_cooldown_seconds: float | None = Field(default=None, ge=1.0, le=600.0)
    research_api_minimum_gap_seconds: float | None = Field(default=None, ge=0.0, le=60.0)


class StudioCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    artifact_type: StudioArtifactType
    document_ids: list[str] = Field(min_length=1, max_length=20)
    language: StudioLanguage = "auto"
    title: str | None = Field(default=None, max_length=180)
    options: dict[str, Any] = Field(default_factory=dict)


class PodcastScriptUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turns: list[PodcastScriptTurn] = Field(min_length=1, max_length=240)


class ProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    api_keys: str = Field(default="", max_length=20_000, repr=False)


class ProviderModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=180)
    label: str = Field(min_length=1, max_length=180)
    capabilities: set[Literal["chat", "vision", "tools"]] = Field(
        default_factory=lambda: {"chat", "tools"}
    )
    chat_priority: int = Field(default=1000, ge=1, le=10_000)
    vision_priority: int = Field(default=1000, ge=1, le=10_000)
    enabled: bool = True


class ProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    adapter: Literal["openai"] = "openai"
    base_url: str = Field(min_length=8, max_length=500)
    environment_variable: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,80}$")
    api_keys: str = Field(default="", max_length=20_000, repr=False)
    capabilities: set[str] = Field(default_factory=set)
    enabled: bool = True
    models: list[ProviderModelRequest] = Field(min_length=1, max_length=100)


class ProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str | None = Field(default=None, min_length=1, max_length=120)
    adapter: Literal["openai"] | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    environment_variable: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,80}$")
    api_keys: str | None = Field(default=None, max_length=20_000, repr=False)
    enabled: bool | None = None
    models: list[ProviderModelRequest] | None = Field(default=None, min_length=1, max_length=100)


class ProviderRuntimeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_timeout_seconds: float | None = Field(default=None, ge=5, le=120)
    llm_pool_retry_rounds: int | None = Field(default=None, ge=1, le=3)
    llm_pool_cooldown_seconds: float | None = Field(default=None, ge=1, le=600)
    ingestion_worker_count: int | None = Field(default=None, ge=1, le=8)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=30)
    retrieval_min_similarity: float | None = Field(default=None, ge=0, le=1)
    ocr_requests_per_minute: float | None = Field(default=None, ge=0.1, le=120)
    ocr_global_requests_per_minute: float | None = Field(default=None, ge=0.1, le=120)
    ocr_cooldown_seconds: float | None = Field(default=None, ge=1, le=600)
    vlm_enabled: bool | None = None
    vlm_requests_per_minute: float | None = Field(default=None, ge=0.1, le=120)
    vlm_global_requests_per_minute: float | None = Field(default=None, ge=0.1, le=120)
    vlm_cooldown_seconds: float | None = Field(default=None, ge=1, le=600)
    podcast_turn_timeout_seconds: float | None = Field(default=None, ge=30, le=300)
    podcast_minimum_key_gap_seconds: float | None = Field(default=None, ge=0, le=60)
    podcast_idle_refresh_seconds: float | None = Field(default=None, ge=30, le=300)


class ProviderRegistryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_order: list[str] | None = Field(default=None, max_length=100)
    model_order: list[str] | dict[str, list[str]] | None = None
    disabled_providers: list[str] | None = Field(default=None, max_length=100)
    disabled_models: list[str] | None = Field(default=None, max_length=500)
    runtime: ProviderRuntimeRequest | None = None


def _credential_values(value: str) -> list[str]:
    keys: list[str] = []
    for item in re.split(r"[,\r\n]+", value):
        key = item.strip()
        if not key or key in keys:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._:/+=-]{8,512}", key):
            raise HTTPException(status_code=422, detail="An API key has an invalid format")
        keys.append(key)
    return keys


def _write_env_setting(name: str, value: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    setting = f"{name}={value}"
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    updated = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = setting
            updated = True
            break
    if not updated:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(setting)
    temporary = env_path.with_name(f".{env_path.name}.tmp")
    temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    temporary.replace(env_path)


def _reset_provider_runtimes() -> None:
    for cached in (
        settings,
        assistant,
        knowledge_base,
        prompt_image_analyzer,
        font_registry,
        studio_service,
        podcast_service,
        web_source_service,
        visual_artifact_service,
        workspace_assistant,
    ):
        cache_clear = getattr(cached, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


def _provider_credential_count(current: Settings, provider_id: str) -> int:
    accessors = {
        "google": current.google_llm_keys,
        "groq": current.groq_chat_keys,
        "mistral": current.mistral_keys,
        "nvidia": current.nvidia_keys,
        "ocr_space": current.ocr_space_keys,
    }
    return len(accessors[provider_id]())


def _provider_config_path(current: Settings) -> Path:
    path = current.provider_config_path or Path("providers.json")
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _provider_catalog(current: Settings) -> ProviderCatalogConfig:
    return load_provider_catalog(_provider_config_path(current))


def _provider_model_payload(
    *,
    model_id: str,
    label: str,
    capabilities: set[str],
    enabled: bool,
    chat_priority: int,
    vision_priority: int,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "label": label,
        "capabilities": sorted(capabilities),
        "enabled": enabled,
        "chat_priority": chat_priority,
        "vision_priority": vision_priority,
    }


def _provider_settings_payload(current: Settings) -> dict[str, Any]:
    disabled_providers = current.disabled_model_providers()
    disabled_models = current.disabled_models()
    effective_routes = all_model_routes(current)
    configured_models = current.ordered_llm_models()
    model_rank = (
        {model: index for index, model in enumerate(configured_models)}
        if configured_models
        else {route.model: index for index, route in enumerate(effective_routes)}
    )

    def model_priority(provider_id: str, model_id: str, fallback: int) -> int:
        return model_rank.get(
            f"{provider_id}:{model_id}",
            model_rank.get(model_id, 100_000 + fallback),
        )

    def model_enabled(provider_id: str, model_id: str, default: bool = True) -> bool:
        return default and (
            f"{provider_id}:{model_id}" not in disabled_models and model_id not in disabled_models
        )

    providers: list[dict[str, Any]] = []
    for provider_id, metadata in _PROVIDER_CREDENTIALS.items():
        credential_count = _provider_credential_count(current, provider_id)
        routes = sorted(
            (route for route in MODEL_ROUTES if route.credential_pool == provider_id),
            key=lambda route: model_priority(provider_id, route.model, route.chat_priority),
        )
        models = [
            _provider_model_payload(
                model_id=route.model,
                label=route.label,
                capabilities={"chat", "tools"} | ({"vision"} if route.vision else set()),
                enabled=model_enabled(provider_id, route.model),
                chat_priority=index + 1,
                vision_priority=index + 1,
            )
            for index, route in enumerate(routes)
        ]
        if provider_id == "ocr_space":
            models = [
                _provider_model_payload(
                    model_id="ocr-space-engine-3",
                    label="OCR.Space Engine 3",
                    capabilities={"ocr"},
                    enabled=True,
                    chat_priority=1000,
                    vision_priority=1000,
                )
            ]
        elif provider_id == "google":
            live_models = (
                ("gemini-3.1-flash-live-preview", "Gemini 3.1 Flash Live Preview"),
                (
                    "gemini-2.5-flash-native-audio-preview-12-2025",
                    "Gemini 2.5 Flash Native Audio",
                ),
            )
            models.extend(
                _provider_model_payload(
                    model_id=model_id,
                    label=label,
                    capabilities={"audio"},
                    enabled=True,
                    chat_priority=1000 + index,
                    vision_priority=1000 + index,
                )
                for index, (model_id, label) in enumerate(live_models)
            )
        first_route = routes[0] if routes else None
        providers.append(
            {
                "id": provider_id,
                "label": metadata["label"],
                "adapter": "ocr_space" if provider_id == "ocr_space" else "openai",
                "base_url": first_route.base_url if first_route else "https://api.ocr.space",
                "environment_variable": metadata["environment_variable"],
                "capabilities": sorted(
                    set(metadata["capabilities"])
                    | ({"audio"} if provider_id == "google" else set())
                ),
                "enabled": provider_id not in disabled_providers,
                "built_in": True,
                "configured": credential_count > 0,
                "credential_count": credential_count,
                "models": models,
            }
        )
    for provider in _provider_catalog(current).providers:
        keys = credential_env_keys(
            provider.api_key_env,
            env_path=PROJECT_ROOT / ".env",
        )
        ordered_models = sorted(
            provider.models,
            key=lambda model: model_priority(provider.id, model.id, model.chat_priority),
        )
        providers.append(
            {
                "id": provider.id,
                "label": provider.label,
                "adapter": provider.adapter,
                "base_url": provider.base_url,
                "environment_variable": provider.api_key_env,
                "capabilities": sorted(
                    {capability for model in provider.models for capability in model.capabilities}
                ),
                "enabled": provider.enabled and provider.id not in disabled_providers,
                "built_in": False,
                "configured": bool(keys),
                "credential_count": len(keys),
                "models": [
                    _provider_model_payload(
                        model_id=model.id,
                        label=model.label,
                        capabilities=set(model.capabilities),
                        enabled=model_enabled(provider.id, model.id, model.enabled),
                        chat_priority=model.chat_priority,
                        vision_priority=model.vision_priority,
                    )
                    for model in ordered_models
                ],
            }
        )
    configured_order = current.ordered_llm_providers()
    provider_rank = {provider_id: index for index, provider_id in enumerate(configured_order)}
    providers.sort(key=lambda provider: provider_rank.get(provider["id"], 100_000))
    model_routes = [
        {
            "route_id": f"{provider['id']}:{model['id']}",
            "provider_id": provider["id"],
            "provider_label": provider["label"],
            "model_id": model["id"],
            "model_label": model["label"],
            "capabilities": model["capabilities"],
            "enabled": provider["enabled"] and model["enabled"],
            "configured": provider["configured"],
            "credential_count": provider["credential_count"],
        }
        for provider in providers
        for model in provider["models"]
        if "chat" in model["capabilities"]
    ]
    model_routes.sort(
        key=lambda route: (
            model_priority(route["provider_id"], route["model_id"], 1000),
            provider_rank.get(route["provider_id"], 100_000),
        )
    )
    return {
        "providers": providers,
        "provider_order": [provider["id"] for provider in providers],
        "model_order": [route["route_id"] for route in model_routes],
        "model_routes": model_routes,
        "runtime": {
            "llm_timeout_seconds": current.llm_timeout_seconds,
            "llm_pool_retry_rounds": current.llm_pool_retry_rounds,
            "llm_pool_cooldown_seconds": current.llm_pool_cooldown_seconds,
            "ingestion_worker_count": current.ingestion_worker_count,
            "retrieval_top_k": current.retrieval_top_k,
            "retrieval_min_similarity": current.retrieval_min_similarity,
            "ocr_requests_per_minute": current.ocr_requests_per_minute,
            "ocr_global_requests_per_minute": current.ocr_global_requests_per_minute,
            "ocr_cooldown_seconds": current.ocr_cooldown_seconds,
            "vlm_enabled": current.vlm_enabled,
            "vlm_requests_per_minute": current.vlm_requests_per_minute,
            "vlm_global_requests_per_minute": current.vlm_global_requests_per_minute,
            "vlm_cooldown_seconds": current.vlm_cooldown_seconds,
            "podcast_turn_timeout_seconds": current.podcast_turn_timeout_seconds,
            "podcast_minimum_key_gap_seconds": current.podcast_minimum_key_gap_seconds,
            "podcast_idle_refresh_seconds": current.podcast_idle_refresh_seconds,
        },
        "security": {
            "keys_returned": False,
            "storage": ".env + providers.json",
            "message": (
                "Secrets stay in .env; only secret-free routing metadata is stored "
                "in providers.json."
            ),
        },
    }


def _save_provider_keys(environment_variable: str, raw_keys: str) -> list[str]:
    keys = _credential_values(raw_keys)
    value = ",".join(keys)
    _write_env_setting(environment_variable, value)
    return keys


def _save_provider_catalog(current: Settings, catalog: ProviderCatalogConfig) -> None:
    path = _provider_config_path(current)
    save_provider_catalog(path, catalog)
    relative = path.name if path.parent == PROJECT_ROOT.resolve() else str(path)
    _write_env_setting("PROVIDER_CONFIG_PATH", relative)


def _provider_by_id(payload: dict[str, Any], provider_id: str) -> dict[str, Any]:
    for provider in payload["providers"]:
        if provider["id"] == provider_id:
            return provider
    raise HTTPException(status_code=404, detail="Provider not found")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(_env_file=PROJECT_ROOT / ".env")


@lru_cache(maxsize=1)
def assistant():
    return create_assistant(settings(), PROJECT_ROOT)


@lru_cache(maxsize=1)
def workspace_store():
    return create_workspace_store(settings(), PROJECT_ROOT)


@lru_cache(maxsize=1)
def conversation_memory() -> ConversationMemory:
    current = settings()
    return ConversationMemory(
        workspace_store().root,
        lambda: assistant().language_model,
        context_budget_tokens=current.conversation_context_budget_tokens,
        recent_turns=current.conversation_recent_turns,
        evidence_memory_chars=current.conversation_evidence_memory_chars,
    )


@lru_cache(maxsize=1)
def knowledge_base():
    return create_knowledge_base(
        settings(),
        PROJECT_ROOT,
        store=workspace_store(),
    )


@lru_cache(maxsize=1)
def processing_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(
        max_workers=settings().ingestion_worker_count,
        thread_name_prefix="paperlens-ingest",
    )


@lru_cache(maxsize=1)
def studio_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="paperlens-studio")


@lru_cache(maxsize=1)
def podcast_executor() -> ThreadPoolExecutor:
    # A single queue prevents two episodes from leasing the same local credential pool.
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="paperlens-podcast")


@lru_cache(maxsize=1)
def prompt_image_analyzer() -> PromptImageAnalyzer:
    return PromptImageAnalyzer(knowledge_base().pipeline)


@lru_cache(maxsize=1)
def studio_artifact_store() -> StudioArtifactStore:
    store = workspace_store()
    return StudioArtifactStore(store.root)


@lru_cache(maxsize=1)
def font_registry() -> FontRegistry:
    return FontRegistry(
        PROJECT_ROOT / "data" / "fonts",
        bundled_root=FontRegistry.default_bundled_root(),
    )


@lru_cache(maxsize=1)
def studio_service() -> StudioService:
    store = workspace_store()
    return StudioService(
        workspaces=store,
        knowledge_base=knowledge_base(),
        language_model=create_language_model(settings()),
        artifacts=studio_artifact_store(),
    )


@lru_cache(maxsize=1)
def podcast_service() -> PodcastService:
    current = settings()
    renderers = []
    if current.google_llm_keys():
        renderers.append(
            GeminiLivePodcastRenderer(
                turn_timeout_seconds=current.podcast_turn_timeout_seconds,
                minimum_key_gap_seconds=current.podcast_minimum_key_gap_seconds,
                idle_refresh_seconds=current.podcast_idle_refresh_seconds,
            )
        )
    if current.groq_speech_keys():
        renderers.append(GroqOrpheusPodcastRenderer(current.groq_speech_keys()))
    piper = PiperPodcastRenderer(
        current.piper_command_path(),
        en_voice_models=current.piper_voice_models("en"),
        fa_voice_models=current.piper_voice_models("fa"),
        timeout_seconds=current.piper_timeout_seconds,
    )
    if piper.configured:
        renderers.append(piper)
    return PodcastService(
        workspaces=workspace_store(),
        knowledge_base=knowledge_base(),
        language_model=create_language_model(current),
        artifacts=studio_artifact_store(),
        audio_renderer=(
            ResilientPodcastRenderer(renderers[0], *renderers[1:]) if renderers else None
        ),
    )


@lru_cache(maxsize=1)
def web_source_service() -> WebSourceService:
    current = settings()
    return create_web_source_service(settings=current, workspaces=workspace_store())


@lru_cache(maxsize=1)
def visual_artifact_service():
    return create_visual_artifact_service(
        settings=settings(),
        artifacts=studio_artifact_store(),
        fonts=font_registry(),
    )


@lru_cache(maxsize=24)
def workspace_assistant(workspace_id: str, document_ids: tuple[str, ...]):
    return create_workspace_assistant(
        settings(),
        PROJECT_ROOT,
        workspace_id,
        set(document_ids) or None,
    )


@lru_cache(maxsize=1)
def transcriber():
    from paperlens.runtime import create_transcriber

    return create_transcriber(settings())


def _collection_name(current: Settings) -> str:
    if current.embedding_provider == "gemini":
        return f"paper_chunks_gemini_{current.embedding_dimensions}"
    return "paper_chunks"


def _collection(current: Settings | None = None):
    current = current or settings()
    client = chromadb.PersistentClient(
        path=str(current.resolve_path(current.chroma_path, PROJECT_ROOT))
    )
    return client.get_collection(_collection_name(current))


def _runtime_failover_status() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        current_assistant = assistant()
    except Exception as error:  # noqa: BLE001 - the health endpoint must stay observable
        current = settings()
        return (
            {
                "available": False,
                "provider": current.llm_provider,
                "active_model": current.llm_model or "not configured",
                "credential_count": 0,
                "active_slot": None,
                "last_failover": False,
                "routes": [],
                "initialization_error": type(error).__name__,
            },
            {
                "available": False,
                "credential_count": len(current.embedding_api_keys()),
                "active_slot": None,
                "last_backend": "unavailable",
                "initialization_error": type(error).__name__,
            },
        )
    language_model = current_assistant.language_model
    corpus = current_assistant.corpus
    index = getattr(corpus, "index", None)
    if index is None:
        index = getattr(getattr(corpus, "knowledge_base", None), "embedder", None)
    llm_status = (
        language_model.failover_status()
        if hasattr(language_model, "failover_status")
        else {"credential_count": 1, "active_slot": 1, "last_failover": False}
    )
    embedding_status = (
        index.failover_status()
        if index is not None and hasattr(index, "failover_status")
        else {"credential_count": 1, "active_slot": 1, "last_backend": "primary"}
    )
    return llm_status, embedding_status


def _artifact_url(path: str | Path | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).resolve()
    try:
        relative = candidate.relative_to(OUTPUTS_ROOT.resolve())
    except ValueError:
        return None
    return "/artifacts/" + relative.as_posix()


def _artifact(
    path: Path,
    *,
    label: str,
    kind: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return {
        "label": label,
        "name": path.name,
        "kind": kind,
        "url": _artifact_url(path),
        "size_bytes": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=UTC,
        ).isoformat(),
    }


def _answer_artifacts(answer) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any] | None] = []
    fields = {
        "chart_path": ("نمودار", "image"),
        "csv_path": ("دادهٔ CSV", "csv"),
        "markdown_path": ("گزارش Markdown", "markdown"),
        "json_path": ("دادهٔ JSON", "json"),
        "path": ("گزارش پژوهشی", "markdown"),
    }
    for call in answer.tool_calls:
        if not call.output:
            continue
        try:
            payload = json.loads(call.output)
        except (TypeError, json.JSONDecodeError):
            continue
        for field, (label, kind) in fields.items():
            value = payload.get(field)
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            artifacts.append(_artifact(path, label=label, kind=kind))
    return [item for item in artifacts if item is not None]


def _display_text(text: str) -> str:
    """The web UI renders artifacts as cards instead of showing filesystem paths."""
    return re.split(
        r"\n###\s+(?:فایل‌های خروجی|Generated artifacts)\s*\n",
        text,
        maxsplit=1,
    )[0].rstrip()


def _redacted_diagnostic(message: str, current: Settings) -> str:
    """Redact configured credentials while preserving complete diagnostics."""
    secrets = {
        current.llm_api_key,
        current.embedding_api_key,
        current.groq_api_key,
        *current.llm_api_keys(),
        *current.google_llm_keys(),
        *current.groq_chat_keys(),
        *current.mistral_keys(),
        *current.nvidia_keys(),
        *current.embedding_api_keys(),
        *current.ocr_space_keys(),
        *current.groq_speech_keys(),
    }
    for secret in sorted(
        (value for value in secrets if len(value.strip()) >= 8),
        key=len,
        reverse=True,
    ):
        message = message.replace(secret, "[REDACTED]")
    return message


def _redacted_error(error: Exception, current: Settings) -> str:
    """Keep the complete operational error without exposing configured credentials."""
    return _redacted_diagnostic(f"{type(error).__name__}: {error}", current)


def _error_detail(error: Exception, current: Settings) -> dict[str, Any]:
    causes: list[dict[str, str]] = []
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        causes.append(
            {
                "type": type(cause).__name__,
                "message": _redacted_diagnostic(str(cause), current),
            }
        )
        cause = cause.__cause__ or cause.__context__
    return {
        "type": type(error).__name__,
        "message": _redacted_diagnostic(str(error), current),
        "traceback": _redacted_diagnostic(
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            current,
        ),
        "causes": causes,
    }


def _trace_directory(workspace_id: str) -> Path:
    safe_workspace_id = (
        workspace_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", workspace_id) else "system"
    )
    directory = (workspace_store().root / ".traces" / safe_workspace_id).resolve()
    root = workspace_store().root.resolve()
    if root not in directory.parents:
        raise ValueError("Trace path escapes workspace storage")
    return directory


def _persist_trace(trace: dict[str, Any]) -> None:
    directory = _trace_directory(str(trace.get("workspace_id") or "system"))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{trace['id']}.json"
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(target)


def _stored_traces(workspace_id: str | None = None) -> list[dict[str, Any]]:
    root = workspace_store().root / ".traces"
    paths = (
        list(_trace_directory(workspace_id).glob("*.json"))
        if workspace_id
        else list(root.glob("*/*.json"))
    )
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return records


def _new_trace(
    kind: str,
    summary: str,
    *,
    workspace_id: str = "demo",
) -> dict[str, Any]:
    trace = {
        "id": uuid4().hex[:12],
        "kind": kind,
        "summary": summary[:180],
        "workspace_id": workspace_id,
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "duration_ms": None,
        "mode": None,
        "evidence_count": 0,
        "tools": [],
        "provider": None,
        "model": None,
        "token_usage": None,
        "cost_estimate": None,
        "selected_evidence": [],
        "stages": [
            {
                "name": "request.accepted",
                "label": "دریافت درخواست در FastAPI",
                "service": "SourceLens API",
                "status": "completed",
            }
        ],
    }
    with _trace_lock:
        _traces.appendleft(trace)
    _persist_trace(trace)
    return trace


def _finish_trace(
    trace: dict[str, Any],
    *,
    started: float,
    answer=None,
    error: Exception | None = None,
    transcription=None,
) -> None:
    current = settings()
    if answer is not None:
        llm_status, embedding_status = _runtime_failover_status()
        llm_generated_report = any(
            json.loads(call.output or "{}").get("generated_by_llm") is True
            for call in answer.tool_calls
        )
        cached_report = any(
            json.loads(call.output or "{}").get("cache_hit") is True for call in answer.tool_calls
        )
        trace.update(
            {
                "status": "completed",
                "mode": answer.mode,
                "evidence_count": len(answer.evidence),
                "tools": [call.name for call in answer.tool_calls],
                "provider": llm_status.get("provider", current.llm_provider),
                "model": llm_status.get("active_model", current.llm_model),
                "selected_evidence": [
                    {
                        "chunk_id": item.chunk_id,
                        "document_id": item.document_id or item.paper_id,
                        "page_start": item.page_start,
                        "page_end": item.page_end,
                        "similarity": item.similarity,
                    }
                    for item in answer.evidence
                ],
            }
        )
        trace["stages"].extend(
            [
                {
                    "name": "embedding.query",
                    "label": "تبدیل پرسش به بردار ۷۶۸بعدی",
                    "service": (
                        (
                            "Google Gemini Embedding API · "
                            f"slot {embedding_status.get('active_slot')}/"
                            f"{embedding_status.get('credential_count')}"
                        )
                        if current.embedding_provider == "gemini"
                        else "Local SentenceTransformer"
                    ),
                    "status": "completed",
                    "backend": embedding_status.get("last_backend", "primary"),
                },
                {
                    "name": "vector.retrieve",
                    "label": f"بازیابی {len(answer.evidence)} شاهد با cosine similarity",
                    "service": f"ChromaDB / {_collection_name(current)}",
                    "status": "completed",
                },
                {
                    "name": "evidence.gate",
                    "label": "ارزیابی کفایت شواهد و مسیر پاسخ",
                    "service": "SourceLens retrieval gate",
                    "status": "completed",
                    "result": answer.mode,
                },
            ]
        )
        if answer.execution_path in {"agent_tool", "hybrid"}:
            trace["stages"].append(
                {
                    "name": "llm.tool_plan",
                    "label": "انتخاب ابزار توسط مدل زبانی",
                    "service": llm_status.get("active_model", current.llm_model),
                    "status": "completed",
                }
            )
        for call in answer.tool_calls:
            trace["stages"].append(
                {
                    "name": "function.call",
                    "label": f"اجرای تابع {call.name}()",
                    "service": "ResearchTools",
                    "status": "completed",
                }
            )
        if cached_report or llm_generated_report:
            trace["stages"].append(
                {
                    "name": "report.cache" if cached_report else "llm.report_generate",
                    "label": (
                        "بازیابی گزارش LLM از cache اعتبارسنجی‌شده"
                        if cached_report
                        else "تولید گزارش مستند توسط LLM داخل تابع پژوهشی"
                    ),
                    "service": (
                        "Content-addressed local report cache"
                        if cached_report
                        else llm_status.get("active_model", current.llm_model)
                    ),
                    "status": "completed",
                    "model_tier": llm_status.get("active_slot"),
                    "failover_used": llm_status.get("last_failover", False),
                }
            )
        if answer.execution_path == "hybrid" or (
            answer.execution_path == "agent_tool" and not llm_generated_report
        ):
            trace["stages"].append(
                {
                    "name": "llm.synthesize",
                    "label": (
                        "ترکیب شواهد RAG و خروجی ابزار در پاسخ نهایی"
                        if answer.execution_path == "hybrid"
                        else "تحلیل خروجی ابزار و ساخت پاسخ نهایی"
                    ),
                    "service": llm_status.get("active_model", current.llm_model),
                    "status": "completed",
                    "model_tier": llm_status.get("active_slot"),
                    "failover_used": llm_status.get("last_failover", False),
                }
            )
        elif answer.execution_path not in {"agent_tool", "hybrid"}:
            trace["stages"].append(
                {
                    "name": "llm.generate",
                    "label": (
                        "تولید پاسخ عمومی و کنترل زبان"
                        if answer.execution_path == "general_fallback"
                        else "پاسخ مستقیم مدل از شواهد و کنترل citation"
                    ),
                    "service": llm_status.get("active_model", current.llm_model),
                    "status": "completed",
                    "model_tier": llm_status.get("active_slot"),
                    "failover_used": llm_status.get("last_failover", False),
                }
            )
        trace["stages"].append(
            {
                "name": "response.render",
                "label": "تبدیل Markdown و ارسال پاسخ",
                "service": "SourceLens Web",
                "status": "completed",
            }
        )
    elif transcription is not None:
        trace.update({"status": "completed", "mode": "transcription"})
        trace["stages"].extend(
            [
                {
                    "name": "speech.transcribe",
                    "label": "تبدیل صوت به متن",
                    "service": f"{transcription.provider} / {transcription.model}",
                    "status": "completed",
                    "fallback_used": transcription.fallback_used,
                },
                {
                    "name": "response.render",
                    "label": "بازگرداندن متن تشخیص‌داده‌شده",
                    "service": "SourceLens Web",
                    "status": "completed",
                },
            ]
        )
    elif error is not None:
        trace["status"] = "failed"
        trace["error"] = _redacted_error(error, current)
        trace["error_detail"] = _error_detail(error, current)
        trace["stages"].append(
            {
                "name": "request.failed",
                "label": "درخواست با خطا متوقف شد",
                "service": "SourceLens API",
                "status": "failed",
            }
        )
    trace["duration_ms"] = round((time.perf_counter() - started) * 1000)
    trace["finished_at"] = datetime.now(UTC).isoformat()
    _persist_trace(trace)


def _ask_locked(
    question: str,
    history: list[ChatTurn],
    conversation_summary: str = "",
    prior_evidence: tuple[Evidence, ...] = (),
):
    # Retrieval and provider pools are thread-safe. Do not serialize every chat
    # behind a slow external request; only direct artifact writes use the tool lock.
    return assistant().ask(
        question,
        [(turn.role, turn.content) for turn in history],
        conversation_summary=conversation_summary,
        prior_evidence=prior_evidence,
    )


def _ask_workspace_locked(
    question: str,
    history: list[ChatTurn],
    workspace_id: str,
    document_ids: tuple[str, ...],
    conversation_summary: str = "",
    prior_evidence: tuple[Evidence, ...] = (),
):
    return workspace_assistant(workspace_id, document_ids).ask(
        question,
        [(turn.role, turn.content) for turn in history],
        conversation_summary=conversation_summary,
        prior_evidence=prior_evidence,
    )


def _execute_workspace_tool_locked(
    tool_name: str,
    arguments: dict[str, Any],
    workspace_id: str,
) -> str:
    with _assistant_lock:
        return workspace_assistant(workspace_id, ()).tools.execute(tool_name, arguments)


def _transcribe_locked(content: bytes, suffix: str):
    with _voice_lock:
        return transcriber().transcribe_bytes(content, suffix)


def _workspace_or_404(workspace_id: str) -> WorkspaceRecord:
    try:
        return workspace_store().get(workspace_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Workspace not found") from error


def _document_payload(workspace_id: str, document) -> dict[str, Any]:
    methods = Counter(document.extraction_methods.values())
    failed_pages = 0
    with suppress(FileNotFoundError, ValueError):
        extraction_path = workspace_store().extraction_path(workspace_id, document.document_id)
        extraction = DocumentIngestionResult.model_validate_json(
            extraction_path.read_text(encoding="utf-8")
        )
        failed_pages = sum(
            1
            for page in extraction.pages
            if page.confidence == 0 or "no usable extracted text" in " ".join(page.warnings)
        )
    return {
        **document.model_dump(mode="json"),
        "source_url": (f"/api/workspaces/{workspace_id}/documents/{document.document_id}/source"),
        "pdf_url": (
            f"/api/workspaces/{workspace_id}/documents/{document.document_id}/pdf"
            if document.source_type == "pdf"
            else None
        ),
        "extraction_report": {
            "text_pages": methods.get("text", 0),
            "ocr_pages": methods.get("ocr", 0),
            "vlm_pages": methods.get("vlm", 0),
            "hybrid_pages": methods.get("hybrid", 0),
            "failed_pages": failed_pages,
            "warnings": document.warnings,
        },
    }


def _inspect_workspace_vector(workspace_id: str) -> dict[str, Any]:
    """Inspect persisted vector metadata without loading embedding or LLM runtimes."""
    if not hasattr(knowledge_base, "cache_info"):
        # Test and embedded deployments may replace the cached runtime with an
        # already-constructed knowledge base that owns a different vector path.
        return knowledge_base().inspect(workspace_id)
    workspace = workspace_store().get(workspace_id)
    current = settings()
    base = {
        "workspace_id": workspace_id,
        "document_count": len(workspace.documents),
        "chunk_count": 0,
        "embedding_model": workspace.embedding_model,
        "dimensions": current.embedding_dimensions,
        "collection_name": workspace.vector_collection_id,
        "extraction_method_distribution": {},
        "top_sections": {},
        "top_pages": {},
        "last_rebuild_at": workspace.last_rebuild_at,
    }
    if not workspace.vector_collection_id:
        return base
    try:
        client = chromadb.PersistentClient(
            path=str(current.resolve_path(current.chroma_path, PROJECT_ROOT))
        )
        collection = client.get_collection(workspace.vector_collection_id)
        payload = collection.get(include=["metadatas"])
    except Exception:  # noqa: BLE001 - a missing stale collection is an empty inspection
        return base
    metadatas = payload.get("metadatas") or []
    methods = Counter(str(item.get("extraction_method", "text")) for item in metadatas)
    sections = Counter(str(item.get("section", "Unknown")) for item in metadatas)
    pages = Counter(
        f"{item.get('filename') or item.get('paper_title')} · p.{item.get('page_start')}"
        for item in metadatas
    )
    metadata = collection.metadata or {}
    return {
        **base,
        "document_count": len(
            {str(item.get("document_id", item.get("paper_id"))) for item in metadatas}
        ),
        "chunk_count": collection.count(),
        "embedding_model": metadata.get("embedding_model", workspace.embedding_model),
        "dimensions": int(metadata.get("embedding_dimensions") or current.embedding_dimensions),
        "extraction_method_distribution": dict(methods),
        "top_sections": dict(sections.most_common(10)),
        "top_pages": dict(pages.most_common(10)),
    }


def _process_document_job(workspace_id: str, document_id: str) -> None:
    try:
        knowledge_base().process_document(workspace_id, document_id)
    except Exception:
        # The knowledge-base module persists a redacted document-level failure.
        pass
    finally:
        with _processing_lock:
            _processing_jobs.discard((workspace_id, document_id))
        workspace_assistant.cache_clear()


def _submit_document_job(workspace_id: str, document_id: str) -> bool:
    job = (workspace_id, document_id)
    with _processing_lock:
        if job in _processing_jobs:
            return False
        _processing_jobs.add(job)
    try:
        workspace_store().update_document(
            workspace_id,
            document_id,
            status="processing",
            errors=[],
            progress=ProcessingProgress(
                stage="queued",
                started_at=datetime.now(UTC).isoformat(),
                events=[
                    ProcessingEvent(
                        sequence=1,
                        stage="queued",
                        status="queued",
                        message="سند وارد صف پردازش شد.",
                    )
                ],
            ),
        )
        processing_executor().submit(
            _process_document_job,
            workspace_id,
            document_id,
        )
    except Exception:
        with _processing_lock:
            _processing_jobs.discard(job)
        raise
    return True


def _workspace_rebuild_job(workspace_id: str, job_id: str) -> None:
    started = time.perf_counter()

    def update_progress(update: dict[str, Any]) -> None:
        with _rebuild_lock:
            current = _workspace_rebuild_states.get(workspace_id, {})
            if current.get("job_id") == job_id:
                _workspace_rebuild_states[workspace_id] = {**current, **update}

    try:
        report = knowledge_base().rebuild(
            workspace_id,
            on_progress=update_progress,
        )
        state = {
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": "completed",
            "progress": 100,
            "report": report,
            "error": None,
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as error:  # noqa: BLE001 - background state records failure
        state = {
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": "failed",
            "progress": 0,
            "report": None,
            "error": _redacted_error(error, settings()),
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    with _rebuild_lock:
        _workspace_rebuild_states[workspace_id] = state
    workspace_assistant.cache_clear()


@app.get("/api/workspaces")
def workspaces() -> list[dict[str, Any]]:
    return [
        {
            **workspace.model_dump(mode="json"),
            "document_count": len(workspace.documents),
            "ready_document_count": sum(item.status == "ready" for item in workspace.documents),
        }
        for listed in workspace_store().list()
        for workspace in [_workspace_or_404(listed.workspace_id)]
    ]


@app.post("/api/workspaces", status_code=http_status.HTTP_201_CREATED)
def create_workspace(request: WorkspaceCreateRequest) -> dict[str, Any]:
    return workspace_store().create_workspace(request.name).model_dump(mode="json")


@app.delete("/api/workspaces/{workspace_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_workspace(workspace_id: str) -> Response:
    _workspace_or_404(workspace_id)
    with _processing_lock:
        if any(job_workspace == workspace_id for job_workspace, _ in _processing_jobs):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Wait for active document processing to finish before deleting this workspace"
                ),
            )
    with _rebuild_lock:
        if _workspace_rebuild_states.get(workspace_id, {}).get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Wait for the active vector rebuild to finish before deleting this workspace"
                ),
            )
    knowledge_base().delete_workspace(workspace_id)
    with _rebuild_lock:
        _workspace_rebuild_states.pop(workspace_id, None)
    if not workspace_store().list():
        workspace_store().ensure_default_workspace()
    workspace_assistant.cache_clear()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@app.post("/api/demo", status_code=http_status.HTTP_202_ACCEPTED)
def install_demo_workspace() -> dict[str, Any]:
    store = workspace_store()
    seed_demo_workspace(settings(), PROJECT_ROOT, store)
    demo = store.get("demo")
    for document in demo.documents:
        if document.status != "ready" or not store.extraction_path(
            demo.workspace_id, document.document_id
        ).is_file():
            _submit_document_job(demo.workspace_id, document.document_id)
    workspace_assistant.cache_clear()
    return {
        **store.get("demo").model_dump(mode="json"),
        "document_count": len(demo.documents),
    }


@app.get("/api/workspaces/{workspace_id}")
def get_workspace(workspace_id: str) -> dict[str, Any]:
    workspace = _workspace_or_404(workspace_id)
    return {
        **workspace.model_dump(mode="json"),
        "documents": [
            _document_payload(workspace_id, document) for document in workspace.documents
        ],
    }


@app.get("/api/workspaces/{workspace_id}/documents")
def workspace_documents(workspace_id: str) -> list[dict[str, Any]]:
    workspace = _workspace_or_404(workspace_id)
    return [_document_payload(workspace_id, document) for document in workspace.documents]


@app.post(
    "/api/workspaces/{workspace_id}/documents",
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def upload_documents(
    workspace_id: str,
    files: Annotated[list[UploadFile], File()],
    process: bool = Query(default=True),
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    accepted = []
    rejected = []
    for upload in files:
        try:
            content = await upload.read(100 * 1024 * 1024 + 1)
            document = workspace_store().add_document(
                workspace_id,
                content,
                upload.filename or "document",
                content_type=upload.content_type,
            )
            if process:
                _submit_document_job(workspace_id, document.document_id)
                document = workspace_store().document(workspace_id, document.document_id)
            accepted.append(_document_payload(workspace_id, document))
        except (OSError, ValueError) as error:
            rejected.append(
                {
                    "filename": upload.filename or "document.pdf",
                    "error": str(error),
                }
            )
    if not accepted:
        raise HTTPException(status_code=422, detail={"rejected": rejected})
    return {"documents": accepted, "rejected": rejected}


@app.post(
    "/api/workspaces/{workspace_id}/documents/text",
    status_code=http_status.HTTP_202_ACCEPTED,
)
def add_text_document(
    workspace_id: str,
    request: TextDocumentRequest,
    process: bool = Query(default=True),
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    document = workspace_store().add_document(
        workspace_id,
        request.text.encode("utf-8"),
        f"{request.title}.txt",
        content_type="text/plain",
    )
    if process:
        _submit_document_job(workspace_id, document.document_id)
        document = workspace_store().document(workspace_id, document.document_id)
    return _document_payload(workspace_id, document)


@app.post(
    "/api/workspaces/{workspace_id}/source-searches",
    status_code=http_status.HTTP_201_CREATED,
)
def discover_web_sources(
    workspace_id: str,
    request: WebSourceSearchRequest,
) -> dict[str, Any]:
    """Find lightweight candidates; full content is deferred until curation."""
    _workspace_or_404(workspace_id)
    started = time.perf_counter()
    trace = _new_trace("source_discovery", request.query, workspace_id=workspace_id)
    try:
        session = web_source_service().discover(workspace_id, request)
    except ValueError as error:
        trace.update(
            {
                "status": "failed",
                "error": str(error),
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        _persist_trace(trace)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001 - provider details belong in the trace
        detail = _redacted_error(error, settings())
        trace.update(
            {
                "status": "failed",
                "error": detail,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        _persist_trace(trace)
        raise HTTPException(status_code=503, detail=detail) from error
    trace.update(
        {
            "status": "completed",
            "mode": "source_discovery",
            "evidence_count": len(session.results),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "finished_at": datetime.now(UTC).isoformat(),
            "stages": [
                {
                    "name": "sources.search",
                    "label": "Search and merge web sources",
                    "service": " + ".join(request.providers),
                    "status": session.status,
                    "result_count": len(session.results),
                    "provider_errors": session.provider_errors,
                }
            ],
        }
    )
    _persist_trace(trace)
    return {**session.model_dump(mode="json"), "trace_id": trace["id"]}


@app.get("/api/workspaces/{workspace_id}/source-searches/{search_id}")
def get_web_source_search(workspace_id: str, search_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        return web_source_service().searches.get(workspace_id, search_id).model_dump(mode="json")
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Source search not found") from error


@app.post(
    "/api/workspaces/{workspace_id}/source-searches/{search_id}/import",
    status_code=http_status.HTTP_202_ACCEPTED,
)
def import_web_sources(
    workspace_id: str,
    search_id: str,
    request: WebSourceImportRequest,
) -> dict[str, Any]:
    """Extract selected pages in full and enter them into the normal ingestion queue."""
    _workspace_or_404(workspace_id)
    started = time.perf_counter()
    trace = _new_trace("source_import", "Import curated web sources", workspace_id=workspace_id)
    try:
        result = web_source_service().import_selected(
            workspace_id,
            search_id,
            request.source_ids,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    documents = []
    for document in result.documents:
        if request.process:
            _submit_document_job(workspace_id, document.document_id)
            document = workspace_store().document(workspace_id, document.document_id)
        documents.append(_document_payload(workspace_id, document))
    trace.update(
        {
            "status": "completed" if documents else "failed",
            "mode": "source_import",
            "error": None if documents else "No selected source could be extracted",
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "finished_at": datetime.now(UTC).isoformat(),
            "stages": [
                {
                    "name": "sources.extract",
                    "label": "Extract selected full-text sources",
                    "service": "Exa / Tavily extraction pool",
                    "status": "completed" if documents else "failed",
                    "selected_count": len(request.source_ids),
                    "imported_count": len(documents),
                    "errors": result.errors,
                },
                {
                    "name": "sources.ingest",
                    "label": "Queue imported sources for RAG ingestion",
                    "service": "SourceLens ingestion pipeline",
                    "status": "queued" if documents and request.process else "completed",
                },
            ],
        }
    )
    _persist_trace(trace)
    workspace_assistant.cache_clear()
    return {"documents": documents, "errors": result.errors, "trace_id": trace["id"]}


@app.get("/api/workspaces/{workspace_id}/documents/{document_id}")
def workspace_document(workspace_id: str, document_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        document = workspace_store().document(workspace_id, document_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    return _document_payload(workspace_id, document)


@app.delete(
    "/api/workspaces/{workspace_id}/documents/{document_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_workspace_document(workspace_id: str, document_id: str) -> Response:
    _workspace_or_404(workspace_id)
    try:
        workspace_store().document(workspace_id, document_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    with _processing_lock:
        if (workspace_id, document_id) in _processing_jobs:
            raise HTTPException(
                status_code=409,
                detail="Wait for document processing to finish before deleting this source",
            )
    with _rebuild_lock:
        if _workspace_rebuild_states.get(workspace_id, {}).get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail="Wait for the active vector rebuild to finish before deleting this source",
            )
    knowledge_base().delete_document(workspace_id, document_id)
    workspace_assistant.cache_clear()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/workspaces/{workspace_id}/documents/{document_id}/process",
    status_code=http_status.HTTP_202_ACCEPTED,
)
def process_workspace_document(workspace_id: str, document_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        workspace_store().document(workspace_id, document_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    if not _submit_document_job(workspace_id, document_id):
        raise HTTPException(status_code=409, detail="Document is already processing")
    document = workspace_store().document(workspace_id, document_id)
    return _document_payload(workspace_id, document)


@app.get("/api/workspaces/{workspace_id}/documents/{document_id}/pdf")
def workspace_document_pdf(workspace_id: str, document_id: str) -> FileResponse:
    _workspace_or_404(workspace_id)
    try:
        document = workspace_store().document(workspace_id, document_id)
        path = workspace_store().document_path(workspace_id, document_id)
    except (KeyError, FileNotFoundError) as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    if document.source_type != "pdf":
        raise HTTPException(status_code=404, detail="Document is not a PDF")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.original_filename,
        content_disposition_type="inline",
    )


@app.get("/api/workspaces/{workspace_id}/documents/{document_id}/source")
def workspace_document_source(workspace_id: str, document_id: str) -> FileResponse:
    _workspace_or_404(workspace_id)
    try:
        document = workspace_store().document(workspace_id, document_id)
        path = workspace_store().document_path(workspace_id, document_id)
    except (KeyError, FileNotFoundError) as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    return FileResponse(
        path,
        media_type=document.media_type,
        filename=document.original_filename,
        content_disposition_type="inline",
    )


@app.get("/api/workspaces/{workspace_id}/documents/{document_id}/progress")
def workspace_document_progress(
    workspace_id: str,
    document_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        document = workspace_store().document(workspace_id, document_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Document not found") from error
    progress = document.progress.model_dump(mode="json")
    progress["events"] = [
        event.model_dump(mode="json")
        for event in document.progress.events
        if event.sequence > after
    ]
    progress["document_id"] = document_id
    progress["status"] = document.status
    return progress


@app.get("/api/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}")
def workspace_document_page(
    workspace_id: str,
    document_id: str,
    page_number: int,
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        extraction = knowledge_base().extraction(workspace_id, document_id)
        page = next(item for item in extraction.pages if item.page_number == page_number)
    except (FileNotFoundError, StopIteration) as error:
        raise HTTPException(status_code=404, detail="Page preview not found") from error
    return {
        **page.model_dump(mode="json"),
        "document_id": document_id,
        "filename": extraction.original_filename,
        "source_url": (f"/api/workspaces/{workspace_id}/documents/{document_id}/source"),
        "pdf_url": (
            f"/api/workspaces/{workspace_id}/documents/{document_id}/pdf#page={page_number}"
            if extraction.source_type == "pdf"
            else None
        ),
        "image_url": (
            f"/api/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}/image"
            if extraction.source_type in {"pdf", "image"}
            else None
        ),
    }


@app.get("/api/workspaces/{workspace_id}/documents/{document_id}/pages/{page_number}/image")
def workspace_document_page_image(
    workspace_id: str,
    document_id: str,
    page_number: int,
) -> Response:
    try:
        path = workspace_store().document_path(workspace_id, document_id)
        record = workspace_store().document(workspace_id, document_id)
        if record.source_type == "image":
            if page_number != 1:
                raise IndexError(page_number)
            return Response(content=path.read_bytes(), media_type=record.media_type)
        if record.source_type != "pdf":
            raise ValueError("Source has no page image")
        image = render_pdf_page(path, page_number - 1, scale=1.5)
    except (KeyError, FileNotFoundError, IndexError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Page image not found") from error
    return Response(content=image, media_type="image/png")


@app.get("/api/workspaces/{workspace_id}/vector-store")
def workspace_vector_store(workspace_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    return {
        **_inspect_workspace_vector(workspace_id),
        "rebuild": _workspace_rebuild_states.get(
            workspace_id,
            {"status": "idle", "progress": 0},
        ),
        "chunking_strategy": (
            "Section-aware paragraphs, 350-token maximum, 50-token overlap; "
            "tables, figures, OCR text, and VLM summaries retain physical pages."
        ),
    }


@app.get("/api/workspaces/{workspace_id}/vector-store/chunks")
def workspace_vector_chunks(
    workspace_id: str,
    query: str,
    document_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=40)] = 12,
) -> list[dict[str, Any]]:
    _workspace_or_404(workspace_id)
    evidence = knowledge_base().search(
        workspace_id,
        query,
        top_k=limit,
        document_ids={document_id} if document_id else None,
    )
    return [item.model_dump(mode="json") for item in evidence]


@app.post(
    "/api/workspaces/{workspace_id}/vector-store/rebuild",
    status_code=http_status.HTTP_202_ACCEPTED,
)
def rebuild_workspace_vector_store(workspace_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    with _rebuild_lock:
        current = _workspace_rebuild_states.get(workspace_id, {})
        if current.get("status") == "running":
            raise HTTPException(status_code=409, detail="A rebuild is already running")
        job_id = uuid4().hex[:10]
        state = {
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": "running",
            "progress": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "error": None,
        }
        _workspace_rebuild_states[workspace_id] = state
    processing_executor().submit(_workspace_rebuild_job, workspace_id, job_id)
    return state


@app.get("/api/workspaces/{workspace_id}/vector-store/rebuild")
def workspace_rebuild_status(workspace_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    return _workspace_rebuild_states.get(
        workspace_id,
        {"workspace_id": workspace_id, "status": "idle", "progress": 0},
    )


def _integration_payload(current: Settings) -> dict[str, Any]:
    return {
        "integrations": [
            {
                "id": "exa",
                "label": "Exa Search",
                "configured": len(current.exa_keys()),
                "capabilities": ["search", "full_text", "pdf_extraction"],
                "environment_variable": "EXA_API_KEYS",
            },
            {
                "id": "tavily",
                "label": "Tavily Search",
                "configured": len(current.tavily_keys()),
                "capabilities": ["search", "full_text", "news"],
                "environment_variable": "TAVILY_API_KEYS",
            },
            {
                "id": "pexels",
                "label": "Pexels Media",
                "configured": len(current.pexels_keys()),
                "capabilities": ["photos", "videos"],
                "environment_variable": "PEXELS_API_KEYS",
            },
            {
                "id": "jamendo",
                "label": "Jamendo Music",
                "configured": len(current.jamendo_ids()),
                "capabilities": ["licensed_music"],
                "environment_variable": "JAMENDO_CLIENT_IDS",
            },
        ],
        "keys_returned": False,
        "timeout_seconds": current.research_api_timeout_seconds,
        "cooldown_seconds": current.research_api_cooldown_seconds,
        "minimum_gap_seconds": current.research_api_minimum_gap_seconds,
    }


@app.get("/api/integrations")
def integration_settings() -> dict[str, Any]:
    settings.cache_clear()
    return _integration_payload(settings())


@app.put("/api/integrations")
def update_integration_settings(request: IntegrationCredentialRequest) -> dict[str, Any]:
    mapping = {
        "exa_api_keys": "EXA_API_KEYS",
        "tavily_api_keys": "TAVILY_API_KEYS",
        "pexels_api_keys": "PEXELS_API_KEYS",
        "jamendo_client_ids": "JAMENDO_CLIENT_IDS",
    }
    for field, environment_variable in mapping.items():
        raw = getattr(request, field)
        if raw is not None:
            _save_provider_keys(environment_variable, raw)
    runtime_mapping = {
        "research_api_timeout_seconds": "RESEARCH_API_TIMEOUT_SECONDS",
        "research_api_cooldown_seconds": "RESEARCH_API_COOLDOWN_SECONDS",
        "research_api_minimum_gap_seconds": "RESEARCH_API_MINIMUM_GAP_SECONDS",
    }
    for field, environment_variable in runtime_mapping.items():
        value = getattr(request, field)
        if value is not None:
            _write_env_setting(environment_variable, str(value))
    _reset_provider_runtimes()
    return _integration_payload(settings())


@app.get("/api/provider-settings")
def provider_settings() -> dict[str, Any]:
    settings.cache_clear()
    return _provider_settings_payload(settings())


@app.post("/api/provider-settings", status_code=http_status.HTTP_201_CREATED)
def create_provider(request: ProviderCreateRequest) -> dict[str, Any]:
    current = settings()
    catalog = _provider_catalog(current)
    reserved_ids = set(_PROVIDER_CREDENTIALS)
    if request.id in reserved_ids or any(item.id == request.id for item in catalog.providers):
        raise HTTPException(status_code=409, detail="Provider id already exists")
    provider = ProviderEndpointConfig(
        id=request.id,
        label=request.label,
        adapter=request.adapter,
        base_url=request.base_url,
        api_key_env=request.environment_variable,
        enabled=request.enabled,
        models=[ProviderModelConfig.model_validate(model.model_dump()) for model in request.models],
    )
    _save_provider_catalog(
        current,
        catalog.model_copy(update={"providers": [*catalog.providers, provider]}),
    )
    if request.api_keys:
        _save_provider_keys(provider.api_key_env, request.api_keys)
    _reset_provider_runtimes()
    return _provider_by_id(_provider_settings_payload(settings()), request.id)


@app.patch("/api/provider-settings")
def update_provider_registry(request: ProviderRegistryUpdateRequest) -> dict[str, Any]:
    current = settings()
    updates: dict[str, str] = {}
    if request.provider_order is not None:
        ordered = list(dict.fromkeys(request.provider_order))
        updates["LLM_PROVIDER_ORDER"] = ",".join(ordered)
        updates["VLM_PROVIDER_ORDER"] = ",".join(ordered)
    if isinstance(request.model_order, list):
        flattened = request.model_order
        flattened = list(dict.fromkeys(flattened))
        updates["LLM_MODEL_ORDER"] = ",".join(flattened)
        updates["VLM_MODEL_ORDER"] = ",".join(flattened)
    elif request.model_order is not None:
        provider_order = request.provider_order or current.ordered_llm_providers()
        remaining = [key for key in request.model_order if key not in provider_order]
        flattened = [
            model
            for provider_id in [*provider_order, *remaining]
            for model in request.model_order.get(provider_id, [])
        ]
        flattened = list(dict.fromkeys(flattened))
        updates["LLM_MODEL_ORDER"] = ",".join(flattened)
        updates["VLM_MODEL_ORDER"] = ",".join(flattened)
    if request.disabled_providers is not None:
        updates["DISABLED_LLM_PROVIDERS"] = ",".join(dict.fromkeys(request.disabled_providers))
    if request.disabled_models is not None:
        updates["DISABLED_LLM_MODELS"] = ",".join(dict.fromkeys(request.disabled_models))
    runtime_mapping = {
        "llm_timeout_seconds": "LLM_TIMEOUT_SECONDS",
        "llm_pool_retry_rounds": "LLM_POOL_RETRY_ROUNDS",
        "llm_pool_cooldown_seconds": "LLM_POOL_COOLDOWN_SECONDS",
        "ingestion_worker_count": "INGESTION_WORKER_COUNT",
        "retrieval_top_k": "RETRIEVAL_TOP_K",
        "retrieval_min_similarity": "RETRIEVAL_MIN_SIMILARITY",
        "ocr_requests_per_minute": "OCR_REQUESTS_PER_MINUTE",
        "ocr_global_requests_per_minute": "OCR_GLOBAL_REQUESTS_PER_MINUTE",
        "ocr_cooldown_seconds": "OCR_COOLDOWN_SECONDS",
        "vlm_enabled": "VLM_ENABLED",
        "vlm_requests_per_minute": "VLM_REQUESTS_PER_MINUTE",
        "vlm_global_requests_per_minute": "VLM_GLOBAL_REQUESTS_PER_MINUTE",
        "vlm_cooldown_seconds": "VLM_COOLDOWN_SECONDS",
        "podcast_turn_timeout_seconds": "PODCAST_TURN_TIMEOUT_SECONDS",
        "podcast_minimum_key_gap_seconds": "PODCAST_MINIMUM_KEY_GAP_SECONDS",
        "podcast_idle_refresh_seconds": "PODCAST_IDLE_REFRESH_SECONDS",
    }
    if request.runtime is not None:
        for field, value in request.runtime.model_dump(exclude_none=True).items():
            updates[runtime_mapping[field]] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )
    for name, value in updates.items():
        _write_env_setting(name, value)
    _reset_provider_runtimes()
    return _provider_settings_payload(settings())


@app.put("/api/provider-settings/{provider_id}")
def update_provider(
    provider_id: str,
    request: ProviderUpdateRequest,
) -> dict[str, Any]:
    metadata = _PROVIDER_CREDENTIALS.get(provider_id)
    current = settings()
    catalog = _provider_catalog(current)
    custom_index = next(
        (index for index, provider in enumerate(catalog.providers) if provider.id == provider_id),
        None,
    )
    if metadata is None and custom_index is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    environment_variable = (
        str(metadata["environment_variable"])
        if metadata is not None
        else catalog.providers[custom_index].api_key_env  # type: ignore[index]
    )
    if custom_index is not None:
        provider = catalog.providers[custom_index]
        changes = request.model_dump(exclude_none=True, exclude={"api_keys"})
        if "environment_variable" in changes:
            changes["api_key_env"] = changes.pop("environment_variable")
        if "models" in changes:
            changes["models"] = [
                ProviderModelConfig.model_validate(model) for model in changes["models"]
            ]
        provider = ProviderEndpointConfig.model_validate({**provider.model_dump(), **changes})
        providers = list(catalog.providers)
        providers[custom_index] = provider
        _save_provider_catalog(current, catalog.model_copy(update={"providers": providers}))
        environment_variable = provider.api_key_env
    if request.api_keys is not None:
        _save_provider_keys(environment_variable, request.api_keys)
    _reset_provider_runtimes()
    return _provider_by_id(_provider_settings_payload(settings()), provider_id)


@app.delete(
    "/api/provider-settings/{provider_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_provider(provider_id: str) -> Response:
    if provider_id in _PROVIDER_CREDENTIALS:
        raise HTTPException(status_code=409, detail="Built-in providers cannot be deleted")
    current = settings()
    catalog = _provider_catalog(current)
    providers = [provider for provider in catalog.providers if provider.id != provider_id]
    if len(providers) == len(catalog.providers):
        raise HTTPException(status_code=404, detail="Provider not found")
    _save_provider_catalog(current, catalog.model_copy(update={"providers": providers}))
    _reset_provider_runtimes()
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


@app.get("/api/status")
def status() -> dict[str, Any]:
    current = settings()
    vision_routes = configured_model_routes(current, vision_only=True)
    model_routes = configured_model_routes(current)
    workspaces = workspace_store().list()
    document_count = sum(len(workspace.documents) for workspace in workspaces)
    chunk_count = sum(
        max(0, int(document.chunk_count or 0))
        for workspace in workspaces
        for document in workspace.documents
        if document.status == "ready"
    )
    cache_info = getattr(assistant, "cache_info", None)
    if callable(cache_info) and cache_info().currsize:
        llm_failover, embedding_failover = _runtime_failover_status()
    else:
        first_route = model_routes[0] if model_routes else None
        llm_failover = {
            "available": bool(model_routes),
            "provider": first_route.provider if first_route else current.llm_provider,
            "active_model": first_route.label if first_route else current.llm_model,
            "credential_count": sum(route.credential_count for route in model_routes),
            "active_slot": 1 if model_routes else None,
            "last_failover": False,
            "routes": [
                {
                    "label": route.label,
                    "provider": route.provider,
                    "credential_count": route.credential_count,
                    "active_key_slot": 1,
                    "source": route.source,
                }
                for route in model_routes
            ],
            "initialized": False,
        }
        embedding_failover = {
            "credential_count": len(current.embedding_api_keys()),
            "active_slot": 1 if current.embedding_api_keys() else None,
            "last_backend": "configured",
            "initialized": False,
        }
    return {
        "ready": bool(model_routes and chunk_count and llm_failover.get("available", True)),
        "runtime_available": llm_failover.get("available", True),
        "document_count": document_count,
        "chunk_count": chunk_count,
        "llm": llm_failover.get("active_model", current.llm_model),
        "llm_provider": llm_failover.get("provider", current.llm_provider),
        "llm_routes": llm_failover.get("routes", []),
        "embedding": current.embedding_model,
        "embedding_provider": current.embedding_provider,
        "embedding_dimensions": current.embedding_dimensions,
        "vector_database": "ChromaDB · cosine",
        "collection": _collection_name(current),
        "retrieval_top_k": current.retrieval_top_k,
        "retrieval_threshold": current.retrieval_min_similarity,
        "llm_endpoint": (
            "Capability-validated multi-provider route"
            if current.llm_routing_enabled and llm_failover.get("routes")
            else current.llm_base_url
        ),
        "voice_model": current.groq_whisper_model,
        "speech_primary": (
            "groq"
            if current.speech_primary_provider == "groq" and current.groq_speech_keys()
            else "local"
        ),
        "speech_fallback": f"faster-whisper/{current.whisper_model}",
        "groq_configured": bool(current.groq_speech_keys()),
        "llm_failover": llm_failover,
        "embedding_failover": embedding_failover,
        "processing": {
            "worker_count": current.ingestion_worker_count,
            "active_jobs": len(_processing_jobs),
        },
        "ocr": {
            "configured": bool(current.ocr_space_keys()),
            "credential_count": len(current.ocr_space_keys()),
            "provider": "OCR.Space Engine 3",
            "requests_per_minute": current.ocr_requests_per_minute,
            "global_requests_per_minute": current.ocr_global_requests_per_minute,
            "local_fallback_enabled": current.local_ocr_enabled,
        },
        "vlm": {
            "configured": bool(current.vlm_enabled and vision_routes),
            "credential_count": sum(route.credential_count for route in vision_routes),
            "model": vision_routes[0].label if vision_routes else "",
            "model_count": len(vision_routes),
            "routes": [
                {
                    "label": route.label,
                    "provider": route.provider,
                    "credential_count": route.credential_count,
                    "source": route.source,
                }
                for route in vision_routes
            ],
            "requests_per_minute": current.vlm_requests_per_minute,
            "global_requests_per_minute": current.vlm_global_requests_per_minute,
        },
        "podcast": {
            "configured": bool(current.google_llm_keys()),
            "credential_count": len(current.google_llm_keys()),
            "models": [
                "gemini-3.1-flash-live-preview",
                "gemini-2.5-flash-native-audio-preview-12-2025",
            ],
            "duration_minutes": {"minimum": 2, "maximum": 20},
            "participant_count": {"minimum": 1, "maximum": 5},
            "minimum_key_gap_seconds": current.podcast_minimum_key_gap_seconds,
            "turn_timeout_seconds": current.podcast_turn_timeout_seconds,
            "session_resumption": True,
            "context_window_compression": True,
        },
        "api_keys": {
            "llm": bool(model_routes),
            "embedding": bool(current.embedding_api_keys()),
            "groq": bool(current.groq_speech_keys()),
            "ocr": bool(current.ocr_space_keys()),
            "vlm": bool(current.vlm_enabled and vision_routes),
            "podcast": bool(current.google_llm_keys()),
        },
    }


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    return await _chat_response(request)


@app.get("/api/workspaces/{workspace_id}/conversations")
def list_conversations(workspace_id: str) -> list[dict[str, Any]]:
    _workspace_or_404(workspace_id)
    return conversation_memory().list_public(workspace_id)


@app.get("/api/workspaces/{workspace_id}/conversations/{conversation_id}")
def get_conversation(workspace_id: str, conversation_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        payload = conversation_memory().get_public(workspace_id, conversation_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Conversation not found") from error
    for turn in payload.get("turns", []):
        if turn.get("role") == "assistant":
            turn["html"] = render_markdown(turn.get("content", ""))
    return payload


@app.delete("/api/workspaces/{workspace_id}/conversations", status_code=204)
def clear_conversations(workspace_id: str) -> Response:
    _workspace_or_404(workspace_id)
    conversation_memory().delete_all(workspace_id)
    return Response(status_code=204)


@app.post("/api/chat/image")
async def chat_with_image(
    image: Annotated[UploadFile, File()],
    question: Annotated[str, Form()],
    workspace_id: Annotated[str, Form()] = "default",
    document_ids: Annotated[str, Form()] = "[]",
    history: Annotated[str, Form()] = "[]",
    conversation_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    if not (image.content_type or "").casefold().startswith("image/"):
        raise HTTPException(status_code=415, detail="Prompt attachment must be an image")
    try:
        request = ChatRequest(
            question=question,
            workspace_id=workspace_id,
            document_ids=json.loads(document_ids),
            history=json.loads(history),
            conversation_id=conversation_id,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Invalid multimodal chat fields") from error
    _workspace_or_404(request.workspace_id)
    trace = _new_trace(
        "chat",
        request.question,
        workspace_id=request.workspace_id,
    )
    started = time.perf_counter()
    content = await image.read()
    try:
        analysis = await run_in_threadpool(
            prompt_image_analyzer().analyze,
            content,
            original_filename=image.filename or "prompt-image.png",
        )
    except ValueError as error:
        _finish_trace(trace, started=started, error=error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        _finish_trace(trace, started=started, error=error)
        raise HTTPException(
            status_code=502,
            detail=f"Image analysis failed; trace id: {trace['id']}",
        ) from error
    return await _chat_response(
        request,
        model_question=prompt_image_context(request.question, analysis),
        prompt_attachment=analysis.model_dump(
            mode="json",
            exclude={"raw_text", "source_artifact"},
        ),
        trace=trace,
        started=started,
    )


async def _chat_response(
    request: ChatRequest,
    *,
    model_question: str | None = None,
    prompt_attachment: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    _workspace_or_404(request.workspace_id)
    known_documents = {
        item.document_id for item in workspace_store().get(request.workspace_id).documents
    }
    unknown = set(request.document_ids) - known_documents
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown selected document ids: {', '.join(sorted(unknown))}",
        )
    if trace is None:
        trace = _new_trace(
            "chat",
            request.question,
            workspace_id=request.workspace_id,
        )
    if started is None:
        started = time.perf_counter()
    try:
        submitted_question = model_question or request.question
        memory_context = None
        active_history = request.history
        if request.conversation_id:
            fallback_history = [(turn.role, turn.content) for turn in request.history]
            memory_context = await run_in_threadpool(
                conversation_memory().context_for,
                request.workspace_id,
                request.conversation_id,
                request.question,
                fallback_history,
            )
            active_history = [
                ChatTurn(role=role, content=content[:12_000])
                for role, content in memory_context.history
            ]
        if request.workspace_id == "demo" and not request.document_ids:
            if memory_context is None:
                answer = await run_in_threadpool(
                    _ask_locked,
                    submitted_question,
                    active_history,
                )
            else:
                answer = await run_in_threadpool(
                    _ask_locked,
                    submitted_question,
                    active_history,
                    memory_context.summary,
                    tuple(memory_context.evidence),
                )
        else:
            arguments = [
                submitted_question,
                active_history,
                request.workspace_id,
                tuple(sorted(request.document_ids)),
            ]
            if memory_context is not None:
                arguments.extend([memory_context.summary, tuple(memory_context.evidence)])
            answer = await run_in_threadpool(_ask_workspace_locked, *arguments)
        if request.conversation_id:
            await run_in_threadpool(
                conversation_memory().record_exchange,
                request.workspace_id,
                request.conversation_id,
                request.question,
                answer,
            )
    except Exception as error:
        _finish_trace(trace, started=started, error=error)
        raise HTTPException(
            status_code=502,
            detail=f"Request failed; trace id: {trace['id']}",
        ) from error
    if prompt_attachment is not None:
        trace["stages"].append(
            {
                "name": "prompt_image.analyze",
                "label": "Analyze prompt image",
                "service": prompt_attachment.get("extraction_method", "vision pipeline"),
                "status": "completed",
                "confidence": prompt_attachment.get("confidence"),
                "detected_language": prompt_attachment.get("detected_language"),
                "warnings": prompt_attachment.get("warnings", []),
                "requests": prompt_attachment.get("provider_requests", []),
            }
        )
    _finish_trace(trace, started=started, answer=answer)
    display_text = _display_text(answer.text)
    payload = answer.model_dump(mode="json")
    payload["text"] = display_text
    payload["html"] = render_markdown(display_text)
    payload["artifacts"] = _answer_artifacts(answer)
    payload["trace_id"] = trace["id"]
    payload["conversation_id"] = request.conversation_id
    if prompt_attachment is not None:
        payload["prompt_attachment"] = prompt_attachment
    for item in payload.get("evidence", []):
        document_id = item.get("document_id") or item.get("paper_id")
        page = item.get("page_start", 1)
        with suppress(KeyError):
            source = workspace_store().document(request.workspace_id, document_id)
            item["source_type"] = source.source_type
            item["source_url"] = (
                f"/api/workspaces/{request.workspace_id}/documents/{document_id}/source"
            )
            item["pdf_url"] = (
                f"/api/workspaces/{request.workspace_id}/documents/{document_id}/pdf#page={page}"
                if source.source_type == "pdf"
                else None
            )
        item["page_preview_url"] = (
            f"/api/workspaces/{request.workspace_id}/documents/{document_id}/pages/{page}"
        )
    return payload


def _studio_payload(artifact: StudioArtifact) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json")
    payload["data"] = {
        key: value for key, value in payload["data"].items() if not key.startswith("_")
    }
    if artifact.artifact_type == "flashcards" and not artifact.data:
        legacy_cards = parse_legacy_flashcards(artifact.content_markdown)
        if legacy_cards:
            payload["data"] = {"title": artifact.title, "cards": legacy_cards}
    payload["rendered_data"] = render_markdown_tree(payload["data"])
    payload["html"] = (
        render_markdown(artifact.content_markdown) if artifact.content_markdown else ""
    )
    payload["download_url"] = (
        f"/api/workspaces/{artifact.workspace_id}/studio/artifacts/{artifact.artifact_id}/download"
    )
    if artifact.artifact_type == "audio_overview":
        base = f"/api/workspaces/{artifact.workspace_id}/studio/artifacts/{artifact.artifact_id}"
        payload["audio_url"] = f"{base}/audio" if artifact.data.get("audio_filename") else None
        payload["transcript_url"] = (
            f"{base}/transcript" if artifact.data.get("transcript_filename") else None
        )
        payload["credential_status"] = {
            "configured": len(settings().google_llm_keys()),
            "required": len(artifact.options.get("participants") or []),
            "reserve": max(
                0,
                len(settings().google_llm_keys()) - len(artifact.options.get("participants") or []),
            ),
            "quota_scope": "project",
        }
    if artifact.artifact_type == "slide_deck":
        payload["presentation_url"] = (
            payload["download_url"] if artifact.data.get("pptx_filename") else None
        )
    if artifact.artifact_type == "video_overview":
        base = f"/api/workspaces/{artifact.workspace_id}/studio/artifacts/{artifact.artifact_id}"
        payload["video_url"] = f"{base}/video" if artifact.data.get("video_filename") else None
        payload["captions_url"] = (
            f"{base}/captions" if artifact.data.get("captions_filename") else None
        )
        payload["timeline_url"] = (
            f"{base}/timeline" if artifact.data.get("timeline_filename") else None
        )
    if artifact.artifact_type in {"slide_deck", "video_overview"}:
        base = f"/api/workspaces/{artifact.workspace_id}/studio/artifacts/{artifact.artifact_id}"
        payload["manifest_url"] = (
            f"{base}/manifest" if artifact.data.get("manifest_filename") else None
        )
        payload["preview_urls"] = [
            f"{base}/previews/{index}"
            for index, _ in enumerate(artifact.data.get("preview_filenames") or [])
        ]
        payload["diagnostic_urls"] = [
            f"{base}/diagnostics/{index}"
            for index, _ in enumerate(artifact.data.get("diagnostic_filenames") or [])
        ]
    return payload


def _studio_summary(artifact: StudioArtifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "workspace_id": artifact.workspace_id,
        "artifact_type": artifact.artifact_type,
        "title": artifact.title,
        "status": artifact.status,
        "stage": artifact.stage,
        "progress": artifact.progress,
        "revision": artifact.revision,
        "document_count": len(artifact.document_ids),
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
        "error": artifact.error,
    }


@app.get("/api/studio/catalog")
def studio_catalog() -> list[dict[str, Any]]:
    return studio_creation_catalog()


@app.get("/fonts/{filename}")
def public_web_font(filename: str) -> FileResponse:
    path = PUBLIC_WEB_FONTS.get(filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown public font")
    return FileResponse(path, media_type="font/ttf")


@app.get("/api/fonts")
def list_fonts() -> dict[str, Any]:
    return {
        "default": {"font_id": "auto", "family": "Vazirmatn"},
        "fonts": [asset.payload() for asset in font_registry().list()],
    }


@app.post("/api/fonts", status_code=http_status.HTTP_201_CREATED)
async def upload_font(file: Annotated[UploadFile, File(...)]) -> dict[str, Any]:
    content = await file.read(FontRegistry.maximum_bytes + 1)
    try:
        return font_registry().add(file.filename or "font.ttf", content).payload()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.delete("/api/fonts/{font_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_font(font_id: str) -> Response:
    try:
        font_registry().remove(font_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Font not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


def _generate_studio_job(workspace_id: str, artifact_id: str, trace: dict[str, Any]) -> None:
    owner = f"studio-{threading.get_ident()}"
    studio_artifact_store().acquire_lease(
        workspace_id, artifact_id, owner=owner, checkpoint_safe=False
    )
    started = time.perf_counter()
    generation_started = time.perf_counter()
    artifact = studio_service().generate(artifact_id, workspace_id=workspace_id)
    generation_ms = round((time.perf_counter() - generation_started) * 1000)
    render_ms = 0
    if artifact.status == "generating" and artifact.artifact_type in {
        "slide_deck",
        "video_overview",
    }:
        render_started = time.perf_counter()
        artifact = visual_artifact_service().materialize(artifact)
        render_ms = round((time.perf_counter() - render_started) * 1000)
    job_status = "completed" if artifact.status == "ready" else artifact.status
    trace.update(
        {
            "status": job_status,
            "mode": "studio",
            "provider": artifact.provider,
            "model": artifact.model,
            "evidence_count": len(artifact.citations),
            "selected_evidence": [item.model_dump(mode="json") for item in artifact.citations],
            "error": artifact.error,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )
    trace["stages"].extend(
        [
            {
                "name": "studio.sources",
                "label": "Collect selected-source evidence",
                "service": "SourceLens hybrid retrieval",
                "status": "cancelled" if artifact.status == "cancelled" else (
                    "completed" if artifact.citations else "failed"
                ),
            },
            {
                "name": "studio.generate",
                "label": f"Generate {artifact.artifact_type}",
                "service": artifact.model or "LLM provider pool",
                "status": "completed" if artifact.status == "ready" else artifact.status,
                "duration_ms": generation_ms,
            },
            *(
                [
                    {
                        "name": "studio.render_file",
                        "label": (
                            "Render editable PowerPoint"
                            if artifact.artifact_type == "slide_deck"
                            else "Render video overview"
                        ),
                        "service": (
                            "SourceLens deck engine + Pexels"
                            if artifact.artifact_type == "slide_deck"
                            else "FFmpeg + Pexels + Gemini Live + Jamendo"
                        ),
                        "status": "completed" if artifact.status == "ready" else artifact.status,
                        "duration_ms": render_ms,
                        "warnings": artifact.data.get("render_warnings") or [],
                    }
                ]
                if artifact.artifact_type in {"slide_deck", "video_overview"}
                else []
            ),
        ]
    )
    _persist_trace(trace)
    studio_artifact_store().release_lease(workspace_id, artifact_id, owner=owner)


def _prepare_podcast_job(workspace_id: str, artifact_id: str, trace: dict[str, Any]) -> None:
    owner = f"podcast-plan-{threading.get_ident()}"
    studio_artifact_store().acquire_lease(
        workspace_id, artifact_id, owner=owner, checkpoint_safe=False
    )
    started = time.perf_counter()
    artifact = podcast_service().prepare(workspace_id, artifact_id)
    draft_ready = artifact.status in {"script_ready", "plan_ready"}
    trace.update(
        {
            "status": "completed" if draft_ready else "failed",
            "mode": (
                "studio.audio_overview.review"
                if artifact.status == "script_ready"
                else "studio.audio_overview.plan"
            ),
            "provider": artifact.provider,
            "model": artifact.model,
            "evidence_count": len(artifact.citations),
            "selected_evidence": [item.model_dump(mode="json") for item in artifact.citations],
            "error": artifact.error,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )
    trace["stages"].extend(
        [
            {
                "name": "podcast.sources",
                "label": "Collect selected-source evidence",
                "service": "SourceLens hybrid retrieval",
                "status": "completed" if artifact.citations else "failed",
            },
            {
                "name": "podcast.draft",
                "label": (
                    "Prepare reviewable dialogue"
                    if artifact.status == "script_ready"
                    else "Prepare episode plan"
                ),
                "service": artifact.model or "LLM provider pool",
                "status": "completed" if draft_ready else "failed",
            },
        ]
    )
    if artifact.status != "plan_ready":
        _persist_trace(trace)
        studio_artifact_store().release_lease(workspace_id, artifact_id, owner=owner)
        return

    keys = settings().google_llm_keys()
    required = len(artifact.options.get("participants") or [])
    if len(keys) < required:
        trace["stages"].append(
            {
                "name": "podcast.credentials",
                "label": "Wait for enough Gemini audio credentials",
                "service": "Provider settings",
                "status": "waiting",
            }
        )
        _persist_trace(trace)
        studio_artifact_store().release_lease(workspace_id, artifact_id, owner=owner)
        return
    try:
        podcast_service().queue_audio(workspace_id, artifact_id, len(keys))
    except (KeyError, ValueError) as error:
        trace.update({"status": "failed", "error": str(error)})
        _persist_trace(trace)
        studio_artifact_store().release_lease(workspace_id, artifact_id, owner=owner)
        return
    trace.update(
        {
            "status": "running",
            "mode": "studio.audio_overview.render",
            "finished_at": None,
        }
    )
    _persist_trace(trace)
    studio_artifact_store().release_lease(workspace_id, artifact_id, owner=owner)
    podcast_executor().submit(
        _generate_podcast_audio_job,
        workspace_id,
        artifact_id,
        trace,
    )


def _generate_podcast_audio_job(
    workspace_id: str,
    artifact_id: str,
    trace: dict[str, Any],
) -> None:
    owner = f"podcast-audio-{threading.get_ident()}"
    studio_artifact_store().acquire_lease(
        workspace_id, artifact_id, owner=owner, checkpoint_safe=True
    )
    started = time.perf_counter()
    artifact = podcast_service().generate_audio(
        workspace_id,
        artifact_id,
        settings().google_llm_keys(),
    )
    trace.update(
        {
            "status": "completed" if artifact.status == "ready" else artifact.status,
            "mode": "studio.audio_overview.render",
            "provider": artifact.provider,
            "model": artifact.model,
            "error": artifact.error,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )
    turns = list(artifact.data.get("generated_turns") or [])
    requests = []
    warnings = []
    for turn in turns:
        warnings.extend(str(item) for item in turn.get("warnings") or [])
        for attempt in turn.get("attempts") or []:
            requests.append(
                {
                    "slot": attempt.get("provider_slot"),
                    "status": attempt.get("status"),
                    "duration_ms": attempt.get("latency_ms"),
                    "wait_ms": attempt.get("wait_ms"),
                    "error_type": attempt.get("error_type"),
                    "status_code": attempt.get("status_code"),
                }
            )
    trace["stages"].append(
        {
            "name": "podcast.render",
            "label": "Render resumable Gemini Live audio",
            "service": artifact.model or "Gemini Live API",
            "status": "completed" if artifact.status == "ready" else artifact.status,
            "requests": requests,
            "warnings": list(dict.fromkeys(warnings)),
            "failover_used": any(item.get("status") == "failed" for item in requests),
        }
    )
    _persist_trace(trace)
    studio_artifact_store().release_lease(workspace_id, artifact_id, owner=owner)


@app.post(
    "/api/workspaces/{workspace_id}/studio/artifacts",
    status_code=http_status.HTTP_202_ACCEPTED,
)
def create_studio_artifact(
    workspace_id: str,
    request: StudioCreateRequest,
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        artifact = studio_service().create(
            StudioGenerationRequest(
                workspace_id=workspace_id,
                **request.model_dump(mode="python"),
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    trace = _new_trace(
        "studio",
        f"Generate {request.artifact_type}",
        workspace_id=workspace_id,
    )
    job = (
        _prepare_podcast_job if request.artifact_type == "audio_overview" else _generate_studio_job
    )
    studio_executor().submit(
        job,
        workspace_id,
        artifact.artifact_id,
        trace,
    )
    payload = _studio_payload(artifact)
    payload["trace_id"] = trace["id"]
    return payload


@app.put("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/script")
def update_podcast_script(
    workspace_id: str,
    artifact_id: str,
    request: PodcastScriptUpdateRequest,
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        artifact = podcast_service().update_script(
            workspace_id,
            artifact_id,
            [item.model_dump(mode="json") for item in request.turns],
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _studio_payload(artifact)


@app.post(
    "/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/generate-audio",
    status_code=http_status.HTTP_202_ACCEPTED,
)
def generate_podcast_audio(workspace_id: str, artifact_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    keys = settings().google_llm_keys()
    try:
        podcast_service().queue_audio(workspace_id, artifact_id, len(keys))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    trace = _new_trace("studio", "Render audio overview", workspace_id=workspace_id)
    podcast_executor().submit(
        _generate_podcast_audio_job,
        workspace_id,
        artifact_id,
        trace,
    )
    latest = studio_artifact_store().get(workspace_id, artifact_id)
    payload = _studio_payload(latest)
    payload["trace_id"] = trace["id"]
    return payload


@app.post("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/cancel-audio")
def cancel_podcast_audio(workspace_id: str, artifact_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        return _studio_payload(podcast_service().request_cancel(workspace_id, artifact_id))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Audio overview not found") from error


@app.post("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/cancel")
def cancel_studio_artifact(workspace_id: str, artifact_id: str) -> dict[str, Any]:
    """Cancel any active Studio artifact, including visual rendering jobs."""
    _workspace_or_404(workspace_id)
    try:
        artifact = studio_artifact_store().get(workspace_id, artifact_id)
        if artifact.artifact_type == "audio_overview":
            artifact = podcast_service().request_cancel(workspace_id, artifact_id)
        else:
            artifact = studio_service().request_cancel(workspace_id, artifact_id)
        return _studio_payload(artifact)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Studio artifact not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/retry")
def retry_studio_artifact(workspace_id: str, artifact_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    store = studio_artifact_store()
    try:
        artifact = store.get(workspace_id, artifact_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Studio artifact not found") from error
    if artifact.status not in {"failed", "interrupted"}:
        raise HTTPException(status_code=409, detail="Only failed or interrupted jobs can retry")
    queued = store.save(
        artifact.model_copy(
            update={
                "status": "queued",
                "stage": "retry_queued",
                "progress": 0,
                "error": None,
                "data": {
                    **artifact.data,
                    "retryable": False,
                    "cancel_requested": False,
                },
            }
        )
    )
    store.record_event(
        workspace_id,
        artifact_id,
        stage="queue",
        status="queued",
        message="Studio retry queued",
        current=0,
        total=1,
    )
    trace = _new_trace("studio", f"Retry {artifact.artifact_type}", workspace_id=workspace_id)
    job = (
        _prepare_podcast_job
        if artifact.artifact_type == "audio_overview"
        else _generate_studio_job
    )
    studio_executor().submit(job, workspace_id, artifact_id, trace)
    return _studio_payload(queued)


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/audio")
def podcast_audio(workspace_id: str, artifact_id: str) -> FileResponse:
    _workspace_or_404(workspace_id)
    try:
        artifact = studio_artifact_store().get(workspace_id, artifact_id)
        filename = str(artifact.data.get("audio_filename") or "")
        path = studio_artifact_store().asset_path(workspace_id, artifact_id, filename)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Podcast audio not found") from error
    if artifact.status != "ready" or not path.is_file():
        raise HTTPException(status_code=409, detail="Podcast audio is not ready")
    media_type = "audio/mpeg" if path.suffix.casefold() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media_type)


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/transcript")
def podcast_transcript(workspace_id: str, artifact_id: str) -> FileResponse:
    _workspace_or_404(workspace_id)
    try:
        artifact = studio_artifact_store().get(workspace_id, artifact_id)
        filename = str(artifact.data.get("transcript_filename") or "")
        path = studio_artifact_store().asset_path(workspace_id, artifact_id, filename)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Podcast transcript not found") from error
    if artifact.status != "ready" or not path.is_file():
        raise HTTPException(status_code=409, detail="Podcast transcript is not ready")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)


def _visual_asset(
    workspace_id: str,
    artifact_id: str,
    *,
    field: str,
    artifact_type: str,
) -> tuple[StudioArtifact, Path]:
    artifact, path = _visual_sidecar(workspace_id, artifact_id, field=field)
    if artifact.artifact_type != artifact_type:
        raise HTTPException(status_code=404, detail="Generated visual asset not found")
    return artifact, path


def _visual_sidecar(
    workspace_id: str,
    artifact_id: str,
    *,
    field: str,
    index: int | None = None,
) -> tuple[StudioArtifact, Path]:
    try:
        artifact = studio_artifact_store().get(workspace_id, artifact_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Generated visual asset not found") from error
    if artifact.artifact_type not in {"slide_deck", "video_overview"}:
        raise HTTPException(status_code=404, detail="Generated visual asset not found")
    value = artifact.data.get(field)
    if index is not None:
        values = value if isinstance(value, list) else []
        if index < 0 or index >= len(values):
            raise HTTPException(status_code=404, detail="Generated visual asset not found")
        value = values[index]
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=404, detail="Generated visual asset not found")
    store = studio_artifact_store()
    base = store.asset_path(
        workspace_id, artifact_id, "visual-engine-manifest.json"
    ).parent.resolve()
    candidate = (base / value).resolve()
    if base not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Generated visual asset not found")
    if artifact.status != "ready":
        raise HTTPException(status_code=409, detail="Generated visual asset is not ready")
    return artifact, candidate


def _public_visual_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public_visual_json(child)
            for key, child in value.items()
            if key != "artifact_id"
        }
    if isinstance(value, list):
        return [_public_visual_json(child) for child in value]
    if isinstance(value, str) and re.fullmatch(r"artifact-[a-f0-9]{12}", value, re.I):
        return "public"
    return value


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/manifest")
def studio_visual_manifest(workspace_id: str, artifact_id: str) -> Response:
    _, path = _visual_sidecar(
        workspace_id,
        artifact_id,
        field="manifest_filename",
    )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=500, detail="Generated visual manifest is invalid"
        ) from error
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=500, detail="Generated visual manifest is invalid")
    manifest = _public_visual_json(manifest)
    return Response(
        content=json.dumps(manifest, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'inline; filename="visual-engine-manifest.json"'},
    )


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/previews/{index}")
def studio_visual_preview(workspace_id: str, artifact_id: str, index: int) -> FileResponse:
    _, path = _visual_sidecar(
        workspace_id,
        artifact_id,
        field="preview_filenames",
        index=index,
    )
    return FileResponse(path, content_disposition_type="inline")


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/diagnostics/{index}")
def studio_visual_diagnostic(workspace_id: str, artifact_id: str, index: int) -> FileResponse:
    _, path = _visual_sidecar(
        workspace_id,
        artifact_id,
        field="diagnostic_filenames",
        index=index,
    )
    return FileResponse(path, content_disposition_type="inline")


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/video")
def studio_video(workspace_id: str, artifact_id: str) -> FileResponse:
    _, path = _visual_asset(
        workspace_id,
        artifact_id,
        field="video_filename",
        artifact_type="video_overview",
    )
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/captions")
def studio_video_captions(workspace_id: str, artifact_id: str) -> FileResponse:
    _, path = _visual_asset(
        workspace_id,
        artifact_id,
        field="captions_filename",
        artifact_type="video_overview",
    )
    return FileResponse(path, media_type="application/x-subrip; charset=utf-8", filename=path.name)


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/timeline")
def studio_video_timeline(workspace_id: str, artifact_id: str) -> Response:
    _, path = _visual_asset(
        workspace_id,
        artifact_id,
        field="timeline_filename",
        artifact_type="video_overview",
    )
    try:
        timeline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=500, detail="Generated video timeline is invalid"
        ) from error
    return Response(
        content=json.dumps(_public_visual_json(timeline), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'inline; filename="video-timeline.json"'},
    )


@app.get("/api/workspaces/{workspace_id}/studio/artifacts")
def list_studio_artifacts(
    workspace_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    page = studio_artifact_store().list_page(workspace_id, limit=limit, cursor=cursor)
    return {
        "items": [_studio_summary(item) for item in page["items"]],
        "next_cursor": page["next_cursor"],
    }


def _studio_stream_is_terminal(status: str) -> bool:
    return status in {
        "ready",
        "failed",
        "cancelled",
        "interrupted",
        "script_ready",
        "plan_ready",
    }


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/events")
async def studio_artifact_events(
    request: Request,
    workspace_id: str,
    artifact_id: str,
    cursor: int = Query(default=0, ge=0),
    follow: bool = True,
    tail: bool = False,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    _workspace_or_404(workspace_id)
    store = studio_artifact_store()
    try:
        store.get(workspace_id, artifact_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Studio artifact not found") from error
    try:
        after = max(cursor, int(last_event_id or 0))
    except ValueError:
        after = cursor
    if tail and last_event_id is None and cursor == 0:
        latest = store.events(workspace_id, artifact_id, limit=1, tail=True)
        if latest:
            after = max(0, int(latest[0]["sequence"]) - 1)

    async def stream():
        nonlocal after
        terminal_idle_polls = 0
        while True:
            replay = store.events(workspace_id, artifact_id, after=after)
            for event in replay:
                after = event["sequence"]
                yield (
                    f"id: {after}\n"
                    "event: studio\n"
                    f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
            artifact = store.get(workspace_id, artifact_id)
            if not follow:
                return
            if _studio_stream_is_terminal(artifact.status):
                terminal_event_seen = any(
                    event.get("details", {}).get("artifact_status") == artifact.status
                    for event in replay
                )
                if terminal_event_seen:
                    return
                terminal_idle_polls = 0 if replay else terminal_idle_polls + 1
                if terminal_idle_polls >= 2:
                    return
            else:
                terminal_idle_polls = 0
            if await request.is_disconnected():
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}")
def get_studio_artifact(workspace_id: str, artifact_id: str) -> dict[str, Any]:
    _workspace_or_404(workspace_id)
    try:
        store = studio_artifact_store()
        payload = _studio_payload(store.get(workspace_id, artifact_id))
        payload["live_events"] = store.events(
            workspace_id, artifact_id, limit=50, tail=True
        )
        return payload
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Studio artifact not found") from error


@app.get("/api/workspaces/{workspace_id}/studio/artifacts/{artifact_id}/download")
def download_studio_artifact(workspace_id: str, artifact_id: str) -> Response:
    _workspace_or_404(workspace_id)
    try:
        artifact = studio_artifact_store().get(workspace_id, artifact_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Studio artifact not found") from error
    if artifact.status != "ready":
        raise HTTPException(status_code=409, detail="Studio artifact is not ready")
    if artifact.artifact_type == "audio_overview":
        filename = str(artifact.data.get("audio_filename") or "")
        try:
            path = studio_artifact_store().asset_path(workspace_id, artifact_id, filename)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Podcast audio not found") from error
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Podcast audio not found")
        media_type = "audio/mpeg" if path.suffix.casefold() == ".mp3" else "audio/wav"
        return FileResponse(path, media_type=media_type, filename=path.name)
    if artifact.artifact_type == "slide_deck":
        try:
            path = studio_artifact_store().asset_path(
                workspace_id, artifact_id, str(artifact.data.get("pptx_filename") or "")
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Presentation file not found") from error
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Presentation file not found")
        return FileResponse(
            path,
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ),
            filename=f"{artifact.title[:100]}.pptx",
        )
    if artifact.artifact_type == "video_overview":
        _, path = _visual_asset(
            workspace_id,
            artifact_id,
            field="video_filename",
            artifact_type="video_overview",
        )
        return FileResponse(path, media_type="video/mp4", filename=f"{artifact.title[:100]}.mp4")
    if artifact.data:
        content = json.dumps(artifact.data, ensure_ascii=False, indent=2)
        media_type = "application/json"
        suffix = "json"
    else:
        sources = "\n".join(
            f"- [{item.label}] {item.filename}, page {item.page_start}"
            for item in artifact.citations
        )
        content = f"# {artifact.title}\n\n{artifact.content_markdown}\n\n## Sources\n\n{sources}\n"
        media_type = "text/markdown; charset=utf-8"
        suffix = "md"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (f'attachment; filename="{artifact.artifact_id}.{suffix}"')
        },
    )


@app.post("/api/tools/{tool_name}")
async def run_tool(tool_name: str, request: ToolRequest) -> dict[str, Any]:
    allowed = {
        "generate_survey",
        "find_research_gap",
        "compare_limitations",
        "search_documents",
        "summarize_document",
        "compare_documents",
        "extract_tables",
        "extract_figures",
        "inspect_workspace",
        "rebuild_knowledge_base",
    }
    if tool_name not in allowed:
        raise HTTPException(status_code=404, detail="Unknown research function")
    _workspace_or_404(request.workspace_id)
    trace = _new_trace(
        "function",
        f"{tool_name}()",
        workspace_id=request.workspace_id,
    )
    started = time.perf_counter()
    try:
        if tool_name in {spec.name for spec in ResearchTools.specs()}:
            ResearchTools.validate_arguments(tool_name, request.arguments)
        else:
            workspace_assistant(request.workspace_id, ()).tools.validate_available_arguments(
                tool_name, request.arguments
            )
    except ToolArgumentError as error:
        _finish_trace(trace, started=started, error=error)
        raise HTTPException(
            status_code=422,
            detail="Invalid tool arguments",
        ) from error
    try:
        raw = await run_in_threadpool(
            _execute_workspace_tool_locked,
            tool_name,
            request.arguments,
            request.workspace_id,
        )
    except Exception as error:
        _finish_trace(trace, started=started, error=error)
        raise HTTPException(
            status_code=502,
            detail=f"اجرای تابع ناموفق بود؛ شناسه پیگیری: {trace['id']}",
        ) from error

    payload = json.loads(raw)
    for key in ("json_path", "csv_path", "markdown_path", "chart_path", "path"):
        if key in payload:
            payload[f"{key.removesuffix('_path')}_url"] = _artifact_url(payload[key])
    markdown_path = payload.get("markdown_path") or payload.get("path")
    if markdown_path and Path(markdown_path).is_file():
        text = Path(markdown_path).read_text(encoding="utf-8")
        display_text = _display_text(text)
        payload["report_markdown"] = display_text
        payload["report_html"] = render_markdown(display_text)
    trace.update(
        {
            "status": "completed",
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "finished_at": datetime.now(UTC).isoformat(),
            "tools": [tool_name],
        }
    )
    trace["stages"].extend(
        [
            {
                "name": "function.call",
                "label": f"اجرای مستقیم {tool_name}()",
                "service": "ResearchTools",
                "status": "completed",
            },
            {
                "name": "artifact.persist",
                "label": "ذخیره و آماده‌سازی فایل‌های خروجی",
                "service": "Local filesystem",
                "status": "completed",
            },
        ]
    )
    _persist_trace(trace)
    payload["trace_id"] = trace["id"]
    return payload


@app.get("/api/traces")
def traces(
    limit: Annotated[int, Query(ge=1, le=80)] = 30,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    with _trace_lock:
        memory_values = list(_traces)
    stored_values = _stored_traces(workspace_id)
    values = {item["id"]: item for item in stored_values if "id" in item}
    values.update({item["id"]: item for item in memory_values if "id" in item})
    ordered = sorted(
        values.values(),
        key=lambda item: str(item.get("started_at") or ""),
        reverse=True,
    )
    if workspace_id:
        ordered = [item for item in ordered if item.get("workspace_id") == workspace_id]
    return ordered[:limit]


@app.get("/api/traces/{trace_id}")
def trace_detail(trace_id: str) -> dict[str, Any]:
    with _trace_lock:
        trace = next((item for item in _traces if item["id"] == trace_id), None)
    if trace is None:
        trace = next(
            (item for item in _stored_traces() if item.get("id") == trace_id),
            None,
        )
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.post("/api/transcribe")
async def transcribe_audio(audio: Annotated[UploadFile, File()]) -> dict[str, Any]:
    media_suffixes = {
        "audio/webm": ".webm",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/ogg": ".ogg",
    }
    media_type = (audio.content_type or "").split(";", 1)[0].casefold()
    if media_type not in media_suffixes:
        raise HTTPException(status_code=415, detail="Unsupported audio format")
    maximum_bytes = 25 * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while chunk := await audio.read(1024 * 1024):
        total += len(chunk)
        if total > maximum_bytes:
            raise HTTPException(status_code=413, detail="Audio input exceeds the 25 MB limit")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail="Audio input is empty")
    suffix = media_suffixes[media_type]
    trace = _new_trace("speech", audio.filename or "voice recording")
    started = time.perf_counter()
    try:
        result = await run_in_threadpool(_transcribe_locked, content, suffix)
    except Exception as error:
        _finish_trace(trace, started=started, error=error)
        raise HTTPException(
            status_code=502,
            detail=f"تبدیل صوت ناموفق بود؛ شناسه پیگیری: {trace['id']}",
        ) from error
    _finish_trace(trace, started=started, transcription=result)
    payload = result.model_dump(mode="json")
    payload["trace_id"] = trace["id"]
    return payload


OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
mimetypes.add_type("application/javascript", ".mjs")
install_domain_routers(app)
app.mount("/artifacts", StaticFiles(directory=OUTPUTS_ROOT), name="artifacts")
app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
