import pytest

from paperlens.tools.registry import ResearchTools


def test_all_tool_schemas_are_strict_and_closed() -> None:
    schemas = [spec.responses_schema() for spec in ResearchTools.specs()]

    assert {schema["name"] for schema in schemas} == {
        "extract_experimental_results",
        "compare_results",
        "generate_survey",
        "find_research_gap",
        "compare_references",
        "compare_limitations",
    }
    for schema in schemas:
        assert schema["strict"] is True
        parameters = schema["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["properties"]) == set(parameters["required"])


def test_chat_completion_tool_schemas_are_strict() -> None:
    schemas = [spec.chat_completions_schema() for spec in ResearchTools.specs()]

    assert all(schema["function"]["strict"] is True for schema in schemas)


def test_runtime_tool_arguments_enforce_closed_schema() -> None:
    ResearchTools.validate_arguments("compare_results", {})
    ResearchTools.validate_arguments("generate_survey", {"language": None})

    with pytest.raises(ValueError, match="Unexpected arguments"):
        ResearchTools.validate_arguments("compare_results", {"language": "fa"})
    with pytest.raises(ValueError, match="Missing required arguments"):
        ResearchTools.validate_arguments("generate_survey", {})
    with pytest.raises(ValueError, match="Unsupported value"):
        ResearchTools.validate_arguments("generate_survey", {"language": "de"})
