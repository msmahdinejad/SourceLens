from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from paperlens.config import Settings
from paperlens.provider_config import load_provider_catalog

CredentialPool = str


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Secret-free description of one validated chat/vision route."""

    label: str
    model: str
    provider: str
    base_url: str
    credential_pool: CredentialPool
    credential_count: int = 0
    vision: bool = False
    source: str = "built-in"
    credential_env: str | None = None
    chat_priority: int = 1000
    vision_priority: int = 1000


_GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GROQ_URL = "https://api.groq.com/openai/v1"
_MISTRAL_URL = "https://api.mistral.ai/v1"
_NVIDIA_URL = "https://integrate.api.nvidia.com/v1"

# One ordered catalog powers both assistant responses and page vision. Vision is
# explicit capability metadata from provider documentation, never inferred from
# a model name or endpoint.
MODEL_ROUTES: tuple[ModelRoute, ...] = (
    ModelRoute(
        "Gemini 3.6 Flash",
        "gemini-3.6-flash",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemini 3.5 Flash",
        "gemini-3.5-flash",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemini 3 Flash · Preview",
        "gemini-3-flash-preview",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemini 2.5 Flash",
        "gemini-2.5-flash",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemma 4 31B · Google",
        "gemma-4-31b-it",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemma 4 26B · Google",
        "gemma-4-26b-a4b-it",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemini 3.5 Flash Lite",
        "gemini-3.5-flash-lite",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    ModelRoute(
        "Gemini 3.1 Flash Lite",
        "gemini-3.1-flash-lite",
        "Google Gemini API",
        _GOOGLE_URL,
        "google",
        vision=True,
    ),
    # Do not add gemini-2.5-flash-lite here until Google's generation endpoint
    # serves it for this account. Its quota-console row is present, but a live
    # generate request returns 404 NOT_FOUND (not available to new users).
    ModelRoute(
        "GPT-OSS 120B · Groq",
        "openai/gpt-oss-120b",
        "Groq API",
        _GROQ_URL,
        "groq",
    ),
    ModelRoute(
        "Mistral Large 3 · 2512",
        "mistral-large-2512",
        "Mistral API",
        _MISTRAL_URL,
        "mistral",
        vision=True,
    ),
    ModelRoute(
        "Mistral Medium 3.5",
        "mistral-medium-3-5",
        "Mistral API",
        _MISTRAL_URL,
        "mistral",
        vision=True,
    ),
    ModelRoute(
        "Nemotron 3 Ultra 550B · NVIDIA",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "NVIDIA NIM API",
        _NVIDIA_URL,
        "nvidia",
    ),
    ModelRoute(
        "Mistral Small 4 · 2603",
        "mistral-small-2603",
        "Mistral API",
        _MISTRAL_URL,
        "mistral",
        vision=True,
    ),
    ModelRoute(
        "Mistral Small 3.2 · 2506",
        "mistral-small-2506",
        "Mistral API",
        _MISTRAL_URL,
        "mistral",
        vision=True,
    ),
    ModelRoute(
        "Qwen 3.6 27B · Groq",
        "qwen/qwen3.6-27b",
        "Groq API",
        _GROQ_URL,
        "groq",
        vision=True,
    ),
    ModelRoute(
        "GPT-OSS 20B · Groq",
        "openai/gpt-oss-20b",
        "Groq API",
        _GROQ_URL,
        "groq",
    ),
    ModelRoute(
        "Llama 3.1 8B Instant · Groq",
        "llama-3.1-8b-instant",
        "Groq API",
        _GROQ_URL,
        "groq",
    ),
    ModelRoute(
        "Ministral 14B · 2512",
        "ministral-14b-2512",
        "Mistral API",
        _MISTRAL_URL,
        "mistral",
        vision=True,
    ),
    ModelRoute(
        "Ministral 8B · 2512",
        "ministral-8b-2512",
        "Mistral API",
        _MISTRAL_URL,
        "mistral",
        vision=True,
    ),
    ModelRoute(
        "Inkling · NVIDIA",
        "thinkingmachines/inkling",
        "NVIDIA NIM API",
        _NVIDIA_URL,
        "nvidia",
        vision=True,
    ),
    # Groq announced retirement for 2026-08-16. Keep this user-requested route
    # only as the final temporary fallback so healthy current models win first.
    ModelRoute(
        "Llama 3.3 70B · Groq (retiring)",
        "llama-3.3-70b-versatile",
        "Groq API",
        _GROQ_URL,
        "groq",
    ),
)

_VISION_PRIORITY = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "thinkingmachines/inkling",
    "mistral-large-2512",
    "mistral-medium-3-5",
    "qwen/qwen3.6-27b",
    "mistral-small-2603",
    "mistral-small-2506",
    "ministral-14b-2512",
    "ministral-8b-2512",
)


def route_api_keys(settings: Settings, route: ModelRoute) -> list[str]:
    if route.credential_env:
        return credential_env_keys(route.credential_env)
    pools = {
        "google": settings.google_llm_keys,
        "groq": settings.groq_chat_keys,
        "mistral": settings.mistral_keys,
        "nvidia": settings.nvidia_keys,
    }
    return pools[route.credential_pool]()


def credential_env_keys(name: str, *, env_path: Path | None = None) -> list[str]:
    """Read an arbitrary provider pool from process env or the local .env file."""
    value = os.getenv(name, "")
    candidate = env_path or Path.cwd() / ".env"
    if not value and candidate.is_file():
        prefix = f"{name}="
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(prefix):
                value = line.lstrip()[len(prefix) :].strip().strip("\"'")
                break
    return _split_keys(value)


def _custom_model_routes(settings: Settings) -> list[ModelRoute]:
    path = settings.provider_config_path
    if path is not None and not path.is_absolute():
        path = settings.resolve_path(path)
    catalog = load_provider_catalog(path)
    routes: list[ModelRoute] = []
    for provider in catalog.providers:
        if not provider.enabled:
            continue
        for model in provider.models:
            if not model.enabled:
                continue
            routes.append(
                ModelRoute(
                    label=model.label,
                    model=model.id,
                    provider=provider.label,
                    base_url=provider.base_url,
                    credential_pool=provider.id,
                    vision="vision" in model.capabilities,
                    source="custom",
                    credential_env=provider.api_key_env,
                    chat_priority=model.chat_priority,
                    vision_priority=model.vision_priority,
                )
            )
    return routes


def configured_model_routes(
    settings: Settings,
    *,
    vision_only: bool = False,
) -> list[ModelRoute]:
    return [
        route
        for route in all_model_routes(settings, vision_only=vision_only)
        if route.credential_count
    ]


def all_model_routes(
    settings: Settings,
    *,
    vision_only: bool = False,
) -> list[ModelRoute]:
    """Return the effective secret-free route catalog in runtime priority order."""
    routes = []
    disabled_providers = settings.disabled_model_providers()
    disabled_models = settings.disabled_models()
    for route in (*MODEL_ROUTES, *_custom_model_routes(settings)):
        if vision_only and not route.vision:
            continue
        route_id = f"{route.credential_pool}:{route.model}"
        if (
            route.credential_pool in disabled_providers
            or route_id in disabled_models
            or route.model in disabled_models
        ):
            continue
        credential_count = len(route_api_keys(settings, route))
        routes.append(
            ModelRoute(
                label=route.label,
                model=route.model,
                provider=route.provider,
                base_url=route.base_url,
                credential_pool=route.credential_pool,
                credential_count=credential_count,
                vision=route.vision,
                source=route.source,
                credential_env=route.credential_env,
                chat_priority=route.chat_priority,
                vision_priority=route.vision_priority,
            )
        )
    provider_order = (
        settings.ordered_vlm_providers() if vision_only else settings.ordered_llm_providers()
    )
    model_order = settings.ordered_vlm_models() if vision_only else settings.ordered_llm_models()
    if provider_order or model_order:
        provider_rank = {provider: index for index, provider in enumerate(provider_order)}
        model_rank = {model: index for index, model in enumerate(model_order)}
        builtin_rank = {route.model: index for index, route in enumerate(MODEL_ROUTES)}

        def configured_rank(route: ModelRoute) -> tuple[int, int, int, int]:
            fallback = route.vision_priority if vision_only else route.chat_priority
            route_id = f"{route.credential_pool}:{route.model}"
            return (
                model_rank.get(route_id, model_rank.get(route.model, len(model_rank) + 1)),
                provider_rank.get(route.credential_pool, len(provider_rank) + 1),
                fallback if route.source == "custom" else 100,
                builtin_rank.get(route.model, 10_000),
            )

        routes.sort(key=configured_rank)
    elif vision_only:
        rank = {model: index for index, model in enumerate(_VISION_PRIORITY)}
        routes.sort(
            key=lambda route: (
                route.vision_priority
                if route.source == "custom"
                else 100 + rank.get(route.model, len(rank))
            )
        )
    else:
        routes.sort(key=lambda route: route.chat_priority if route.source == "custom" else 100)
    return routes


def _split_keys(value: str) -> list[str]:
    keys: list[str] = []
    for item in value.replace("\n", ",").split(","):
        normalized = item.strip()
        if normalized and normalized not in keys:
            keys.append(normalized)
    return keys
