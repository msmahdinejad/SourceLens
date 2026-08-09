from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VISUAL_ENGINE_CONTRACT_VERSION = "paperlens.visual-engine.v1"
VisualArtifactType = Literal["slide_deck", "video_overview"]
_WINDOWS_NO_WINDOW = 0x08000000
_WINDOWS_NEW_PROCESS_GROUP = 0x00000200


class VisualEngineUnavailable(RuntimeError):
    """Raised before execution so callers can safely use the Python renderer."""


class VisualEngineError(RuntimeError):
    """Raised when an available external renderer fails its versioned contract."""

    def __init__(self, message: str, *, events: tuple[VisualEngineEvent, ...] = ()) -> None:
        super().__init__(message)
        self.events = events


class VisualEngineCancelled(VisualEngineError):
    """Raised after cooperative cancellation terminates the renderer process group."""


class VisualEngineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["paperlens.visual-engine.v1"] = VISUAL_ENGINE_CONTRACT_VERSION
    artifact_type: VisualArtifactType
    artifact_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    language: str = Field(default="auto", min_length=1, max_length=16)
    plan: dict[str, Any]
    options: dict[str, Any]


class VisualEngineDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=40)
    compatibility_aliases: list[str] = Field(default_factory=list)


class VisualEngineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["paperlens.visual-engine.v1"]
    status: Literal["ready"]
    engine: VisualEngineDescriptor
    outputs: dict[str, str]
    previews: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class VisualEngineEvent:
    type: Literal["progress", "diagnostic"]
    stage: str
    progress: int
    message: str
    details: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class VisualEngineAvailability:
    available: bool
    artifact_type: VisualArtifactType
    entrypoint: Path | None
    node_path: Path | None
    node_version: str | None
    ffmpeg_path: Path | None
    reason: str = ""


@dataclass(frozen=True)
class VisualEngineExecution:
    contract_version: str
    engine: VisualEngineDescriptor
    outputs: dict[str, str]
    previews: tuple[str, ...]
    diagnostics: tuple[str, ...]
    metadata: dict[str, Any]
    warnings: tuple[str, ...]
    events: tuple[VisualEngineEvent, ...]
    manifest_path: Path

    def artifact_data(self) -> dict[str, Any]:
        return {
            **self.metadata,
            **self.outputs,
            "visual_engine": self.engine.model_dump(mode="json", exclude_defaults=True),
            "visual_engine_contract": self.contract_version,
            "manifest_filename": self.manifest_path.name,
            "preview_filenames": list(self.previews),
            "diagnostic_filenames": list(self.diagnostics),
            "render_events": [event.payload() for event in self.events],
            "render_warnings": list(self.warnings),
        }


class VisualEngineBridge:
    """Run optional Node renderers behind one durable, secret-safe artifact contract."""

    def __init__(
        self,
        *,
        slide_entrypoint: Path | None = None,
        video_entrypoint: Path | None = None,
        node_command: str | Path = "node",
        ffmpeg_command: str | Path = "ffmpeg",
        timeout_seconds: float = 3600,
    ) -> None:
        package_root = Path(__file__).resolve().parent
        self.entrypoints = {
            "slide_deck": slide_entrypoint
            or _environment_path("SOURCELENS_DECK_RENDERER_ENTRYPOINT")
            or _environment_path("SOURCELENS_SLIDE_ENGINE_ENTRYPOINT")
            or package_root / "renderers" / "deck_engine" / "entrypoint.mjs",
            "video_overview": video_entrypoint
            or _environment_path("SOURCELENS_VIDEO_RENDERER_ENTRYPOINT")
            or _environment_path("SOURCELENS_VIDEO_ENGINE_ENTRYPOINT")
            or package_root / "renderers" / "video_engine" / "entrypoint.mjs",
        }
        self.node_command = node_command
        self.ffmpeg_command = ffmpeg_command
        self.timeout_seconds = max(10.0, float(timeout_seconds))
        self._availability: dict[VisualArtifactType, VisualEngineAvailability] = {}

    def available(self, artifact_type: VisualArtifactType) -> VisualEngineAvailability:
        cached = self._availability.get(artifact_type)
        if cached is not None:
            return cached
        entrypoint = Path(self.entrypoints[artifact_type]).resolve()
        node = _executable(self.node_command)
        ffmpeg = _executable(self.ffmpeg_command)
        node_version = _node_version(node) if node else None
        reason = ""
        if not entrypoint.is_file():
            reason = f"Visual engine entrypoint is missing: {entrypoint}"
        elif node is None:
            reason = "Node.js is not installed or is not on PATH"
        elif node_version is None or int(node_version.split(".", 1)[0]) < 20:
            reason = f"Node.js 20 or newer is required; detected {node_version or 'unknown'}"
        elif artifact_type == "video_overview" and ffmpeg is None:
            reason = "FFmpeg is required by the video engine"
        availability = VisualEngineAvailability(
            available=not reason,
            artifact_type=artifact_type,
            entrypoint=entrypoint if entrypoint.is_file() else None,
            node_path=node,
            node_version=node_version,
            ffmpeg_path=ffmpeg,
            reason=reason,
        )
        self._availability[artifact_type] = availability
        return availability

    def render(
        self,
        *,
        artifact_type: VisualArtifactType,
        artifact_id: str,
        workspace_id: str,
        language: str,
        plan: Mapping[str, Any],
        options: Mapping[str, Any],
        output_dir: Path,
        on_event: Callable[[VisualEngineEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> VisualEngineExecution:
        availability = self.available(artifact_type)
        if not availability.available or availability.entrypoint is None:
            raise VisualEngineUnavailable(availability.reason)
        request = VisualEngineRequest(
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            language=language,
            plan=dict(plan),
            options=dict(options),
        )
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".visual-engine-", dir=output_dir))
        request_path = staging / "request.json"
        result_path = staging / "result.json"
        stderr_path = staging / "stderr.log"
        events: list[VisualEngineEvent] = []
        secrets = tuple(value for value in (environment or {}).values() if value)
        try:
            request_path.write_text(
                request.model_dump_json(indent=2),
                encoding="utf-8",
            )
            command = [
                str(availability.node_path),
                str(availability.entrypoint),
                "--request",
                str(request_path),
                "--output",
                str(staging),
                "--result",
                str(result_path),
            ]
            self._execute(
                command,
                stderr_path,
                events,
                on_event,
                should_cancel,
                environment,
                secrets,
            )
            if not result_path.is_file():
                raise VisualEngineError(
                    "Visual engine completed without a result contract",
                    events=tuple(events),
                )
            try:
                response = VisualEngineResponse.model_validate_json(
                    result_path.read_text(encoding="utf-8")
                )
            except ValueError as error:
                raise VisualEngineError(
                    f"Visual engine returned an invalid result contract: {error}",
                    events=tuple(events),
                ) from error
            self._validate_primary_output(artifact_type, response)
            assets = self._publish_assets(response, staging, output_dir)
            manifest_path = self._publish_manifest(
                request=request,
                response=response,
                assets=assets,
                events=events,
                output_dir=output_dir,
            )
            return VisualEngineExecution(
                contract_version=response.contract_version,
                engine=response.engine,
                outputs=dict(response.outputs),
                previews=tuple(response.previews),
                diagnostics=tuple(response.diagnostics),
                metadata=dict(response.metadata),
                warnings=tuple(response.warnings),
                events=tuple(events),
                manifest_path=manifest_path,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _execute(
        self,
        command: list[str],
        stderr_path: Path,
        events: list[VisualEngineEvent],
        on_event: Callable[[VisualEngineEvent], None] | None,
        should_cancel: Callable[[], bool] | None,
        environment: Mapping[str, str] | None,
        secrets: tuple[str, ...],
    ) -> None:
        process_env = os.environ.copy()
        process_env.update(environment or {})
        creation_flags = (
            _WINDOWS_NO_WINDOW | _WINDOWS_NEW_PROCESS_GROUP if os.name == "nt" else 0
        )
        with stderr_path.open("w", encoding="utf-8") as stderr_stream:
            process = subprocess.Popen(  # noqa: S603 - entrypoint and executable are trusted paths
                command,
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=process_env,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
            assert process.stdout is not None
            lines: queue.Queue[str | None] = queue.Queue()

            def read_stdout() -> None:
                for line in process.stdout:
                    lines.put(line)
                lines.put(None)

            reader = threading.Thread(target=read_stdout, daemon=True)
            reader.start()
            try:
                deadline = time.monotonic() + self.timeout_seconds
                finished = False
                while not finished:
                    if should_cancel is not None and should_cancel():
                        _terminate_process_group(process)
                        raise VisualEngineCancelled(
                            "Visual engine cancelled",
                            events=tuple(events),
                        )
                    if time.monotonic() >= deadline:
                        _terminate_process_group(process)
                        raise VisualEngineError(
                            f"Visual engine timed out after {self.timeout_seconds:.0f} seconds",
                            events=tuple(events),
                        )
                    try:
                        line = lines.get(timeout=0.1)
                    except queue.Empty:
                        if process.poll() is not None and not reader.is_alive():
                            break
                        continue
                    if line is None:
                        finished = True
                        continue
                    event = _event(line, events[-1].progress if events else 0)
                    if event is None or len(events) >= 500:
                        continue
                    events.append(event)
                    if on_event is not None:
                        with suppress(Exception):
                            on_event(event)
                return_code = process.wait(timeout=10)
            finally:
                if process.poll() is None:
                    _terminate_process_group(process)
                reader.join(timeout=10)
                process.stdout.close()
        if return_code:
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")[-12_000:]
            stderr = _redact(stderr, secrets)
            raise VisualEngineError(
                f"Visual engine failed with exit code {return_code}: {stderr.strip()}",
                events=tuple(events),
            )

    @staticmethod
    def _validate_primary_output(
        artifact_type: VisualArtifactType,
        response: VisualEngineResponse,
    ) -> None:
        field, suffix = (
            ("pptx_filename", ".pptx")
            if artifact_type == "slide_deck"
            else ("video_filename", ".mp4")
        )
        value = response.outputs.get(field, "")
        if not value.casefold().endswith(suffix):
            raise VisualEngineError(f"Visual engine result is missing a valid {field}")

    @staticmethod
    def _publish_assets(
        response: VisualEngineResponse,
        staging: Path,
        output_dir: Path,
    ) -> list[dict[str, Any]]:
        declared = [
            *((f"output:{role}", path) for role, path in response.outputs.items()),
            *(("preview", path) for path in response.previews),
            *(("diagnostic", path) for path in response.diagnostics),
        ]
        assets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for role, relative in declared:
            normalized, source = _contained_file(staging, relative)
            if normalized in seen:
                continue
            seen.add(normalized)
            destination = (output_dir / normalized).resolve()
            if output_dir not in destination.parents:
                raise VisualEngineError(f"Visual engine output escapes the artifact: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = _sha256(source)
            size = source.stat().st_size
            source.replace(destination)
            assets.append(
                {
                    "role": role,
                    "path": normalized,
                    "size": size,
                    "sha256": digest,
                    "media_type": mimetypes.guess_type(normalized)[0]
                    or "application/octet-stream",
                }
            )
        return assets

    @staticmethod
    def _publish_manifest(
        *,
        request: VisualEngineRequest,
        response: VisualEngineResponse,
        assets: list[dict[str, Any]],
        events: list[VisualEngineEvent],
        output_dir: Path,
    ) -> Path:
        manifest = {
            "contract_version": response.contract_version,
            "artifact_id": request.artifact_id,
            "artifact_type": request.artifact_type,
            "engine": response.engine.model_dump(mode="json", exclude_defaults=True),
            "created_at": datetime.now(UTC).isoformat(),
            "assets": assets,
            "metadata": response.metadata,
            "warnings": response.warnings,
            "events": [event.payload() for event in events],
        }
        target = output_dir / "visual-engine-manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)
        return target


def _environment_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value) if value else None


def _executable(command: str | Path) -> Path | None:
    candidate = Path(command)
    if candidate.is_file():
        return candidate.resolve()
    resolved = shutil.which(str(command))
    return Path(resolved).resolve() if resolved else None


def _node_version(node: Path | None) -> str | None:
    if node is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - detected local executable
            [str(node), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_WINDOWS_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"v?(\d+(?:\.\d+){0,2})", result.stdout or result.stderr)
    return match.group(1) if result.returncode == 0 and match else None


def _event(line: str, prior_progress: int) -> VisualEngineEvent | None:
    text = line.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return VisualEngineEvent(
            type="diagnostic",
            stage="engine_output",
            progress=prior_progress,
            message=text[:2000],
            details={},
        )
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if event_type not in {"progress", "diagnostic"}:
        return None
    details = payload.get("details")
    return VisualEngineEvent(
        type=event_type,
        stage=str(payload.get("stage") or "rendering")[:80],
        progress=max(0, min(100, int(payload.get("progress") or prior_progress))),
        message=str(payload.get("message") or "")[:2000],
        details=dict(details) if isinstance(details, dict) else {},
    )


def _contained_file(root: Path, relative: str) -> tuple[str, Path]:
    normalized = Path(relative).as_posix().lstrip("/")
    if not normalized or Path(normalized).is_absolute() or ".." in Path(normalized).parts:
        raise VisualEngineError(f"Visual engine declared an unsafe asset path: {relative}")
    candidate = (root / normalized).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        raise VisualEngineError(f"Visual engine asset is missing or outside staging: {relative}")
    return normalized, candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    output = value
    for secret in secrets:
        if len(secret) >= 4:
            output = output.replace(secret, "[REDACTED]")
    return output


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 - PID comes from the owned child process
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            creationflags=_WINDOWS_NO_WINDOW,
            check=False,
        )
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)
