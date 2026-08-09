from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_provider: Literal["openai", "ollama"] = "openai"
    llm_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = Field(default="", repr=False)
    gemini_api_keys: str = Field(default="", repr=False)
    google_api_keys: str = Field(default="", repr=False)
    llm_fallback_model: str = ""
    llm_routing_enabled: bool = True
    llm_timeout_seconds: float = Field(default=75.0, ge=5.0, le=120.0)
    groq_chat_api_keys: str = Field(default="", repr=False)
    mistral_api_keys: str = Field(default="", repr=False)
    nvidia_api_keys: str = Field(default="", repr=False)
    provider_config_path: Path | None = Path("providers.json")
    llm_provider_order: str = ""
    llm_model_order: str = ""
    vlm_provider_order: str = ""
    vlm_model_order: str = ""
    disabled_llm_providers: str = ""
    disabled_llm_models: str = ""
    llm_pool_cooldown_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    llm_pool_retry_rounds: int = Field(default=1, ge=1, le=3)

    embedding_provider: Literal["local", "gemini", "openai"] = "local"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimensions: int = Field(default=768, ge=128, le=3072)
    embedding_api_key: str = Field(default="", repr=False)
    embedding_api_key_pool: str = Field(default="", repr=False)
    embedding_base_url: str = "https://api.openai.com/v1"
    chroma_path: Path = Path("data/chroma")
    workspaces_path: Path = Path("data/workspaces")
    papers_path: Path = Path("papers")
    outputs_path: Path = Path("outputs")
    ingestion_worker_count: int = Field(default=2, ge=1, le=8)
    seed_demo_workspace: bool = False

    retrieval_min_similarity: float = Field(default=0.78, ge=0.0, le=1.0)
    retrieval_top_k: int = Field(default=6, ge=1, le=30)
    conversation_context_budget_tokens: int = Field(default=12_000, ge=2_000, le=100_000)
    conversation_recent_turns: int = Field(default=8, ge=2, le=30)
    conversation_evidence_memory_chars: int = Field(default=32_000, ge=4_000, le=200_000)

    ocr_space_api_key: str = Field(default="", repr=False)
    ocr_space_api_keys: str = Field(default="", repr=False)
    ocr_timeout_seconds: float = Field(default=40.0, ge=5.0, le=120.0)
    ocr_retries: int = Field(default=2, ge=0, le=5)
    ocr_requests_per_minute: float = Field(default=10.0, ge=0.1, le=120.0)
    ocr_global_requests_per_minute: float = Field(default=10.0, ge=0.1, le=120.0)
    ocr_cooldown_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    local_ocr_enabled: bool = True

    vlm_enabled: bool = True
    vlm_timeout_seconds: float = Field(default=45.0, ge=5.0, le=120.0)
    vlm_requests_per_minute: float = Field(default=6.0, ge=0.1, le=120.0)
    vlm_global_requests_per_minute: float = Field(default=2.0, ge=0.1, le=120.0)
    vlm_cooldown_seconds: float = Field(default=60.0, ge=1.0, le=600.0)

    whisper_model: str = "small"
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: str = "int8"
    speech_primary_provider: Literal["groq", "local"] = "groq"
    groq_api_key: str = Field(default="", repr=False)
    groq_speech_api_keys: str = Field(default="", repr=False)
    groq_whisper_model: str = "whisper-large-v3"
    speech_requests_per_minute: float = Field(default=4.0, ge=0.1, le=120.0)
    speech_global_requests_per_minute: float = Field(default=3.0, ge=0.1, le=120.0)
    speech_cooldown_seconds: float = Field(default=60.0, ge=1.0, le=600.0)

    podcast_turn_timeout_seconds: float = Field(default=150.0, ge=30.0, le=300.0)
    podcast_minimum_key_gap_seconds: float = Field(default=8.0, ge=0.0, le=60.0)
    podcast_idle_refresh_seconds: float = Field(default=75.0, ge=30.0, le=300.0)
    piper_command: Path | None = None
    piper_en_voice_models: str = ""
    piper_fa_voice_models: str = ""
    piper_timeout_seconds: float = Field(default=120.0, ge=10.0, le=600.0)

    exa_api_keys: str = Field(default="", repr=False)
    tavily_api_keys: str = Field(default="", repr=False)
    pexels_api_keys: str = Field(default="", repr=False)
    jamendo_client_ids: str = Field(default="", repr=False)
    research_api_timeout_seconds: float = Field(default=35.0, ge=5.0, le=120.0)
    research_api_cooldown_seconds: float = Field(default=45.0, ge=1.0, le=600.0)
    research_api_minimum_gap_seconds: float = Field(default=0.75, ge=0.0, le=60.0)

    def llm_api_keys(self) -> list[str]:
        """Keys for the configured primary OpenAI-compatible endpoint."""
        if self._primary_llm_is_google():
            return self.google_llm_keys()
        return _unique_keys(self.llm_api_key)

    def google_llm_keys(self) -> list[str]:
        """Google-only keys; never mix an OpenAI credential into Gemini calls."""
        primary = self.llm_api_key if self._primary_llm_is_google() else ""
        return _unique_keys(primary, self.gemini_api_keys, self.google_api_keys)

    def groq_chat_keys(self) -> list[str]:
        return _unique_keys(self.groq_chat_api_keys)

    def mistral_keys(self) -> list[str]:
        return _unique_keys(self.mistral_api_keys)

    def nvidia_keys(self) -> list[str]:
        return _unique_keys(self.nvidia_api_keys)

    def ordered_llm_providers(self) -> list[str]:
        return _unique_keys(self.llm_provider_order)

    def ordered_llm_models(self) -> list[str]:
        return _unique_keys(self.llm_model_order)

    def ordered_vlm_providers(self) -> list[str]:
        return _unique_keys(self.vlm_provider_order)

    def ordered_vlm_models(self) -> list[str]:
        return _unique_keys(self.vlm_model_order)

    def disabled_model_providers(self) -> set[str]:
        return set(_unique_keys(self.disabled_llm_providers))

    def disabled_models(self) -> set[str]:
        return set(_unique_keys(self.disabled_llm_models))

    def groq_speech_keys(self) -> list[str]:
        return _unique_keys(
            self.groq_api_key,
            self.groq_speech_api_keys,
            self.groq_chat_api_keys,
        )

    def piper_voice_models(self, language: Literal["en", "fa"]) -> list[Path]:
        configured = (
            self.piper_fa_voice_models if language == "fa" else self.piper_en_voice_models
        )
        return [self.resolve_path(Path(value)) for value in _unique_paths(configured)]

    def piper_command_path(self) -> Path | None:
        return self.resolve_path(self.piper_command) if self.piper_command else None

    def has_llm_credentials(self) -> bool:
        return bool(
            self.llm_api_keys()
            or self.google_llm_keys()
            or self.groq_chat_keys()
            or self.mistral_keys()
            or self.nvidia_keys()
        )

    def embedding_api_keys(self) -> list[str]:
        provider_keys = self.google_llm_keys() if self.embedding_provider == "gemini" else []
        return _unique_keys(
            self.embedding_api_key,
            self.embedding_api_key_pool,
            *provider_keys,
        )

    def ocr_space_keys(self) -> list[str]:
        return _unique_keys(self.ocr_space_api_key, self.ocr_space_api_keys)

    def exa_keys(self) -> list[str]:
        return _unique_keys(self.exa_api_keys)

    def tavily_keys(self) -> list[str]:
        return _unique_keys(self.tavily_api_keys)

    def pexels_keys(self) -> list[str]:
        return _unique_keys(self.pexels_api_keys)

    def jamendo_ids(self) -> list[str]:
        return _unique_keys(self.jamendo_client_ids)

    def _primary_llm_is_google(self) -> bool:
        return "generativelanguage.googleapis.com" in self.llm_base_url.casefold()

    def resolve_path(self, path: Path, project_root: Path | None = None) -> Path:
        if path.is_absolute():
            return path
        root = project_root or Path.cwd()
        return (root / path).resolve()


def _unique_keys(*values: str) -> list[str]:
    keys: list[str] = []
    for value in values:
        for key in value.replace("\n", ",").split(","):
            normalized = key.strip()
            if normalized and normalized not in keys:
                keys.append(normalized)
    return keys


def _unique_paths(value: str) -> list[str]:
    return _unique_keys(value.replace(";", ","))
