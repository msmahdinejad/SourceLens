from __future__ import annotations

import base64
import json
import re
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from paperlens.provider_failover import ProviderPool


class VLMResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    visual_summary: str = ""
    recognized_text: str = ""
    tables: list[dict[str, Any]] = Field(default_factory=list)
    figures: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: str
    warnings: list[str] = Field(default_factory=list)
    requests: list[dict[str, Any]] = Field(default_factory=list)


class VLMAdapter(Protocol):
    available: bool
    label: str

    def analyze(self, image: bytes, *, page_number: int) -> VLMResult: ...


class OpenAICompatibleVLM:
    """Vision adapter for the already configured OpenAI-compatible provider."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        label: str | None = None,
        timeout_seconds: float = 45.0,
        client: Any | None = None,
    ) -> None:
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.base_url = base_url
        self.available = bool(self.model and self.api_key)
        self.label = label or self.model or "VLM not configured"
        self._client = client
        self.timeout_seconds = timeout_seconds

    def analyze(self, image: bytes, *, page_number: int) -> VLMResult:
        if not self.available:
            raise RuntimeError("No VLM-capable model is configured")
        client = self._client or OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=0,
        )
        data_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Analyze one PDF page for evidence retrieval. Return strict JSON with "
                        "recognized_text, visual_summary, tables, figures, confidence. "
                        "Transcribe readable text faithfully. Describe charts/tables, "
                        "labels, axes and legends; architecture components and connections; "
                        "visible equations or algorithmic content; and a concise semantic "
                        "summary. Never infer details that are not visible."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Analyze physical PDF page {page_number}."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            max_tokens=1600,
        )
        raw = (response.choices[0].message.content or "").strip()
        payload = self._json_payload(raw)
        return VLMResult(
            recognized_text=str(payload.get("recognized_text", "")).strip(),
            visual_summary=str(payload.get("visual_summary", "")).strip(),
            tables=self._records(payload.get("tables")),
            figures=self._records(payload.get("figures")),
            confidence=self._confidence(payload.get("confidence")),
            provider=self.label,
        )

    @staticmethod
    def _json_payload(raw: str) -> dict[str, Any]:
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return {"visual_summary": raw, "confidence": 0.6}
        return payload if isinstance(payload, dict) else {"visual_summary": raw}

    @staticmethod
    def _records(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, str) and value.strip().casefold() not in {"", "none", "null"}:
            return [{"description": value.strip()}]
        return []

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.75
        return max(0.0, min(1.0, confidence))


class PooledVLM:
    """Rate-paced vision pool shared by concurrent document jobs."""

    label = "VLM provider pool"

    def __init__(
        self,
        adapters: list[VLMAdapter],
        *,
        requests_per_minute: float = 6.0,
        global_requests_per_minute: float = 4.0,
        cooldown_seconds: float = 60.0,
    ) -> None:
        resources = [adapter for adapter in adapters if adapter.available]
        self._resources = tuple(resources)
        self.available = bool(resources)
        self._pool = (
            ProviderPool(
                resources,
                provider="VLM",
                cooldown_seconds=cooldown_seconds,
                minimum_interval_seconds=60 / max(0.1, requests_per_minute),
                global_minimum_interval_seconds=(60 / max(0.1, global_requests_per_minute)),
                retry_rounds=2,
                wait_for_cooldown=True,
                restore_priority_after_cooldown=True,
                max_wait_seconds=max(3600.0, cooldown_seconds * 2),
            )
            if resources
            else None
        )

    def analyze(self, image: bytes, *, page_number: int) -> VLMResult:
        if self._pool is None:
            raise RuntimeError("No VLM provider is configured")
        result = self._pool.run(
            lambda adapter, _index: adapter.analyze(
                image,
                page_number=page_number,
            )
        )
        status = self._pool.status()
        return result.model_copy(
            update={"requests": self._decorate_attempts(status["last_attempts"])}
        )

    def status(self) -> dict[str, object]:
        if self._pool is None:
            return {"available": False, "credential_count": 0, "routes": []}
        status = self._pool.status()
        status["last_attempts"] = self._decorate_attempts(status["last_attempts"])
        routes: list[dict[str, object]] = []
        for adapter in self._resources:
            existing = next(
                (item for item in routes if item["label"] == adapter.label),
                None,
            )
            if existing is None:
                routes.append({"label": adapter.label, "credential_count": 1})
            else:
                existing["credential_count"] = int(existing["credential_count"]) + 1
        return {"available": True, **status, "routes": routes}

    def _decorate_attempts(
        self,
        attempts: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        decorated = []
        for attempt in attempts:
            item = dict(attempt)
            slot = int(item.get("slot") or 0) - 1
            if 0 <= slot < len(self._resources):
                item["model"] = self._resources[slot].label
            decorated.append(item)
        return decorated
