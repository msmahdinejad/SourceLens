from __future__ import annotations

import io
import shutil
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from paperlens.provider_failover import ProviderPool


class OCRResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provider: str
    warnings: list[str] = Field(default_factory=list)
    requests: list[dict[str, Any]] = Field(default_factory=list)


class OCRAdapter(Protocol):
    available: bool
    label: str

    def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult: ...


class OCRSpaceClient:
    """OCR.Space Engine 3 adapter with bounded retries and injectable HTTP."""

    label = "OCR.Space Engine 3"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 40.0,
        retries: int = 2,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.available = bool(self.api_key)
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._client = client

    def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
        if not self.available:
            raise RuntimeError("OCR_SPACE_API_KEY is not configured")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = {
                    "headers": {"apikey": self.api_key},
                    "data": {
                        "OCREngine": "3",
                        "language": self._language(language_hint),
                        "isOverlayRequired": "false",
                        "detectOrientation": "true",
                        "isTable": "true",
                        "scale": "true",
                    },
                    "files": {"file": ("page.png", image, "image/png")},
                }
                if self._client is not None:
                    response = self._client.post(
                        "https://api.ocr.space/parse/image",
                        **request,
                    )
                else:
                    with httpx.Client(timeout=self.timeout_seconds) as client:
                        response = client.post(
                            "https://api.ocr.space/parse/image",
                            **request,
                        )
                response.raise_for_status()
                return self._parse(response.json())
            except httpx.HTTPStatusError as error:
                last_error = error
                if error.response.status_code == 429:
                    # The shared pool owns quota cooldown and credential rotation.
                    # Retrying the same key here would only amplify the limit.
                    raise
                if error.response.status_code != 408 and not (
                    500 <= error.response.status_code < 600
                ):
                    raise RuntimeError(
                        f"OCR.Space request was rejected with HTTP {error.response.status_code}"
                    ) from error
                if attempt < self.retries:
                    time.sleep(0.25 * (2**attempt))
            except (
                httpx.RequestError,
                KeyError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(0.25 * (2**attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("OCR.Space failed without a provider response")

    def _parse(self, payload: dict[str, Any]) -> OCRResult:
        exit_code = int(payload.get("OCRExitCode") or 1)
        if payload.get("IsErroredOnProcessing") or exit_code not in {1, 2}:
            message = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "unknown error"
            if isinstance(message, list):
                message = "; ".join(str(item) for item in message)
            raise RuntimeError(f"OCR.Space rejected the page: {message}")
        parsed = payload.get("ParsedResults") or []
        failed_parts = [
            item for item in parsed if int(item.get("FileParseExitCode") or 1) not in {1}
        ]
        text = "\n".join(str(item.get("ParsedText", "")).strip() for item in parsed).strip()
        if not text:
            raise RuntimeError("OCR.Space returned no text")
        confidence_values = [
            float(item["TextOverlay"]["MeanConfidence"])
            for item in parsed
            if isinstance(item.get("TextOverlay"), dict)
            and item["TextOverlay"].get("MeanConfidence") is not None
        ]
        confidence = (
            sum(confidence_values) / len(confidence_values) / 100 if confidence_values else 0.75
        )
        return OCRResult(
            text=text,
            confidence=max(0.0, min(1.0, confidence)),
            provider=self.label,
            warnings=(
                [f"OCR.Space reported {len(failed_parts)} partial parse failure(s)"]
                if failed_parts
                else []
            ),
        )

    @staticmethod
    def _language(hint: str | None) -> str:
        if hint and hint.casefold().startswith(("fa", "per")):
            return "per"
        return "auto"


class TesseractOCR:
    label = "local Tesseract"

    def __init__(self) -> None:
        try:
            import pytesseract  # noqa: F401

            self.available = shutil.which("tesseract") is not None
        except ImportError:
            self.available = False

    def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
        if not self.available:
            raise RuntimeError(
                "Local OCR is unavailable; install Tesseract and the optional pytesseract package"
            )
        import pytesseract
        from PIL import Image

        languages = "fas+eng" if language_hint and language_hint.startswith("fa") else "eng"
        text = pytesseract.image_to_string(Image.open(io.BytesIO(image)), lang=languages).strip()
        if not text:
            raise RuntimeError("Local Tesseract returned no text")
        return OCRResult(text=text, confidence=0.65, provider=self.label)


class FallbackOCR:
    def __init__(self, primary: OCRAdapter | None, fallback: OCRAdapter | None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.available = bool((primary and primary.available) or (fallback and fallback.available))
        self.label = "OCR.Space with local fallback"

    def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
        warnings: list[str] = []
        for adapter in (self.primary, self.fallback):
            if adapter is None or not adapter.available:
                continue
            try:
                result = adapter.extract(image, language_hint=language_hint)
                return result.model_copy(update={"warnings": [*warnings, *result.warnings]})
            except Exception as error:  # noqa: BLE001 - fallback must isolate provider failure
                warnings.append(
                    f"{adapter.label} failed: {type(error).__name__}: {error}"
                )
        message = "; ".join(warnings) or "No OCR provider is available"
        raise RuntimeError(message)

    def status(self) -> dict[str, object]:
        primary_status = (
            self.primary.status()
            if self.primary is not None and hasattr(self.primary, "status")
            else {"available": bool(self.primary and self.primary.available)}
        )
        return {
            **primary_status,
            "available": self.available,
            "local_fallback_available": bool(self.fallback and self.fallback.available),
        }


class PooledOCR:
    """Rate-paced OCR credential pool with cooldown and transparent failover."""

    label = "OCR provider pool"

    def __init__(
        self,
        adapters: list[OCRAdapter],
        *,
        requests_per_minute: float = 10.0,
        global_requests_per_minute: float = 10.0,
        cooldown_seconds: float = 60.0,
    ) -> None:
        resources = [adapter for adapter in adapters if adapter.available]
        self.available = bool(resources)
        self._pool = (
            ProviderPool(
                resources,
                provider="OCR",
                retryable=lambda _error: True,
                cooldown_seconds=cooldown_seconds,
                minimum_interval_seconds=60 / max(0.1, requests_per_minute),
                global_minimum_interval_seconds=(60 / max(0.1, global_requests_per_minute)),
                retry_rounds=2,
                wait_for_cooldown=True,
                restore_priority_after_cooldown=False,
                max_wait_seconds=max(3600.0, cooldown_seconds * 2),
            )
            if resources
            else None
        )

    def extract(self, image: bytes, *, language_hint: str | None = None) -> OCRResult:
        if self._pool is None:
            raise RuntimeError("No OCR provider is configured")
        result = self._pool.run(
            lambda adapter, _index: adapter.extract(
                image,
                language_hint=language_hint,
            )
        )
        status = self._pool.status()
        return result.model_copy(update={"requests": status["last_attempts"]})

    def status(self) -> dict[str, object]:
        if self._pool is None:
            return {"available": False, "credential_count": 0}
        return {"available": True, **self._pool.status()}
