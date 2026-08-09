from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from paperlens import __version__


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Process-level resources and lifecycle for the local application."""

    project_root: Path
    web_root: Path
    outputs_root: Path
    startup: Callable[[], None]

    @asynccontextmanager
    async def lifespan(self, _app: FastAPI):
        self.outputs_root.mkdir(parents=True, exist_ok=True)
        self.startup()
        yield


def create_application(container: ApplicationContainer) -> FastAPI:
    app = FastAPI(
        title="SourceLens",
        description="Cited multilingual RAG workspace for documents, images, and audio.",
        version=__version__,
        lifespan=container.lifespan,
    )
    app.state.container = container
    return app
