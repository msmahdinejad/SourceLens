from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

ProcessingStatus = Literal["uploaded", "processing", "ready", "failed"]
ExtractionMethod = Literal["text", "ocr", "vlm", "hybrid", "transcription"]
SourceType = Literal["pdf", "text", "image", "audio"]

_EXTENSION_SOURCE = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "text",
    ".csv": "text",
    ".json": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".wav": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".mp4": "audio",
    ".webm": "audio",
}
_PERSISTENCE_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ProcessingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(default=0, ge=0)
    timestamp: str = Field(default_factory=utc_now)
    stage: str
    status: Literal["queued", "running", "completed", "skipped", "failed"]
    message: str
    page_number: int | None = Field(default=None, ge=1)
    total_pages: int | None = Field(default=None, ge=1)
    extraction_method: ExtractionMethod | None = None
    provider: str | None = None
    text_preview: str = ""
    visual_preview: str = ""
    requests: list[dict[str, object]] = Field(default_factory=list)


class ProcessingProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = "queued"
    percent: int = Field(default=0, ge=0, le=100)
    processed_units: int = Field(default=0, ge=0)
    total_units: int = Field(default=0, ge=0)
    current_unit: int | None = Field(default=None, ge=1)
    ocr_requests: int = Field(default=0, ge=0)
    vlm_requests: int = Field(default=0, ge=0)
    transcription_requests: int = Field(default=0, ge=0)
    extraction_counts: dict[str, int] = Field(default_factory=dict)
    provider_failovers: int = Field(default=0, ge=0)
    rate_wait_ms: int = Field(default=0, ge=0)
    started_at: str | None = None
    finished_at: str | None = None
    events: list[ProcessingEvent] = Field(default_factory=list)


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    original_filename: str
    source_type: SourceType = "pdf"
    media_type: str = "application/pdf"
    title: str | None = None
    page_count: int = Field(default=0, ge=0)
    extraction_methods: dict[str, ExtractionMethod] = Field(default_factory=dict)
    language_hints: list[str] = Field(default_factory=list)
    chunk_count: int = Field(default=0, ge=0)
    embedding_model: str | None = None
    vector_collection_id: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    status: ProcessingStatus = "uploaded"
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    progress: ProcessingProgress = Field(default_factory=lambda: ProcessingProgress())
    source_path: str
    origin_url: str | None = None
    origin_provider: str | None = None
    author: str | None = None
    published_at: str | None = None


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    name: str
    is_demo: bool = False
    documents: list[DocumentRecord] = Field(default_factory=list)
    embedding_model: str | None = None
    vector_collection_id: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    last_rebuild_at: str | None = None


class WorkspaceStore:
    """JSON-backed workspace persistence with uploaded sources beside metadata."""

    def __init__(self, root: Path, *, papers_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.papers_path = papers_path.resolve() if papers_path else None
        # Multiple cached services can construct store instances for the same
        # root during cold-start. A process-wide lock keeps Windows replaces
        # atomic even when those instances initialize concurrently.
        self._lock = _PERSISTENCE_LOCK
        self.root.mkdir(parents=True, exist_ok=True)

    def ensure_workspace(self, workspace_id: str, name: str) -> WorkspaceRecord:
        try:
            return self.get(workspace_id)
        except KeyError:
            workspace = WorkspaceRecord(
                workspace_id=workspace_id,
                name=name.strip()[:120],
            )
            self.save(workspace)
            return workspace

    def ensure_demo_workspace(self) -> WorkspaceRecord:
        """Compatibility alias; new code installs the optional workspace fixture."""
        if self.papers_path is None:
            return self.ensure_workspace("demo", "Charge While You Shop")
        from paperlens.workspace_fixtures import install_demo_fixture

        return install_demo_fixture(self, self.papers_path)

    def ensure_default_workspace(self) -> WorkspaceRecord:
        """Create the empty first-run workspace without importing any source data."""
        return self.ensure_workspace("default", "My research")

    def create_workspace(self, name: str) -> WorkspaceRecord:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Workspace name cannot be empty")
        workspace = WorkspaceRecord(
            workspace_id=f"ws-{uuid4().hex[:12]}",
            name=normalized[:120],
        )
        self.save(workspace)
        return workspace

    def list(self) -> list[WorkspaceRecord]:
        records = []
        with self._lock:
            for path in self.root.glob("*/workspace.json"):
                try:
                    records.append(WorkspaceRecord.model_validate_json(path.read_text("utf-8")))
                except (OSError, ValueError):
                    continue
        return sorted(records, key=lambda item: item.created_at)

    def get(self, workspace_id: str) -> WorkspaceRecord:
        with self._lock:
            path = self._workspace_file(workspace_id)
            if not path.is_file():
                raise KeyError(f"Unknown workspace: {workspace_id}")
            return WorkspaceRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, workspace: WorkspaceRecord) -> WorkspaceRecord:
        with self._lock:
            workspace = workspace.model_copy(update={"updated_at": utc_now()})
            path = self._workspace_file(workspace.workspace_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            temporary.write_text(
                json.dumps(
                    workspace.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(path)
            return workspace

    def add_document(
        self,
        workspace_id: str,
        content: bytes,
        original_filename: str,
        content_type: str | None = None,
    ) -> DocumentRecord:
        if len(content) > 100 * 1024 * 1024:
            raise ValueError("File exceeds the 100 MB upload limit")
        source_type, media_type, extension = self._detect_source(content)
        supplied_extension = Path(original_filename).suffix.casefold()
        claimed_source = _EXTENSION_SOURCE.get(supplied_extension)
        declared_media = (content_type or "").partition(";")[0].strip().casefold()
        if claimed_source is not None and claimed_source != source_type:
            raise ValueError("File content does not match its filename extension")
        declared_source = (
            "pdf"
            if declared_media == "application/pdf"
            else "text"
            if declared_media == "application/json" or declared_media.startswith("text/")
            else "image"
            if declared_media.startswith("image/")
            else "audio"
            if declared_media.startswith(("audio/", "video/"))
            else None
        )
        if declared_source is not None and declared_source != source_type:
            raise ValueError("File content does not match its declared media type")
        with self._lock:
            workspace = self.get(workspace_id)
            filename = self._safe_filename(original_filename, extension)
            document_id = f"doc-{uuid4().hex[:12]}"
            relative = Path(workspace_id) / "documents" / document_id / filename
            target = (self.root / relative).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            document = DocumentRecord(
                document_id=document_id,
                original_filename=filename,
                source_type=source_type,
                media_type=media_type,
                source_path=str(target),
            )
            self.save(
                workspace.model_copy(
                    update={"documents": [*workspace.documents, document]},
                )
            )
            return document

    def update_document(
        self,
        workspace_id: str,
        document_id: str,
        **changes: object,
    ) -> DocumentRecord:
        with self._lock:
            workspace = self.get(workspace_id)
            documents = []
            updated: DocumentRecord | None = None
            for document in workspace.documents:
                if document.document_id == document_id:
                    updated = document.model_copy(
                        update={**changes, "updated_at": utc_now()},
                    )
                    documents.append(updated)
                else:
                    documents.append(document)
            if updated is None:
                raise KeyError(f"Unknown document: {document_id}")
            self.save(workspace.model_copy(update={"documents": documents}))
            return updated

    def document(self, workspace_id: str, document_id: str) -> DocumentRecord:
        workspace = self.get(workspace_id)
        try:
            return next(item for item in workspace.documents if item.document_id == document_id)
        except StopIteration as error:
            raise KeyError(f"Unknown document: {document_id}") from error

    def record_progress(
        self,
        workspace_id: str,
        document_id: str,
        event: ProcessingEvent,
        *,
        percent: int | None = None,
        processed_units: int | None = None,
        total_units: int | None = None,
        current_unit: int | None = None,
        finished: bool = False,
    ) -> DocumentRecord:
        with self._lock:
            document = self.document(workspace_id, document_id)
            previous = document.progress
            sequenced = event.model_copy(
                update={"sequence": (previous.events[-1].sequence + 1 if previous.events else 1)}
            )
            attempts = sequenced.requests
            failover_used = int(
                len(attempts) > 1
                and any(item.get("status") == "failed" for item in attempts[:-1])
                and attempts[-1].get("status") == "completed"
            )
            wait_ms = sum(int(item.get("wait_ms") or 0) for item in attempts)
            request_count = len(attempts) or int(
                sequenced.stage == "transcription" and sequenced.status == "completed"
            )
            updates: dict[str, object] = {
                "stage": sequenced.stage,
                "events": [*previous.events, sequenced],
                "provider_failovers": previous.provider_failovers + failover_used,
                "rate_wait_ms": previous.rate_wait_ms + wait_ms,
                "started_at": previous.started_at or utc_now(),
                "finished_at": utc_now() if finished else previous.finished_at,
            }
            if (
                sequenced.stage == "page"
                and sequenced.status == "completed"
                and sequenced.extraction_method
            ):
                counts = dict(previous.extraction_counts)
                method = sequenced.extraction_method
                counts[method] = counts.get(method, 0) + 1
                updates["extraction_counts"] = counts
            if sequenced.stage == "ocr":
                updates["ocr_requests"] = previous.ocr_requests + request_count
            if sequenced.stage == "vlm":
                updates["vlm_requests"] = previous.vlm_requests + request_count
            if sequenced.stage == "transcription":
                updates["transcription_requests"] = previous.transcription_requests + request_count
            if percent is not None:
                updates["percent"] = max(0, min(100, percent))
            if processed_units is not None:
                updates["processed_units"] = processed_units
            if total_units is not None:
                updates["total_units"] = total_units
            if current_unit is not None:
                updates["current_unit"] = current_unit
            return self.update_document(
                workspace_id,
                document_id,
                progress=previous.model_copy(update=updates),
            )

    def document_path(self, workspace_id: str, document_id: str) -> Path:
        path = Path(self.document(workspace_id, document_id).source_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def delete_document(self, workspace_id: str, document_id: str) -> DocumentRecord:
        """Remove one document and its derived artifacts from a workspace."""
        with self._lock:
            workspace = self.get(workspace_id)
            document = self.document(workspace_id, document_id)
            document_dir = (
                self._workspace_dir(workspace_id) / "documents" / document.document_id
            ).resolve()
            workspace_dir = self._workspace_dir(workspace_id)
            if workspace_dir not in document_dir.parents:
                raise ValueError("Document path escapes workspace storage")
            if document_dir.exists():
                shutil.rmtree(document_dir)
            self.save(
                workspace.model_copy(
                    update={
                        "documents": [
                            item
                            for item in workspace.documents
                            if item.document_id != document_id
                        ]
                    }
                )
            )
            return document

    def delete_workspace(self, workspace_id: str) -> WorkspaceRecord:
        """Remove an exact workspace directory after validating its storage boundary."""
        with self._lock:
            workspace = self.get(workspace_id)
            workspace_dir = self._workspace_dir(workspace_id)
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir)
            return workspace

    def extraction_path(self, workspace_id: str, document_id: str) -> Path:
        return self._workspace_dir(workspace_id) / "documents" / document_id / "extraction.json"

    def _workspace_file(self, workspace_id: str) -> Path:
        return self._workspace_dir(workspace_id) / "workspace.json"

    def _workspace_dir(self, workspace_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", workspace_id):
            raise ValueError("Invalid workspace id")
        path = (self.root / workspace_id).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Workspace path escapes storage root")
        return path

    @staticmethod
    def _safe_filename(value: str, extension: str = ".pdf") -> str:
        name = Path(value or f"document{extension}").name
        stem = re.sub(r"[^A-Za-z0-9._\-\u0600-\u06ff ]+", "_", name).strip(" .")
        base = Path(stem).stem[:160] or "document"
        return f"{base}{extension}"

    @staticmethod
    def _detect_source(content: bytes) -> tuple[SourceType, str, str]:
        if not content:
            raise ValueError("Uploaded file is empty")
        if content.startswith(b"%PDF-"):
            return "pdf", "application/pdf", ".pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image", "image/png", ".png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image", "image/jpeg", ".jpg"
        if content[:4] in {b"II*\x00", b"MM\x00*"}:
            return "image", "image/tiff", ".tiff"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image", "image/webp", ".webp"
        if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
            return "audio", "audio/wav", ".wav"
        if content.startswith(b"OggS"):
            return "audio", "audio/ogg", ".ogg"
        if content.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
            return "audio", "audio/mpeg", ".mp3"
        if content.startswith(b"\x1aE\xdf\xa3"):
            return "audio", "audio/webm", ".webm"
        if len(content) >= 12 and content[4:8] == b"ftyp":
            return "audio", "audio/mp4", ".m4a"
        for encoding in ("utf-8-sig", "utf-16"):
            try:
                text = content.decode(encoding)
            except UnicodeError:
                continue
            if not text.strip():
                continue
            controls = sum(ord(character) < 32 and character not in "\n\r\t" for character in text)
            if controls / max(1, len(text)) <= 0.01:
                return "text", "text/plain", ".txt"
        raise ValueError("Unsupported or unrecognized file format")
