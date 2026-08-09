from __future__ import annotations

from click import unstyle
from typer.testing import CliRunner

from paperlens.cli import app

runner = CliRunner()


def test_cli_help_lists_the_required_project_workflows() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "ingest",
        "search",
        "evaluate",
        "benchmark-retrieval",
        "provider-smoke",
        "eval",
        "ask",
        "rebuild",
        "demo",
        "chat",
        "web",
    ):
        assert command in result.stdout


def test_cli_demo_help_names_the_charge_while_you_shop_fixture() -> None:
    result = runner.invoke(app, ["demo", "--help"])

    assert result.exit_code == 0
    assert "Charge While You Shop" in unstyle(result.stdout)


def test_cli_evaluation_requires_an_explicit_fixture_and_workspace() -> None:
    result = runner.invoke(app, ["evaluate", "--help"])
    help_text = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "questions" in help_text
    assert "--workspace" in help_text


def test_cli_retrieval_benchmark_exposes_fixture_and_output_paths() -> None:
    result = runner.invoke(app, ["benchmark-retrieval", "--help"])
    help_text = unstyle(result.stdout)

    assert result.exit_code == 0
    assert "--fixture" in help_text
    assert "--output" in help_text


def test_provider_smoke_requires_explicit_live_opt_in() -> None:
    result = runner.invoke(app, ["provider-smoke"])

    assert result.exit_code == 2
    assert "--live" in unstyle(result.stdout)
