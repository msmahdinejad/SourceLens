from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from paperlens.config import Settings
from paperlens.language_models.base import ToolSpec
from paperlens.language_models.openai_chat import OpenAIChatCompletionsModel
from paperlens.model_routes import (
    ModelRoute,
    configured_model_routes,
    route_api_keys,
)


@dataclass(frozen=True, slots=True)
class ProviderSmokeResult:
    provider: str
    model: str
    slot: int
    text_status: str
    tool_status: str
    detail: str = ""


ModelFactory = Callable[[ModelRoute, str], Any]


def run_live_provider_smoke(
    settings: Settings,
    *,
    model_factory: ModelFactory | None = None,
) -> list[ProviderSmokeResult]:
    """Probe the strongest enabled route in each configured provider family."""
    factory = model_factory or _model_factory(settings)
    routes: list[ModelRoute] = []
    families: set[str] = set()
    for route in configured_model_routes(settings):
        if route.credential_pool not in families:
            families.add(route.credential_pool)
            routes.append(route)

    results = []
    for route in routes:
        keys = route_api_keys(settings, route)
        if not keys:
            continue
        model = factory(route, keys[0])
        text_status, text_detail = _probe(
            lambda current=model: _require_text(
                current.run(
                    instructions="Return a short health acknowledgement.",
                    user_input="Reply with SOURCELENS_SMOKE_OK.",
                )
            ),
            keys,
        )
        tool_status, tool_detail = _probe(
            lambda current=model: _require_tool(current), keys
        )
        details = [
            f"text: {text_detail}" if text_detail else "",
            f"tool: {tool_detail}" if tool_detail else "",
        ]
        detail = "; ".join(item for item in details if item)
        results.append(
            ProviderSmokeResult(
                provider=route.provider,
                model=route.model,
                slot=1,
                text_status=text_status,
                tool_status=tool_status,
                detail=detail,
            )
        )
    return results


def _model_factory(settings: Settings) -> ModelFactory:
    def create(route: ModelRoute, key: str) -> OpenAIChatCompletionsModel:
        return OpenAIChatCompletionsModel(
            model=route.model,
            api_key=key,
            base_url=route.base_url,
            max_output_tokens=512,
            max_retries=0,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    return create


def _require_text(result) -> None:
    if not result.text.strip():
        raise RuntimeError("provider returned no text")


def _require_tool(model) -> None:
    tool = ToolSpec(
        name="sourcelens_smoke",
        description="Return one focused search subject for the smoke test.",
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": ["subject-a", "subject-b"]}
            },
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    result = model.run(
        instructions=(
            "Plan two focused searches. Call sourcelens_smoke for both distinct values "
            "subject-a and subject-b in this response."
        ),
        user_input="Perform both required smoke search calls.",
        tools=[tool],
        execute_tool=lambda _name, _arguments: '{"ok":true}',
        tool_choice="required",
        return_after_tool=True,
    )
    values = {
        call.arguments.get("value")
        for call in result.tool_calls
        if call.name == "sourcelens_smoke"
    }
    if values != {"subject-a", "subject-b"}:
        raise RuntimeError("provider did not plan two distinct tool calls")


def _probe(call: Callable[[], None], secrets: list[str]) -> tuple[str, str]:
    try:
        call()
    except Exception as error:  # noqa: BLE001 - live provider boundary
        detail = _safe_error(error, secrets)
        return _classify_error(detail), detail
    return "pass", ""


def _classify_error(detail: str) -> str:
    lowered = detail.casefold()
    if any(marker in lowered for marker in ("429", "quota", "resource_exhausted")):
        return "quota-blocked"
    if any(marker in lowered for marker in ("401", "403", "unauthorized", "forbidden")):
        return "auth-blocked"
    if any(marker in lowered for marker in ("404", "not found", "unavailable model")):
        return "unavailable"
    if any(marker in lowered for marker in ("timeout", "connect", "network", "dns")):
        return "network-blocked"
    return "failed"


def _safe_error(error: Exception, secrets: list[str]) -> str:
    detail = f"{type(error).__name__}: {error}"
    for secret in secrets:
        detail = detail.replace(secret, "[REDACTED]")
    detail = re.sub(
        r"(?i)(authorization|api[-_ ]?key)(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]",
        detail,
    )
    return " ".join(detail.split())[:240]
