from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from paperlens.language_models.base import ModelCapabilityError, ToolSpec
from paperlens.language_models.openai_responses import OpenAIResponsesModel


def _model(responses) -> OpenAIResponsesModel:
    model = OpenAIResponsesModel(
        model="test-model",
        api_key="test-key",
        base_url="https://example.test/v1",
    )
    model.client = SimpleNamespace(responses=responses)
    return model


def _tool() -> ToolSpec:
    return ToolSpec(
        name="demo",
        description="Demo tool",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )


def test_responses_adapter_executes_tool_and_continues_to_final_text() -> None:
    class Responses:
        def __init__(self) -> None:
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            if len(self.requests) == 1:
                call = SimpleNamespace(
                    type="function_call",
                    name="demo",
                    arguments='{"value": "x"}',
                    call_id="call-1",
                )
                return SimpleNamespace(output=[call], output_text="")
            return SimpleNamespace(output=[], output_text="completed")

    responses = Responses()

    result = _model(responses).run(
        instructions="system",
        user_input="use the tool",
        tools=[_tool()],
        execute_tool=lambda name, arguments: f"{name}:{arguments['value']}",
    )

    assert result.text == "completed"
    assert result.tool_calls[0].output == "demo:x"
    assert responses.requests[0]["parallel_tool_calls"] is False
    assert responses.requests[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "demo:x",
    }


def test_responses_lets_model_repair_malformed_nested_object() -> None:
    class Responses:
        def __init__(self) -> None:
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            round_number = len(self.requests)
            if round_number <= 2:
                filters = "{bad-json" if round_number == 1 else '{"top_k":6}'
                call = SimpleNamespace(
                    type="function_call",
                    name="search_documents",
                    arguments=json.dumps({"query": "Q", "filters": filters}),
                    call_id=f"call-{round_number}",
                )
                return SimpleNamespace(output=[call], output_text="")
            return SimpleNamespace(output=[], output_text="repaired synthesis")

    responses = Responses()
    executed = []
    spec = ToolSpec(
        name="search_documents",
        description="Search",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filters": {
                    "type": "object",
                    "properties": {"top_k": {"type": "integer"}},
                    "required": ["top_k"],
                    "additionalProperties": False,
                },
            },
            "required": ["query", "filters"],
            "additionalProperties": False,
        },
    )

    result = _model(responses).run(
        instructions="system",
        user_input="repair the call",
        tools=[spec],
        execute_tool=lambda _name, arguments: (
            executed.append(arguments) or '{"ok":true}'
        ),
    )

    assert result.text == "repaired synthesis"
    assert json.loads(result.tool_calls[0].output)["error"]["recoverable"] is True
    assert executed == [{"query": "Q", "filters": {"top_k": 6}}]
    assert len(responses.requests) == 3


def test_responses_required_planner_rejects_text_without_a_tool() -> None:
    class Responses:
        @staticmethod
        def create(**request):
            return SimpleNamespace(output=[], output_text="direct answer")

    with pytest.raises(ModelCapabilityError, match="required function call"):
        _model(Responses()).run(
            instructions="system",
            user_input="use tool",
            tools=[_tool()],
            execute_tool=lambda _name, _arguments: "{}",
            tool_choice="required",
        )


@pytest.mark.parametrize("arguments", ["{bad-json", "[1, 2]"])
def test_responses_repeated_invalid_arguments_exhaust_bounded_repair(
    arguments: str,
) -> None:
    class Responses:
        @staticmethod
        def create(**request):
            call = SimpleNamespace(
                type="function_call",
                name="demo",
                arguments=arguments,
                call_id="call-1",
            )
            return SimpleNamespace(output=[call], output_text="")

    with pytest.raises(ModelCapabilityError, match="tool-call round limit"):
        _model(Responses()).run(
            instructions="system",
            user_input="use tool",
            tools=[_tool()],
            execute_tool=lambda _name, _arguments: "{}",
        )


def test_responses_empty_text_is_a_model_capability_failure() -> None:
    class Responses:
        @staticmethod
        def create(**request):
            return SimpleNamespace(output=[], output_text="   ")

    with pytest.raises(ModelCapabilityError, match="no final text"):
        _model(Responses()).run(instructions="system", user_input="question")
