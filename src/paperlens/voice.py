from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from paperlens.provider_failover import ProviderPool


class Transcription(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    language: str
    language_probability: float = Field(ge=0.0, le=1.0)
    duration_seconds: float = Field(ge=0.0)
    provider: str = "local"
    model: str = "small"
    fallback_used: bool = False
    primary_error: str | None = None
    requests: list[dict[str, Any]] = Field(default_factory=list)


class SpeechTranscriber(Protocol):
    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription: ...


class GroqTranscriber:
    """High-accuracy cloud transcription through Groq Whisper Large V3."""

    def __init__(
        self,
        api_key: str,
        *,
        model_name: str = "whisper-large-v3",
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("GROQ_API_KEY is required for Groq transcription")
        self.model_name = model_name
        self._client = client or httpx.Client(
            base_url="https://api.groq.com/openai/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120.0,
        )

    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        if not audio:
            raise ValueError("Audio input is empty")
        filename = f"question{suffix if suffix.startswith('.') else f'.{suffix}'}"
        mime_type = {
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".wav": "audio/wav",
            ".webm": "audio/webm",
        }.get(Path(filename).suffix.casefold(), "application/octet-stream")
        response = self._client.post(
            "/audio/transcriptions",
            files={"file": (filename, audio, mime_type)},
            data={
                "model": self.model_name,
                "temperature": "0",
                "response_format": "verbose_json",
            },
        )
        response.raise_for_status()
        result = response.json()
        text = str(result.get("text", "")).strip()
        if not text:
            raise ValueError("No speech was detected in the audio")
        return Transcription(
            text=text,
            language=str(result.get("language", "unknown") or "unknown"),
            language_probability=0.0,
            duration_seconds=float(result.get("duration", 0.0) or 0.0),
            provider="groq",
            model=self.model_name,
        )


class FallbackTranscriber:
    """Use the cloud transcriber first and transparently fall back to local Whisper."""

    def __init__(
        self,
        primary: SpeechTranscriber,
        fallback: SpeechTranscriber,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        try:
            return self.primary.transcribe_bytes(audio, suffix)
        except Exception as error:  # noqa: BLE001 - fallback is the intended boundary
            result = self.fallback.transcribe_bytes(audio, suffix)
            status_method = getattr(self.primary, "status", None)
            status = status_method() if callable(status_method) else {}
            attempts = list(status.get("last_attempts", []))
            attempts.append(
                {
                    "slot": 0,
                    "status": "completed",
                    "provider": result.provider,
                    "model": result.model,
                    "wait_ms": 0,
                    "duration_ms": round(result.duration_seconds * 1000),
                }
            )
            return result.model_copy(
                update={
                    "fallback_used": True,
                    "primary_error": f"{type(error).__name__}: {str(error)[:180]}",
                    "requests": attempts,
                }
            )


class PooledTranscriber:
    """Rate-paced cloud speech pool shared by uploads and voice questions."""

    def __init__(
        self,
        transcribers: list[SpeechTranscriber],
        *,
        requests_per_minute: float = 4.0,
        global_requests_per_minute: float = 3.0,
        cooldown_seconds: float = 60.0,
    ) -> None:
        if not transcribers:
            raise ValueError("At least one speech transcriber is required")
        self._pool = ProviderPool(
            transcribers,
            provider="speech transcription",
            cooldown_seconds=cooldown_seconds,
            minimum_interval_seconds=60 / max(0.1, requests_per_minute),
            global_minimum_interval_seconds=(60 / max(0.1, global_requests_per_minute)),
            retry_rounds=2,
            wait_for_cooldown=True,
            restore_priority_after_cooldown=False,
            max_wait_seconds=max(3600.0, cooldown_seconds * 2),
        )

    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        result = self._pool.run(
            lambda transcriber, _index: transcriber.transcribe_bytes(audio, suffix)
        )
        return result.model_copy(update={"requests": self._pool.status()["last_attempts"]})

    def status(self) -> dict[str, object]:
        return {"available": True, **self._pool.status()}


class WhisperTranscriber:
    """Lazy offline transcription for Persian and English audio."""

    def __init__(
        self,
        model_name: str = "small",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model_factory = model_factory
        self._model: Any | None = None

    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        if not audio:
            raise ValueError("Audio input is empty")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                handle.write(audio)
                temporary_path = Path(handle.name)
            return self.transcribe_file(temporary_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def transcribe_file(self, path: Path) -> Transcription:
        if not path.is_file():
            raise FileNotFoundError(path)
        model = self._load_model()
        segments, info = model.transcribe(
            str(path),
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        completed_segments = list(segments)
        text = " ".join(segment.text.strip() for segment in completed_segments).strip()
        if not text:
            raise ValueError("No speech was detected in the audio")
        duration = max(
            (float(segment.end) for segment in completed_segments),
            default=0.0,
        )
        return Transcription(
            text=text,
            language=str(info.language),
            language_probability=float(info.language_probability),
            duration_seconds=duration,
            provider="local",
            model=self.model_name,
        )

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        factory = self._model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise RuntimeError(
                    "Voice support is not installed. Run: uv sync --extra voice"
                ) from error
            factory = WhisperModel
        self._model = factory(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )
        return self._model
