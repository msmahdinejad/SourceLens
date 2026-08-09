import json
from types import SimpleNamespace

import pytest

from paperlens.language_models.base import ModelCapabilityError, ToolSpec
from paperlens.language_models.openai_chat import OpenAIChatCompletionsModel
from paperlens.tools.registry import ResearchTools


class FakeMessage:
    def __init__(self, *, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = True) -> dict:
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": self.tool_calls,
        }


class FakeCompletions:
    def __init__(self) -> None:
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        if len(self.requests) == 1:
            function = SimpleNamespace(name="demo", arguments='{"value": "x"}')
            call = SimpleNamespace(id="call-1", function=function)
            message = FakeMessage(tool_calls=[call])
        else:
            message = FakeMessage(content="completed")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_chat_completions_json_mode_requests_a_json_object() -> None:
    class JsonCompletions:
        def __init__(self) -> None:
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content='{"slides": []}'))]
            )

    completions = JsonCompletions()
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = model.run_json(instructions="JSON only", user_input="compose")

    assert result.text == '{"slides": []}'
    assert completions.requests[0]["response_format"] == {"type": "json_object"}


def test_chat_completions_json_mode_honors_a_smaller_request_budget() -> None:
    class JsonCompletions:
        def __init__(self) -> None:
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content='{"slides": []}'))]
            )

    completions = JsonCompletions()
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        max_output_tokens=10_000,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    model.run_json(
        instructions="JSON only",
        user_input="compose",
        max_output_tokens=3_000,
    )

    assert completions.requests[0]["max_tokens"] == 3_000


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("  completed  ", "completed"),
        (
            [
                {"type": "text", "text": " first block "},
                {"type": "text", "text": "second block"},
            ],
            "first block\nsecond block",
        ),
        (
            [
                SimpleNamespace(type="text", text="known object block"),
                {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                {"type": "text", "text": "known mapping block"},
                {"type": "unknown", "text": "must not leak"},
                None,
            ],
            "known object block\nknown mapping block",
        ),
    ],
)
def test_chat_completions_normalizes_provider_content_dialects(
    content: object,
    expected: str,
) -> None:
    class DialectCompletions:
        def create(self, **_request):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content=content))]
            )

    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=DialectCompletions())
        ),
    )

    result = model.run(instructions="system", user_input="question")

    assert result.text == expected


@pytest.mark.parametrize(
    "content",
    [
        None,
        [],
        [{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}}],
        [{"type": "text", "text": {"value": "not a string"}}],
    ],
)
def test_chat_completions_rejects_content_without_text_as_capability_failure(
    content: object,
) -> None:
    class NonTextCompletions:
        def create(self, **_request):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content=content))]
            )

    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=NonTextCompletions())
        ),
    )

    with pytest.raises(ModelCapabilityError, match="no final text"):
        model.run(instructions="system", user_input="question")


def test_chat_completions_executes_tool_and_returns_result() -> None:
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=client,
    )
    spec = ToolSpec(
        name="demo",
        description="Demo tool",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )

    result = model.run(
        instructions="system",
        user_input="use the tool",
        tools=[spec],
        execute_tool=lambda name, arguments: f"{name}:{arguments['value']}",
    )

    assert result.text == "completed"
    assert result.tool_calls[0].name == "demo"
    assert completions.requests[0]["tools"][0]["function"]["name"] == "demo"
    assert completions.requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "demo:x",
    }


def test_chat_completions_normalizes_nested_object_arguments() -> None:
    class NestedObjectCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            if len(self.requests) == 1:
                function = SimpleNamespace(
                    name="search_documents",
                    arguments=(
                        '{"query":"Q","filters":"{\\"document_ids\\":null,'
                        '\\"section_types\\":null,\\"chunk_types\\":null,'
                        '\\"page_start\\":null,\\"page_end\\":null,\\"top_k\\":6}"}'
                    ),
                )
                call = SimpleNamespace(id="call-1", function=function)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=FakeMessage(tool_calls=[call]))]
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content="synthesized"))]
            )

    filters_seen = []
    spec = next(
        spec for spec in ResearchTools.document_specs() if spec.name == "search_documents"
    )
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=NestedObjectCompletions())
        ),
    )

    result = model.run(
        instructions="system",
        user_input="search",
        tools=[spec],
        execute_tool=lambda _name, arguments: (
            filters_seen.append(arguments["filters"]) or "{}"
        ),
    )

    assert result.text == "synthesized"
    assert filters_seen == [
        {
            "document_ids": None,
            "section_types": None,
            "chunk_types": None,
            "page_start": None,
            "page_end": None,
            "top_k": 6,
        }
    ]


def test_chat_completions_loop_can_execute_multiple_sequential_tools() -> None:
    class TwoToolCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            round_number = len(self.requests)
            if round_number <= 2:
                function = SimpleNamespace(
                    name=f"tool_{round_number}",
                    arguments='{"value": "x"}',
                )
                call = SimpleNamespace(id=f"call-{round_number}", function=function)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=FakeMessage(tool_calls=[call]))]
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content="synthesized"))]
            )

    completions = TwoToolCompletions()
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    specs = [
        ToolSpec(
            name=name,
            description=name,
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        for name in ("tool_1", "tool_2")
    ]

    result = model.run(
        instructions="system",
        user_input="use both tools",
        tools=specs,
        execute_tool=lambda name, arguments: f"{name}:{arguments['value']}",
        tool_choice="auto",
        return_after_tool=False,
    )

    assert result.text == "synthesized"
    assert [call.name for call in result.tool_calls] == ["tool_1", "tool_2"]
    assert len(completions.requests) == 3


def test_chat_completions_lets_model_repair_malformed_tool_json() -> None:
    class RepairingCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            round_number = len(self.requests)
            if round_number <= 2:
                arguments = "{bad-json" if round_number == 1 else '{"value":"fixed"}'
                function = SimpleNamespace(name="demo", arguments=arguments)
                call = SimpleNamespace(id=f"call-{round_number}", function=function)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=FakeMessage(tool_calls=[call]))]
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content="repaired synthesis"))]
            )

    completions = RepairingCompletions()
    executed = []
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    spec = ToolSpec(
        name="demo",
        description="Demo",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )

    result = model.run(
        instructions="system",
        user_input="repair the call",
        tools=[spec],
        execute_tool=lambda _name, arguments: (
            executed.append(arguments) or '{"ok":true}'
        ),
    )

    error = json.loads(result.tool_calls[0].output)
    assert result.text == "repaired synthesis"
    assert error == {
        "ok": False,
        "error": {
            "type": "invalid_tool_arguments",
            "message": "Tool arguments must be valid JSON",
            "recoverable": True,
        },
    }
    assert executed == [{"value": "fixed"}]
    assert len(completions.requests) == 3


def test_required_planner_accepts_null_args_and_returns_immediately_after_tool() -> None:
    class NullArgumentCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            function = SimpleNamespace(name="demo", arguments="null")
            call = SimpleNamespace(id="call-1", function=function)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(tool_calls=[call]))]
            )

    completions = NullArgumentCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=client,
    )
    spec = ToolSpec(
        name="demo",
        description="No-argument demo tool",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )

    result = model.run(
        instructions="system",
        user_input="use the tool",
        tools=[spec],
        execute_tool=lambda name, arguments: f"{name}:{arguments}",
        tool_choice="required",
        return_after_tool=True,
    )

    assert result.text == ""
    assert result.tool_calls[0].arguments == {}
    assert result.tool_calls[0].output == "demo:{}"
    assert completions.requests[0]["tool_choice"] == "required"
    assert len(completions.requests) == 1


def test_required_planner_rejects_a_text_answer_without_a_tool_call() -> None:
    class TextOnlyCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(content="direct answer"))]
            )

    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=TextOnlyCompletions())),
    )
    spec = ToolSpec(
        name="demo",
        description="Demo",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )

    with pytest.raises(ModelCapabilityError, match="required function call"):
        model.run(
            instructions="system",
            user_input="use the tool",
            tools=[spec],
            execute_tool=lambda _name, _arguments: "{}",
            tool_choice="required",
            return_after_tool=True,
        )


@pytest.mark.parametrize(
    "arguments",
    ["{not-json", "[1, 2, 3]"],
)
def test_repeated_invalid_generated_tool_arguments_exhaust_bounded_repair(
    arguments: str,
) -> None:
    class InvalidArgumentsCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            function = SimpleNamespace(name="demo", arguments=arguments)
            call = SimpleNamespace(id="call-1", function=function)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage(tool_calls=[call]))]
            )

    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=InvalidArgumentsCompletions())),
    )
    spec = ToolSpec(
        name="demo",
        description="Demo",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )

    with pytest.raises(ModelCapabilityError, match="tool-call round limit"):
        model.run(
            instructions="system",
            user_input="use the tool",
            tools=[spec],
            execute_tool=lambda _name, _arguments: "{}",
            tool_choice="required",
        )


@pytest.mark.parametrize("failure", ["no_choices", "empty_text"])
def test_empty_chat_completion_is_a_model_capability_failure(failure: str) -> None:
    class EmptyCompletions(FakeCompletions):
        def create(self, **request):
            self.requests.append(request)
            if failure == "no_choices":
                return SimpleNamespace(choices=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=FakeMessage(content="   "))])

    model = OpenAIChatCompletionsModel(
        model="test",
        api_key="test",
        base_url="https://example.test/v1/",
        client=SimpleNamespace(chat=SimpleNamespace(completions=EmptyCompletions())),
    )

    with pytest.raises(ModelCapabilityError, match="no (completion choices|final text)"):
        model.run(instructions="system", user_input="question")
