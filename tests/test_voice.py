from pathlib import Path
from types import SimpleNamespace

from paperlens.voice import (
    FallbackTranscriber,
    GroqTranscriber,
    PooledTranscriber,
    Transcription,
    WhisperTranscriber,
)


class FakeWhisperModel:
    def __init__(self, model_name: str, *, device: str, compute_type: str) -> None:
        assert (model_name, device, compute_type) == ("small", "cpu", "int8")

    def transcribe(self, path: str, **kwargs):
        assert Path(path).read_bytes() == b"fake wav"
        assert kwargs["vad_filter"] is True
        segments = iter(
            [
                SimpleNamespace(text=" سلام ", end=0.8),
                SimpleNamespace(text="دنیا", end=1.4),
            ]
        )
        info = SimpleNamespace(language="fa", language_probability=0.98)
        return segments, info


def test_transcriber_collects_generator_and_metadata() -> None:
    transcriber = WhisperTranscriber(model_factory=FakeWhisperModel)

    result = transcriber.transcribe_bytes(b"fake wav")

    assert result.text == "سلام دنیا"
    assert result.language == "fa"
    assert result.language_probability == 0.98
    assert result.duration_seconds == 1.4
    assert result.provider == "local"


class FakeGroqResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"text": "پرسش آزمایشی", "language": "fa", "duration": 2.5}


class FakeGroqClient:
    def post(self, path: str, *, files, data):
        assert path == "/audio/transcriptions"
        assert files["file"] == ("question.webm", b"voice", "audio/webm")
        assert data == {
            "model": "whisper-large-v3",
            "temperature": "0",
            "response_format": "verbose_json",
        }
        return FakeGroqResponse()


def test_groq_transcriber_uses_whisper_large_v3() -> None:
    result = GroqTranscriber("test-key", client=FakeGroqClient()).transcribe_bytes(
        b"voice",
        ".webm",
    )

    assert result.text == "پرسش آزمایشی"
    assert result.language == "fa"
    assert result.duration_seconds == 2.5
    assert result.provider == "groq"
    assert result.model == "whisper-large-v3"
    assert result.fallback_used is False


class FailingTranscriber:
    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        raise RuntimeError("cloud is unavailable")


class LocalTranscriber:
    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        return Transcription(
            text="local result",
            language="en",
            language_probability=0.9,
            duration_seconds=1.0,
            provider="local",
            model="small",
        )


def test_transcription_falls_back_to_local_model() -> None:
    result = FallbackTranscriber(FailingTranscriber(), LocalTranscriber()).transcribe_bytes(
        b"voice"
    )

    assert result.text == "local result"
    assert result.provider == "local"
    assert result.fallback_used is True
    assert result.primary_error == "RuntimeError: cloud is unavailable"


class TimeoutTranscriber:
    def transcribe_bytes(self, audio: bytes, suffix: str = ".wav") -> Transcription:
        raise TimeoutError("temporary speech timeout")


def test_cloud_transcription_pool_fails_over_without_losing_attempts() -> None:
    pool = PooledTranscriber(
        [TimeoutTranscriber(), LocalTranscriber()],
        requests_per_minute=120,
        global_requests_per_minute=120,
    )
    pool._pool.minimum_interval_seconds = 0
    pool._pool.global_minimum_interval_seconds = 0

    result = pool.transcribe_bytes(b"voice")

    assert result.text == "local result"
    assert [attempt["status"] for attempt in result.requests] == [
        "failed",
        "completed",
    ]
