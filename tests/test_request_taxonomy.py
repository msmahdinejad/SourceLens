from paperlens.tools.registry import ResearchTools


def test_all_research_functions_are_exposed_to_the_llm() -> None:
    assert {spec.name for spec in ResearchTools.specs()} == {
        "extract_experimental_results",
        "compare_results",
        "generate_survey",
        "find_research_gap",
        "compare_references",
        "compare_limitations",
    }


def test_tool_schemas_are_closed_and_self_describing() -> None:
    for spec in ResearchTools.specs():
        assert spec.description.strip()
        assert spec.parameters["type"] == "object"
        assert spec.parameters["additionalProperties"] is False
