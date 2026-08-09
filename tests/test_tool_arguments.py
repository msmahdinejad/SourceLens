from __future__ import annotations

import json

import pytest

from paperlens.language_models.base import ToolSpec, execute_generated_tool


def _search_spec() -> ToolSpec:
    return ToolSpec(
        name="search_documents",
        description="Search",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "filters": {
                    "type": ["object", "null"],
                    "properties": {
                        "document_ids": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                        },
                        "top_k": {"type": ["integer", "null"], "minimum": 1},
                    },
                    "required": ["document_ids", "top_k"],
                    "additionalProperties": False,
                },
            },
            "required": ["query", "filters"],
            "additionalProperties": False,
        },
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"query": "Q", "filters": {"document_ids": None, "top_k": {}}}, "integer"),
        ({"query": "Q", "filters": {"document_ids": [1], "top_k": 6}}, "string"),
        ({"query": [], "filters": {"document_ids": None, "top_k": 6}}, "string"),
        ({"query": "Q", "filters": {"document_ids": None}}, "Missing required"),
        (
            {"query": "Q", "filters": {"document_ids": None, "top_k": 6, "extra": 1}},
            "Unexpected",
        ),
    ],
)
def test_generated_tool_rejects_nested_schema_violations_as_recoverable(
    arguments: dict,
    message: str,
) -> None:
    executed = []
    call, valid = execute_generated_tool(
        "search_documents",
        json.dumps(arguments),
        {"search_documents": _search_spec()},
        lambda _name, value: executed.append(value) or "{}",
    )

    assert valid is False
    assert executed == []
    assert json.loads(call.output)["error"]["recoverable"] is True
    assert message in json.loads(call.output)["error"]["message"]


def test_generated_tool_normalizes_nested_objects_before_validation() -> None:
    executed = []
    filters = json.dumps({"document_ids": ["doc-1"], "top_k": 6})
    call, valid = execute_generated_tool(
        "search_documents",
        json.dumps({"query": "Q", "filters": filters}),
        {"search_documents": _search_spec()},
        lambda _name, value: executed.append(value) or "{}",
    )

    assert valid is True
    assert call.arguments["filters"]["top_k"] == 6
    assert executed == [call.arguments]
