from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from paperlens.config import Settings


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_console()
app = typer.Typer(
    name="paperlens",
    help="SourceLens: a cited multilingual research workspace for documents and media.",
    no_args_is_help=True,
)
console = Console()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_corpus(workspace_id: str = "default"):
    from paperlens.knowledge_base import WorkspaceCorpus
    from paperlens.runtime import create_knowledge_base, create_workspace_store

    root = project_root()
    settings = Settings()
    store = create_workspace_store(settings, root)
    return WorkspaceCorpus(create_knowledge_base(settings, root, store=store), workspace_id)


@app.command()
def ingest(
    source: Annotated[
        Path | None,
        typer.Argument(
            help="Supported file or folder. Use `paperlens demo` for the optional sample corpus."
        ),
    ] = None,
    paper_id: Annotated[
        list[str] | None,
        typer.Option("--paper", help="Ingest only this paper id; may be repeated."),
    ] = None,
    workspace_id: Annotated[
        str | None,
        typer.Option("--workspace", help="Existing workspace id for uploaded documents."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Name for a newly created workspace."),
    ] = None,
) -> None:
    """Extract, enrich, transcribe, chunk, embed, and persist supported files."""
    if source is not None:
        from paperlens.runtime import create_knowledge_base, create_workspace_store

        root = project_root()
        current = Settings()
        store = create_workspace_store(current, root)
        if workspace_id:
            workspace = store.get(workspace_id)
        else:
            workspace = store.create_workspace(name or source.stem or "Uploaded documents")
        supported_extensions = {
            ".pdf",
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".tif",
            ".tiff",
            ".wav",
            ".mp3",
            ".ogg",
            ".m4a",
            ".mp4",
            ".webm",
        }
        candidates = (
            [source]
            if source.is_file()
            else sorted(
                path
                for path in source.iterdir()
                if path.is_file() and path.suffix.casefold() in supported_extensions
            )
            if source.is_dir()
            else []
        )
        if not candidates:
            console.print(f"[red]No supported files found at {source}.[/red]")
            raise typer.Exit(code=1)
        knowledge_base = create_knowledge_base(current, root, store=store)
        table = Table(title=f"Ingestion · {workspace.name}")
        table.add_column("Document")
        table.add_column("Pages", justify="right")
        table.add_column("Chunks", justify="right")
        table.add_column("Methods")
        for path in candidates:
            document = store.add_document(
                workspace.workspace_id,
                path.read_bytes(),
                path.name,
            )
            result = knowledge_base.process_document(
                workspace.workspace_id,
                document.document_id,
            )
            table.add_row(
                path.name,
                str(result.page_count),
                str(len(result.chunks)),
                ", ".join(
                    f"{method}:{count}" for method, count in result.extraction_distribution.items()
                ),
            )
        console.print(table)
        console.print(f"[green]Workspace:[/green] {workspace.workspace_id}")
        return

    console.print("[yellow]Provide a file or folder, or run `paperlens demo`.[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def demo() -> None:
    """Download, verify, and index the optional Charge While You Shop demo."""
    from paperlens.runtime import (
        create_knowledge_base,
        create_workspace_store,
        seed_demo_workspace,
    )

    root = project_root()
    current = Settings()
    store = create_workspace_store(current, root)
    seed_demo_workspace(current, root, store)
    report = create_knowledge_base(current, root, store=store).rebuild("demo")
    console.print_json(data=report)
    console.print("[green]The Charge While You Shop demo is ready in workspace `demo`.[/green]")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Research question or search query.")],
    top_k: Annotated[int, typer.Option("--top-k", min=1, max=20)] = 6,
    workspace_id: Annotated[str, typer.Option("--workspace")] = "default",
) -> None:
    """Run semantic retrieval and show citations and similarity scores."""
    corpus = build_corpus(workspace_id)
    evidence = corpus.search(query, top_k=top_k)
    if not evidence:
        console.print(
            "[yellow]The vector database is empty. Run `paperlens ingest` first.[/yellow]"
        )
        raise typer.Exit(code=1)
    for rank, item in enumerate(evidence, start=1):
        console.rule(
            f"{rank}. {item.paper_id} | {item.section} | "
            f"p. {item.page_start} | similarity={item.similarity:.3f}"
        )
        console.print(item.text)


@app.command()
def evaluate(
    questions: Annotated[Path, typer.Argument(help="Versioned evaluation question set.")],
    workspace_id: Annotated[str, typer.Option("--workspace")] = "default",
) -> None:
    """Evaluate retrieval quality and calibrate the sufficiency threshold."""
    from paperlens.evaluation import evaluate_retrieval

    root = project_root()
    report = evaluate_retrieval(
        build_corpus(workspace_id),
        questions,
        root / "outputs",
    )
    table = Table(title="Retrieval evaluation")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Hit@1", f"{report.hit_at_1:.1%}")
    table.add_row("Hit@6", f"{report.hit_at_k:.1%}")
    table.add_row("MRR", f"{report.mean_reciprocal_rank:.3f}")
    table.add_row("Recommended threshold", f"{report.recommended_threshold:.3f}")
    table.add_row("Gate sensitivity", f"{report.gate_metrics.sensitivity:.1%}")
    table.add_row("Gate specificity", f"{report.gate_metrics.specificity:.1%}")
    console.print(table)
    console.print(f"[green]Saved report:[/green] {report.markdown_path}")


@app.command("eval")
def evaluate_alias(
    questions: Annotated[Path, typer.Argument(help="Versioned evaluation question set.")],
    workspace_id: Annotated[str, typer.Option("--workspace")] = "default",
) -> None:
    """Alias for `paperlens evaluate`."""
    evaluate(questions, workspace_id)


@app.command("benchmark-retrieval")
def benchmark_retrieval(
    fixture: Annotated[
        Path,
        typer.Option("--fixture", help="Directory containing documents.json and questions.json."),
    ] = Path("fixtures/retrieval_wikidata"),
    output: Annotated[
        Path,
        typer.Option("--output", help="Directory for the measured benchmark report."),
    ] = Path("outputs"),
    repetitions: Annotated[int, typer.Option(min=1, max=20)] = 3,
    scale_chunks: Annotated[int, typer.Option("--scale-chunks", min=100)] = 10_000,
) -> None:
    """Compare deterministic vector, legacy scan, and SQLite FTS retrieval."""
    from paperlens.evaluation import run_general_benchmark

    report = run_general_benchmark(
        fixture,
        output,
        repetitions=repetitions,
        scale_chunk_count=scale_chunks,
    )
    table = Table(title="General retrieval benchmark")
    table.add_column("Strategy")
    table.add_column("Recall@6", justify="right")
    table.add_column("Citation precision", justify="right")
    table.add_column("Abstention", justify="right")
    table.add_column("p95 ms", justify="right")
    for result in report.results:
        table.add_row(
            result.strategy,
            f"{result.recall_at_6:.1%}",
            f"{result.citation_precision:.1%}",
            f"{result.abstention_accuracy:.1%}",
            f"{result.p95_latency_ms:.2f}",
        )
    console.print(table)
    console.print(
        f"Tool validity {report.agent.initial_tool_call_validity:.1%} -> "
        f"{report.agent.repaired_tool_call_validity:.1%}; multi-search contract "
        f"{report.agent.multi_search_contract_success:.1%}."
    )
    console.print(
        f"{report.scaling.chunk_count:,}-chunk p95: "
        f"{report.scaling.p95_latency_ms:.2f} ms."
    )
    console.print(report.graph_gate_reason)
    console.print(f"[green]Saved report:[/green] {report.markdown_path}")
    if not report.after.passes_thresholds:
        raise typer.Exit(code=1)


@app.command("provider-smoke")
def provider_smoke(
    live: Annotated[
        bool,
        typer.Option("--live", help="Allow the command to call configured remote providers."),
    ] = False,
) -> None:
    """Probe configured provider families without displaying credentials."""
    if not live:
        console.print("[yellow]Refusing network calls without explicit --live opt-in.[/yellow]")
        raise typer.Exit(code=2)

    from paperlens.provider_smoke import run_live_provider_smoke

    results = run_live_provider_smoke(Settings())
    table = Table(title="Live provider smoke")
    for column in ("Provider", "Model", "Slot", "Text", "Tool", "Detail"):
        table.add_column(column)
    for result in results:
        table.add_row(
            result.provider,
            result.model,
            str(result.slot),
            result.text_status,
            result.tool_status,
            result.detail,
        )
    console.print(table)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question for the research assistant.")],
    workspace_id: Annotated[
        str,
        typer.Option(
            "--workspace",
            help="Workspace id; defaults to the empty first-run workspace.",
        ),
    ] = "default",
    document_id: Annotated[
        list[str] | None,
        typer.Option("--document", help="Restrict to a document id; may be repeated."),
    ] = None,
) -> None:
    """Answer with RAG, automatic function calling, citations, and fallback."""
    settings = Settings()
    try:
        from paperlens.runtime import create_workspace_assistant

        assistant = create_workspace_assistant(
            settings,
            project_root(),
            workspace_id,
            set(document_id or []),
        )
    except ValueError as error:
        console.print(f"[red]Configuration error:[/red] {error}")
        console.print("Copy .env.example to .env and configure the LLM provider.")
        raise typer.Exit(code=2) from error

    answer = assistant.ask(question)
    mode = "RAG" if answer.mode == "rag" else "GENERAL FALLBACK"
    console.rule(mode)
    console.print(answer.text)
    if answer.tool_calls:
        console.print(
            "[cyan]Called tools:[/cyan] " + ", ".join(call.name for call in answer.tool_calls)
        )


@app.command()
def rebuild(
    workspace_id: Annotated[
        str,
        typer.Option("--workspace", help="Workspace id; defaults to the first-run workspace."),
    ] = "default",
) -> None:
    """Build a fresh versioned vector collection for a workspace."""
    from paperlens.runtime import create_knowledge_base, create_workspace_store

    current = Settings()
    root = project_root()
    store = create_workspace_store(current, root)
    report = create_knowledge_base(current, root, store=store).rebuild(workspace_id)
    console.print_json(data=report)


@app.command()
def chat() -> None:
    """Start an interactive terminal research chat."""
    from paperlens.runtime import create_workspace_assistant

    settings = Settings()
    try:
        assistant = create_workspace_assistant(settings, project_root(), "default")
    except ValueError as error:
        console.print(f"[red]Configuration error:[/red] {error}")
        raise typer.Exit(code=2) from error

    console.print("SourceLens is ready. Type [bold]exit[/bold] to stop.")
    while True:
        question = console.input("\n[bold cyan]Ask your question:[/bold cyan] ").strip()
        if question.casefold() in {"exit", "quit"}:
            break
        if not question:
            continue
        try:
            answer = assistant.ask(question)
            console.print(answer.text)
        except Exception as error:  # noqa: BLE001
            console.print(f"[red]Request failed:[/red] {error}")


@app.command()
def web(
    host: Annotated[str, typer.Option(help="Host address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload when source files change.")] = False,
) -> None:
    """Launch the SourceLens web interface."""
    import uvicorn

    uvicorn.run(
        "paperlens.webapp:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
