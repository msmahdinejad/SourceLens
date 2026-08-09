from pathlib import Path

from fastapi.testclient import TestClient

from paperlens import webapp


def test_provider_credentials_can_be_saved_without_ever_being_returned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("GROQ_CHAT_API_KEYS", "")
    webapp.settings.cache_clear()


def test_research_and_media_credentials_sync_to_env_without_secret_echo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    for variable in ("EXA_API_KEYS", "TAVILY_API_KEYS", "PEXELS_API_KEYS", "JAMENDO_CLIENT_IDS"):
        monkeypatch.delenv(variable, raising=False)
    webapp._reset_provider_runtimes()
    client = TestClient(webapp.app)

    response = client.put(
        "/api/integrations",
        json={
            "exa_api_keys": "exa-secret-one,exa-secret-two",
            "tavily_api_keys": "tavily-secret",
            "pexels_api_keys": "pexels-secret",
            "jamendo_client_ids": "jamendo-client",
            "research_api_timeout_seconds": 44,
            "research_api_cooldown_seconds": 80,
            "research_api_minimum_gap_seconds": 1.25,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["keys_returned"] is False
    assert next(item for item in payload["integrations"] if item["id"] == "exa")[
        "configured"
    ] == 2
    assert payload["timeout_seconds"] == 44
    assert payload["cooldown_seconds"] == 80
    assert payload["minimum_gap_seconds"] == 1.25
    assert "secret" not in response.text
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EXA_API_KEYS=exa-secret-one,exa-secret-two" in env_text
    assert "TAVILY_API_KEYS=tavily-secret" in env_text
    assert "RESEARCH_API_MINIMUM_GAP_SECONDS=1.25" in env_text
    webapp._reset_provider_runtimes()


def test_custom_provider_registry_can_be_created_reordered_and_synced_to_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("PROVIDER_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    webapp.settings.cache_clear()
    client = TestClient(webapp.app)

    created = client.post(
        "/api/provider-settings",
        json={
            "id": "openrouter",
            "label": "OpenRouter",
            "adapter": "openai",
            "base_url": "https://openrouter.ai/api/v1",
            "environment_variable": "OPENROUTER_API_KEYS",
            "api_keys": "or-first,or-second",
            "capabilities": ["chat", "vision"],
            "enabled": True,
            "models": [
                {
                    "id": "qwen/qwen3-235b-a22b",
                    "label": "Qwen 3 235B",
                    "capabilities": ["chat"],
                    "enabled": True,
                },
                {
                    "id": "google/gemini-2.5-flash",
                    "label": "Gemini 2.5 Flash",
                    "capabilities": ["chat", "vision"],
                    "enabled": True,
                },
            ],
        },
    )

    assert created.status_code == 201
    assert "or-first" not in created.text
    assert created.json()["credential_count"] == 2

    reordered = client.patch(
        "/api/provider-settings",
        json={
            "provider_order": ["openrouter", "google", "groq"],
            "model_order": {
                "openrouter": [
                    "google/gemini-2.5-flash",
                    "qwen/qwen3-235b-a22b",
                ]
            },
            "runtime": {
                "llm_timeout_seconds": 75,
                "llm_pool_retry_rounds": 2,
                "llm_pool_cooldown_seconds": 90,
                "ingestion_worker_count": 3,
                "podcast_turn_timeout_seconds": 180,
                "podcast_minimum_key_gap_seconds": 12,
                "podcast_idle_refresh_seconds": 90,
            },
        },
    )
    listed = client.get("/api/provider-settings")

    assert reordered.status_code == 200
    assert listed.status_code == 200
    payload = listed.json()
    custom = next(item for item in payload["providers"] if item["id"] == "openrouter")
    assert custom["models"][0]["id"] == "google/gemini-2.5-flash"
    assert payload["providers"][0]["id"] == "openrouter"
    assert payload["runtime"]["llm_pool_retry_rounds"] == 2
    assert payload["runtime"]["podcast_turn_timeout_seconds"] == 180
    assert payload["runtime"]["podcast_minimum_key_gap_seconds"] == 12
    assert payload["runtime"]["podcast_idle_refresh_seconds"] == 90
    assert "or-first" not in listed.text

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEYS=or-first,or-second" in env_text
    assert "PROVIDER_CONFIG_PATH=providers.json" in env_text
    assert "PODCAST_TURN_TIMEOUT_SECONDS=180.0" in env_text
    assert "PODCAST_MINIMUM_KEY_GAP_SECONDS=12.0" in env_text
    assert "PODCAST_IDLE_REFRESH_SECONDS=90.0" in env_text
    provider_config = (tmp_path / "providers.json").read_text(encoding="utf-8")
    assert "openrouter" in provider_config
    assert "or-first" not in provider_config
    webapp.settings.cache_clear()


def test_models_can_be_prioritized_globally_across_provider_boundaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    webapp.settings.cache_clear()
    client = TestClient(webapp.app)
    order = [
        "google:gemini-3.6-flash",
        "groq:openai/gpt-oss-120b",
        "mistral:mistral-large-2512",
        "google:gemini-3.5-flash",
    ]

    updated = client.patch("/api/provider-settings", json={"model_order": order})

    assert updated.status_code == 200
    assert updated.json()["model_order"][:4] == order
    assert [route["route_id"] for route in updated.json()["model_routes"][:4]] == order
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"LLM_MODEL_ORDER={','.join(order)}" in env_text
    assert f"VLM_MODEL_ORDER={','.join(order)}" in env_text
    webapp.settings.cache_clear()


def test_custom_provider_can_be_updated_and_removed_without_exposing_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("PROVIDER_CONFIG_PATH", raising=False)
    webapp.settings.cache_clear()
    client = TestClient(webapp.app)

    created = client.post(
        "/api/provider-settings",
        json={
            "id": "local-proxy",
            "label": "Local proxy",
            "adapter": "openai",
            "base_url": "http://127.0.0.1:11434/v1",
            "environment_variable": "LOCAL_PROXY_API_KEYS",
            "capabilities": ["chat"],
            "enabled": True,
            "models": [{"id": "local-model", "label": "Local model"}],
        },
    )
    updated = client.put(
        "/api/provider-settings/local-proxy",
        json={
            "label": "Private inference",
            "enabled": False,
            "models": [{"id": "local-model-v2", "label": "Local model v2"}],
        },
    )
    removed = client.delete("/api/provider-settings/local-proxy")
    listed = client.get("/api/provider-settings")

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["label"] == "Private inference"
    assert updated.json()["enabled"] is False
    assert removed.status_code == 204
    assert all(item["id"] != "local-proxy" for item in listed.json()["providers"])
    webapp.settings.cache_clear()
    client = TestClient(webapp.app)

    saved = client.put(
        "/api/provider-settings/groq",
        json={"api_keys": "gsk_first, gsk_second"},
    )
    listed = client.get("/api/provider-settings")

    assert saved.status_code == 200
    assert saved.json()["credential_count"] == 2
    assert "gsk_first" not in saved.text
    assert "gsk_first" not in listed.text
    groq = next(item for item in listed.json()["providers"] if item["id"] == "groq")
    assert groq["configured"] is True
    assert groq["credential_count"] == 2
    assert "GROQ_CHAT_API_KEYS=gsk_first,gsk_second" in (tmp_path / ".env").read_text(
        encoding="utf-8"
    )
    webapp.settings.cache_clear()
