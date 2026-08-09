from __future__ import annotations

import json

from paperlens.config import Settings
from paperlens.model_routes import configured_model_routes, route_api_keys


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="openai",
        llm_model="gemini-3.6-flash",
        llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        llm_api_key="google-one",
        gemini_api_keys="google-two",
        groq_chat_api_keys="groq-one,groq-two",
        mistral_api_keys="mistral-one",
        nvidia_api_keys="nvidia-one",
    )


def test_shared_catalog_contains_only_live_tool_capable_additions() -> None:
    routes = configured_model_routes(_settings())

    models = [route.model for route in routes]
    assert {
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-20b",
        "llama-3.1-8b-instant",
        "ministral-14b-2512",
        "ministral-8b-2512",
        "thinkingmachines/inkling",
    }.issubset(models)
    assert models[-1] == "llama-3.3-70b-versatile"
    assert all(route.credential_count > 0 for route in routes)


def test_google_live_text_routes_are_ordered_by_capability() -> None:
    routes = configured_model_routes(_settings())
    google_models = [
        route.model for route in routes if route.credential_pool == "google"
    ]

    assert google_models == [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    ]


def test_vision_catalog_is_a_capability_subset_of_the_llm_catalog() -> None:
    chat_routes = configured_model_routes(_settings())
    vision_routes = configured_model_routes(_settings(), vision_only=True)

    assert set(vision_routes).issubset(set(chat_routes))
    assert {route.model for route in vision_routes} == {
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
        "qwen/qwen3.6-27b",
        "mistral-large-2512",
        "mistral-medium-3-5",
        "mistral-small-2603",
        "mistral-small-2506",
        "ministral-14b-2512",
        "ministral-8b-2512",
        "thinkingmachines/inkling",
    }
    assert sum(route.credential_count for route in vision_routes) == 25
    assert all(route.vision for route in vision_routes)


def test_vlm_specific_credentials_and_models_are_not_settings() -> None:
    settings = _settings()

    assert not hasattr(settings, "vlm_api_key")
    assert not hasattr(settings, "vlm_api_keys")
    assert not hasattr(settings, "vlm_model")
    assert not hasattr(settings, "vlm_fallback_models")


def test_composite_route_order_interleaves_providers() -> None:
    settings = _settings().model_copy(
        update={
            "llm_model_order": (
                "google:gemini-3.6-flash,"
                "groq:openai/gpt-oss-120b,"
                "mistral:mistral-large-2512,"
                "google:gemini-3.5-flash"
            )
        }
    )

    routes = configured_model_routes(settings)

    assert [(route.credential_pool, route.model) for route in routes[:4]] == [
        ("google", "gemini-3.6-flash"),
        ("groq", "openai/gpt-oss-120b"),
        ("mistral", "mistral-large-2512"),
        ("google", "gemini-3.5-flash"),
    ]


def test_composite_disabled_model_targets_only_one_provider_route() -> None:
    settings = _settings().model_copy(update={"disabled_llm_models": "google:gemini-3.6-flash"})

    routes = configured_model_routes(settings)

    assert not any(
        route.credential_pool == "google" and route.model == "gemini-3.6-flash" for route in routes
    )
    assert any(route.credential_pool == "groq" for route in routes)


def test_custom_openai_compatible_provider_is_loaded_without_storing_secrets(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "providers.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": [
                    {
                        "id": "openrouter",
                        "label": "OpenRouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEYS",
                        "models": [
                            {
                                "id": "anthropic/claude-sonnet",
                                "label": "Claude Sonnet via OpenRouter",
                                "capabilities": ["chat", "vision"],
                                "chat_priority": 1,
                                "vision_priority": 1,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENROUTER_API_KEYS", "paid-key,backup-key")
    settings = _settings().model_copy(update={"provider_config_path": config_path})

    chat_routes = configured_model_routes(settings)
    vision_routes = configured_model_routes(settings, vision_only=True)
    custom = chat_routes[0]

    assert custom.model == "anthropic/claude-sonnet"
    assert custom.source == "custom"
    assert custom in vision_routes
    assert route_api_keys(settings, custom) == ["paid-key", "backup-key"]
    assert custom.credential_count == 2
    assert "paid-key" not in repr(custom)
