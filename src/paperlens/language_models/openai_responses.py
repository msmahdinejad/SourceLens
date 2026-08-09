from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

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


class OpenAIResponsesModel:
    """Responses API adapter with a bounded, non-stateful function-calling loop."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        compatibility_mode: Literal["openai", "ollama"] = "openai",
        max_tool_rounds: int = 4,
        max_output_tokens: int = 3000,
        timeout_seconds: float = 35.0,
    ) -> None:
        if not model.strip():
            raise ValueError("LLM_MODEL is not configured")
        if not api_key.strip():
            raise ValueError("LLM_API_KEY is not configured")
        self.model = model
        self.compatibility_mode = compatibility_mode
        self.max_tool_rounds = max_tool_rounds
        self.max_output_tokens = max_output_tokens
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=2,
        )

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
        if tools and execute_tool is None:
            raise ValueError("execute_tool is required when tools are provided")

        input_items: list[Any] = [{"role": "user", "content": user_input}]
        executed: list[ExecutedToolCall] = []
        tool_schemas = [tool.responses_schema() for tool in tools]
        specs_by_name = {tool.name: tool for tool in tools}

        for round_number in range(self.max_tool_rounds + 1):
            request: dict[str, Any] = {
                "model": self.model,
                "instructions": instructions,
                "input": input_items,
                "max_output_tokens": self.max_output_tokens,
            }
            if self.compatibility_mode == "openai":
                request["store"] = False
            if tool_schemas:
                request["tools"] = tool_schemas
                if self.compatibility_mode == "openai":
                    request.update(
                        {
                            "tool_choice": tool_choice if not executed else "auto",
                            "parallel_tool_calls": False,
                        }
                    )
            response = self.client.responses.create(**request)
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                if tool_schemas and tool_choice == "required" and not executed:
                    raise ModelCapabilityError(
                        "The model did not return the required function call"
                    )
                text = response.output_text.strip()
                if not text:
                    raise ModelCapabilityError("The language model returned no final text")
                incomplete = getattr(response, "incomplete_details", None)
                finish_reason = (
                    getattr(incomplete, "reason", None)
                    if incomplete is not None
                    else getattr(response, "status", None)
                )
                return GenerationResult(
                    text=text,
                    tool_calls=executed,
                    finish_reason=finish_reason,
                )

            if round_number >= self.max_tool_rounds:
                raise ModelCapabilityError("The language model exceeded the tool-call round limit")
            input_items.extend(response.output)
            calls_valid = True
            for call in function_calls:
                executed_call, call_valid = execute_generated_tool(
                    call.name,
                    call.arguments,
                    specs_by_name,
                    execute_tool,  # type: ignore[arg-type]
                )
                calls_valid = calls_valid and call_valid
                executed.append(executed_call)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": executed_call.output,
                    }
                )
            if return_after_tool and calls_valid:
                return GenerationResult(text="", tool_calls=executed)

        raise ModelCapabilityError("Unreachable tool loop state")
