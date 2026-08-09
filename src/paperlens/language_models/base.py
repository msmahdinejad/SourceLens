from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]

    def responses_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "strict": True,
            "parameters": self.parameters,
        }

    def chat_completions_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": self.parameters,
            },
        }


class ModelCapabilityError(RuntimeError):
    """The current model did not honor a required response capability."""


class ToolArgumentError(ValueError):
    """A model supplied arguments that violate a public tool schema."""


def normalize_tool_arguments(
    arguments: dict[str, Any],
    spec: ToolSpec,
) -> dict[str, Any]:
    """Decode provider-stringified object fields at the shared tool boundary."""
    return _normalize_schema_value(arguments, spec.parameters, spec.name)


def _normalize_schema_value(value: Any, schema: dict[str, Any], path: str) -> Any:
    allowed = schema.get("type", [])
    allowed = [allowed] if isinstance(allowed, str) else allowed
    if not allowed:
        return _validate_enum(value, schema, path)
    if value is None:
        if "null" in allowed:
            return None
        raise ToolArgumentError(f"{path} must be {_type_label(allowed)}")
    if "object" in allowed and isinstance(value, str) and "string" not in allowed:
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ToolArgumentError(f"{path} must be a valid JSON object") from error
    if isinstance(value, dict) and "object" in allowed:
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ToolArgumentError(
                f"Missing required field(s) at {path}: {', '.join(missing)}"
            )
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                raise ToolArgumentError(
                    f"Unexpected field(s) at {path}: {', '.join(unexpected)}"
                )
        normalized = {
            key: _normalize_schema_value(item, properties.get(key, {}), f"{path}.{key}")
            for key, item in value.items()
        }
        return _validate_enum(normalized, schema, path)
    if isinstance(value, list) and "array" in allowed:
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise ToolArgumentError(f"{path} must contain at least {minimum} item(s)")
        if maximum is not None and len(value) > maximum:
            raise ToolArgumentError(f"{path} must contain at most {maximum} item(s)")
        items = schema.get("items", {})
        normalized = [
            _normalize_schema_value(item, items, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
        return _validate_enum(normalized, schema, path)
    if isinstance(value, bool) and "boolean" in allowed:
        return _validate_enum(value, schema, path)
    if isinstance(value, int) and not isinstance(value, bool) and "integer" in allowed:
        _validate_number_bounds(value, schema, path)
        return _validate_enum(value, schema, path)
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and "number" in allowed
    ):
        _validate_number_bounds(value, schema, path)
        return _validate_enum(value, schema, path)
    if isinstance(value, str) and "string" in allowed:
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise ToolArgumentError(f"{path} must contain at least {minimum} character(s)")
        if maximum is not None and len(value) > maximum:
            raise ToolArgumentError(f"{path} must contain at most {maximum} character(s)")
        return _validate_enum(value, schema, path)
    raise ToolArgumentError(f"{path} must be {_type_label(allowed)}")


def _validate_number_bounds(value: int | float, schema: dict[str, Any], path: str) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and value < minimum:
        raise ToolArgumentError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ToolArgumentError(f"{path} must be at most {maximum}")


def _validate_enum(value: Any, schema: dict[str, Any], path: str) -> Any:
    choices = schema.get("enum")
    if choices is not None and value not in choices:
        raise ToolArgumentError(f"{path} must be one of: {', '.join(map(str, choices))}")
    return value


def _type_label(allowed: list[str]) -> str:
    labels = [item for item in allowed if item != "null"]
    return " or ".join(labels or allowed)


class ExecutedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Any]
    output: str


def execute_generated_tool(
    name: str,
    raw_arguments: Any,
    specs: dict[str, ToolSpec],
    execute_tool: ToolExecutor,
) -> tuple[ExecutedToolCall, bool]:
    """Validate one untrusted provider call and return a repairable result on errors."""
    try:
        arguments = json.loads(raw_arguments) or {}
    except (TypeError, json.JSONDecodeError):
        return _invalid_tool_call(name, {}, "Tool arguments must be valid JSON")
    if not isinstance(arguments, dict):
        return _invalid_tool_call(name, {}, "Tool arguments must be a JSON object")
    spec = specs.get(name)
    if spec is None:
        return _invalid_tool_call(name, arguments, "Unknown tool")
    try:
        arguments = normalize_tool_arguments(arguments, spec)
        output = execute_tool(name, arguments)
    except ToolArgumentError as error:
        return _invalid_tool_call(name, arguments, str(error))
    return ExecutedToolCall(name=name, arguments=arguments, output=output), True


def _invalid_tool_call(
    name: str,
    arguments: dict[str, Any],
    message: str,
) -> tuple[ExecutedToolCall, bool]:
    output = json.dumps(
        {
            "ok": False,
            "error": {
                "type": "invalid_tool_arguments",
                "message": message,
                "recoverable": True,
            },
        },
        ensure_ascii=False,
    )
    return ExecutedToolCall(name=name, arguments=arguments, output=output), False


class GenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    tool_calls: list[ExecutedToolCall] = Field(default_factory=list)
    finish_reason: str | None = None


ToolExecutor = Callable[[str, dict[str, Any]], str]
ToolChoice = Literal["auto", "required"]


class LanguageModel(Protocol):
    def run(
        self,
        *,
        instructions: str,
        user_input: str,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult: ...
