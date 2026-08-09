from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=180)
    label: str = Field(min_length=1, max_length=180)
    capabilities: set[Literal["chat", "vision", "tools"]] = Field(
        default_factory=lambda: {"chat", "tools"}
    )
    chat_priority: int = Field(default=1000, ge=1, le=10_000)
    vision_priority: int = Field(default=1000, ge=1, le=10_000)
    enabled: bool = True

    @field_validator("capabilities")
    @classmethod
    def require_chat(cls, value: set[str]) -> set[str]:
        if "chat" not in value:
            raise ValueError("Every configured model must support chat completions")
        return value


class ProviderEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    adapter: Literal["openai"] = "openai"
    base_url: str
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,80}$")
    models: list[ProviderModelConfig] = Field(min_length=1)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not re.match(r"^https?://", value, flags=re.IGNORECASE):
            raise ValueError("Provider base_url must be an HTTP(S) URL")
        return value.rstrip("/")


class ProviderCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    providers: list[ProviderEndpointConfig] = Field(default_factory=list)


def load_provider_catalog(path: Path | None) -> ProviderCatalogConfig:
    """Load secret-free provider metadata; API keys remain environment-only."""
    if path is None or not path.is_file():
        return ProviderCatalogConfig(version=1)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid provider configuration JSON: {path}") from error
    return ProviderCatalogConfig.model_validate(payload)


def save_provider_catalog(path: Path, catalog: ProviderCatalogConfig) -> None:
    """Atomically persist secret-free provider metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(catalog.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
