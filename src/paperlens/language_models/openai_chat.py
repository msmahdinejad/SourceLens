from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from openai import OpenAI

from paperlens.language_models.base import (
    ExecutedToolCall,
    GenerationResult,
    ModelCapabilityError,
    ToolChoice,
    ToolExecutor,
    ToolSpec,
    execute_generated_tool,
)


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, Sequence):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            block_text = block.get("text")
        else:
            block_type = getattr(block, "type", None)
            block_text = getattr(block, "text", None)
        if (
            block_type == "text"
            and isinstance(block_text, str)
            and (text := block_text.strip())
        ):
            parts.append(text)
    return "\n".join(parts)


class OpenAIChatCompletionsModel:
    """Chat Completions adapter for Gemini's OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        max_tool_rounds: int = 4,
        max_output_tokens: int = 3000,
        max_retries: int = 2,
        timeout_seconds: float = 35.0,
        client: Any | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("LLM_MODEL is not configured")
        if not api_key.strip():
            raise ValueError("LLM_API_KEY is not configured")
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        self.max_output_tokens = max_output_tokens
        self._client = client
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._client_lock = threading.Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = OpenAI(
                        api_key=self._api_key,
                        base_url=self._base_url,
                        timeout=self._timeout_seconds,
                        max_retries=self._max_retries,
                    )
        return self._client

    def run(
        self,
        *,
        instructions: str,
        user_input: str,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        return self._run(
            instructions=instructions,
            user_input=user_input,
            tools=tools,
            execute_tool=execute_tool,
            tool_choice=tool_choice,
            return_after_tool=return_after_tool,
            json_mode=False,
        )

    def run_json(
        self,
        *,
        instructions: str,
        user_input: str,
        max_output_tokens: int | None = None,
        tools: Sequence[ToolSpec] = (),
        execute_tool: ToolExecutor | None = None,
        tool_choice: ToolChoice = "auto",
        return_after_tool: bool = False,
    ) -> GenerationResult:
        """Request provider-enforced JSON for structured Studio artifacts."""
        return self._run(
            instructions=instructions,
            user_input=user_input,
            tools=tools,
            execute_tool=execute_tool,
            tool_choice=tool_choice,
            return_after_tool=return_after_tool,
            json_mode=True,
            max_output_tokens=max_output_tokens,
        )

    def _run(
        self,
        *,
        instructions: str,
        user_input: str,
        tools: Sequence[ToolSpec],
        execute_tool: ToolExecutor | None,
        tool_choice: ToolChoice,
        return_after_tool: bool,
        json_mode: bool,
        max_output_tokens: int | None = None,
    ) -> GenerationResult:
        if tools and execute_tool is None:
            raise ValueError("execute_tool is required when tools are provided")

        messages: list[Any] = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ]
        executed: list[ExecutedToolCall] = []
        tool_schemas = [tool.chat_completions_schema() for tool in tools]
        specs_by_name = {tool.name: tool for tool in tools}

        for round_number in range(self.max_tool_rounds + 1):
            request: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "max_tokens": (
                    self.max_output_tokens
                    if max_output_tokens is None
                    else max(1, min(self.max_output_tokens, int(max_output_tokens)))
                ),
            }
            if json_mode:
                request["response_format"] = {"type": "json_object"}
            if tool_schemas:
                request["tools"] = tool_schemas
                request["tool_choice"] = tool_choice if not executed else "auto"

            response = self.client.chat.completions.create(**request)
            if not response.choices:
                raise ModelCapabilityError("The language model returned no completion choices")
            message = response.choices[0].message
            function_calls = list(message.tool_calls or [])

            if not function_calls:
                if tool_schemas and tool_choice == "required" and not executed:
                    raise ModelCapabilityError(
                        "The model did not return the required function call"
                    )
                text = _normalize_message_content(message.content)
                if not text:
                    raise ModelCapabilityError("The language model returned no final text")
                return GenerationResult(
                    text=text,
                    tool_calls=executed,
                    finish_reason=getattr(response.choices[0], "finish_reason", None),
                )

            if round_number >= self.max_tool_rounds:
                raise ModelCapabilityError("The language model exceeded the tool-call round limit")
            messages.append(message.model_dump(exclude_none=True))
            calls_valid = True
            for call in function_calls:
                executed_call, call_valid = execute_generated_tool(
                    call.function.name,
                    call.function.arguments,
                    specs_by_name,
                    execute_tool,  # type: ignore[arg-type]
                )
                calls_valid = calls_valid and call_valid
                executed.append(executed_call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": executed_call.output,
                    }
                )
            if return_after_tool and calls_valid:
                return GenerationResult(text="", tool_calls=executed)

        raise ModelCapabilityError("Unreachable tool loop state")
