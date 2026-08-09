from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from paperlens.ingestion import DocumentIngestionPipeline
from paperlens.ocr import FallbackOCR, OCRResult, OCRSpaceClient, PooledOCR
from paperlens.vlm import OpenAICompatibleVLM, PooledVLM, VLMResult
from tests.pdf_fixtures import pdf_bytes, write_scanned_pdf


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://api.ocr.space/parse/image")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class RecordingHTTPClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _ocr_payload(text: str = "recognized text") -> dict:
    return {
        "OCRExitCode": 1,
        "IsErroredOnProcessing": False,
        "ParsedResults": [
            {
                "FileParseExitCode": 1,
                "ParsedText": text,
                "TextOverlay": {"MeanConfidence": 93},
            }
        ],
    }


def test_ocr_space_uses_engine_three_and_parses_confidence() -> None:
    client = RecordingHTTPClient([FakeResponse(_ocr_payload())])
    ocr = OCRSpaceClient("secret-key", client=client, retries=0)

    result = ocr.extract(b"\x89PNG\r\n")

    _, request = client.requests[0]
    assert request["headers"]["apikey"] == "secret-key"
    assert request["data"]["OCREngine"] == "3"
    assert request["data"]["detectOrientation"] == "true"
    assert result.text == "recognized text"
    assert result.confidence == pytest.approx(0.93)


def test_ocr_space_retries_timeout_but_not_invalid_credentials() -> None:
    request = httpx.Request("POST", "https://api.ocr.space/parse/image")
    retrying = RecordingHTTPClient(
        [httpx.ReadTimeout("timeout", request=request), FakeResponse(_ocr_payload("ok"))]
    )
    assert OCRSpaceClient("key", client=retrying, retries=1).extract(b"png").text == "ok"
    assert len(retrying.requests) == 2

    rejected = RecordingHTTPClient(
        [FakeResponse({}, status_code=401), FakeResponse(_ocr_payload())]
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        OCRSpaceClient("key", client=rejected, retries=3).extract(b"png")
    assert len(rejected.requests) == 1


def test_ocr_space_leaves_rate_limit_retry_to_the_shared_pool() -> None:
    limited = RecordingHTTPClient([FakeResponse({}, status_code=429), FakeResponse(_ocr_payload())])

    with pytest.raises(httpx.HTTPStatusError):
        OCRSpaceClient("key", client=limited, retries=3).extract(b"png")

    assert len(limited.requests) == 1


def test_ocr_fallback_returns_local_result_and_warning() -> None:
    class Broken:
        available = True
        label = "broken"

        def extract(self, image, *, language_hint=None):
            raise RuntimeError("offline")

    class Local:
        available = True
        label = "local"

        def extract(self, image, *, language_hint=None):
            return OCRResult(text="local text", confidence=0.7, provider="local")

    result = FallbackOCR(Broken(), Local()).extract(b"png")

    assert result.provider == "local"
    assert result.warnings == ["broken failed: RuntimeError: offline"]


def test_vlm_sends_page_image_and_returns_structured_visual_evidence() -> None:
    message = SimpleNamespace(
        content=(
            '{"visual_summary":"chart summary","tables":[],"figures":'
            '[{"labels":["A"]}],"confidence":0.9}'
        )
    )
    completions = SimpleNamespace(
        create=lambda **request: SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            request=request,
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    vlm = OpenAICompatibleVLM(
        model="vision-test",
        api_key="key",
        base_url="https://example.test/v1",
        client=client,
    )

    result = vlm.analyze(b"\x89PNG", page_number=3)

    assert result.visual_summary == "chart summary"
    assert result.figures[0]["labels"] == ["A"]


def test_vlm_tolerates_loose_provider_json_types() -> None:
    message = SimpleNamespace(
        content=(
            '{"recognized_text":"visible text","visual_summary":"summary",'
            '"tables":3,"figures":"one diagram","confidence":"high"}'
        )
    )
    completions = SimpleNamespace(
        create=lambda **_request: SimpleNamespace(choices=[SimpleNamespace(message=message)])
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = OpenAICompatibleVLM(
        model="vision-test",
        api_key="key",
        base_url="https://example.test/v1",
        client=client,
    ).analyze(b"png", page_number=1)

    assert result.tables == []
    assert result.figures == [{"description": "one diagram"}]
    assert result.confidence == 0.75


def test_one_page_ocr_failure_does_not_abort_following_page(tmp_path: Path) -> None:
    pdf = tmp_path / "two-scans.pdf"
    write_scanned_pdf(pdf, page_count=2)

    class SometimesOCR:
        available = True
        label = "sometimes"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, image, *, language_hint=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first page unavailable")
            return OCRResult(
                text="Second page OCR has enough content to remain independently usable.",
                confidence=0.8,
                provider="sometimes",
            )

    result = DocumentIngestionPipeline(ocr=SometimesOCR(), vlm=None).process(
        pdf,
        workspace_id="ws",
        document_id="doc",
        original_filename="two-scans.pdf",
    )

    assert result.page_count == 2
    assert any("OCR failed on page 1" in warning for warning in result.pages[0].warnings)
    assert "Second page OCR" in result.pages[1].normalized_text


def test_native_text_slides_do_not_spend_ocr_or_vlm_requests(tmp_path: Path) -> None:
    """Decorative slide assets must not turn a text PDF into 33 vision requests."""
    def draw_slide(page) -> None:
        page.setFillColorRGB(1, 1, 1)
        page.setStrokeColorRGB(0.9, 0.9, 0.9)
        page.rect(0, 0, 960, 540, stroke=1, fill=1)
        page.setFillColorRGB(0.1, 0.6, 0.5)
        for offset in range(8):
            page.circle(900 - offset * 24, 500, 7, stroke=1, fill=1)
        page.setFillColorRGB(0, 0, 0)
        page.setFont("Helvetica-Bold", 28)
        page.drawString(72, 430, "Reinforcement Learning")
        page.setFont("Helvetica", 15)
        page.drawString(
            72,
            370,
            "A complete text slide about states, actions, rewards, policies, and value functions.",
        )

    pdf = tmp_path / "native-text-slides.pdf"
    pdf.write_bytes(pdf_bytes([draw_slide], pagesize=(960, 540)))

    class RecordingOCR:
        available = True
        label = "ocr"

        def __init__(self) -> None:
            self.calls = 0

        def extract(self, image, *, language_hint=None):
            self.calls += 1
            return OCRResult(text="unexpected OCR", confidence=0.9, provider=self.label)

    class RecordingVLM:
        available = True
        label = "vlm"

        def __init__(self) -> None:
            self.calls = 0

        def analyze(self, image, *, page_number):
            self.calls += 1
            return VLMResult(visual_summary="unexpected VLM", provider=self.label)

    ocr = RecordingOCR()
    vlm = RecordingVLM()
    result = DocumentIngestionPipeline(ocr=ocr, vlm=vlm).process(
        pdf,
        workspace_id="ws",
        document_id="doc",
        original_filename=pdf.name,
    )

    assert result.pages[0].extraction_method == "text"
    assert result.pages[0].embedded_text_usable is True
    assert ocr.calls == 0
    assert vlm.calls == 0


def test_clean_sparse_native_title_is_text_not_a_failed_scan(tmp_path: Path) -> None:
    def draw_title(page) -> None:
        page.setFont("Helvetica", 22)
        page.drawString(72, 690, "Reinforcement Learning - Winter 2026")

    pdf = tmp_path / "title.pdf"
    pdf.write_bytes(pdf_bytes([draw_title]))

    class NeverOCR:
        available = True
        label = "ocr"

        def extract(self, image, *, language_hint=None):
            raise AssertionError("A clean native title page must not use OCR")

    result = DocumentIngestionPipeline(ocr=NeverOCR(), vlm=None).process(
        pdf,
        workspace_id="ws",
        document_id="doc",
        original_filename=pdf.name,
    )

    assert result.pages[0].extraction_method == "text"
    assert result.pages[0].normalized_text.startswith("Reinforcement Learning")


def test_ocr_pool_rotates_rate_limited_provider_without_losing_page() -> None:
    class LimitedOCR:
        available = True
        label = "limited"

        def extract(self, image, *, language_hint=None):
            error = RuntimeError("rate limited")
            error.status_code = 429
            raise error

    class WorkingOCR:
        available = True
        label = "working"

        def extract(self, image, *, language_hint=None):
            return OCRResult(text="recognized page", confidence=0.9, provider=self.label)

    result = PooledOCR(
        [LimitedOCR(), WorkingOCR()],
        requests_per_minute=600,
        global_requests_per_minute=600,
    ).extract(b"image")

    assert result.text == "recognized page"
    assert result.provider == "working"
    assert [item["status"] for item in result.requests] == ["failed", "completed"]


def test_vlm_pool_rotates_and_preserves_recognized_text() -> None:
    class LimitedVLM:
        available = True
        label = "limited"

        def analyze(self, image, *, page_number):
            error = RuntimeError("rate limited")
            error.status_code = 429
            raise error

    class WorkingVLM:
        available = True
        label = "working"

        def analyze(self, image, *, page_number):
            return VLMResult(
                recognized_text="visible scanned text",
                visual_summary="page summary",
                confidence=0.9,
                provider=self.label,
            )

    pool = PooledVLM(
        [LimitedVLM(), WorkingVLM()],
        requests_per_minute=600,
        global_requests_per_minute=600,
    )
    result = pool.analyze(b"image", page_number=1)

    assert result.recognized_text == "visible scanned text"
    assert result.provider == "working"
    assert pool.status()["active_slot"] == 2
    assert [item["status"] for item in result.requests] == ["failed", "completed"]


def test_vlm_pool_retries_strongest_model_after_cooldown() -> None:
    class StrongestVLM:
        available = True
        label = "strongest"

        def __init__(self) -> None:
            self.calls = 0

        def analyze(self, image, *, page_number):
            self.calls += 1
            if self.calls == 1:
                error = RuntimeError("temporary rate limit")
                error.status_code = 429
                raise error
            return VLMResult(visual_summary="strong result", provider=self.label)

    class FallbackVLM:
        available = True
        label = "fallback"

        def analyze(self, image, *, page_number):
            return VLMResult(visual_summary="fallback result", provider=self.label)

    pool = PooledVLM(
        [StrongestVLM(), FallbackVLM()],
        requests_per_minute=600_000,
        global_requests_per_minute=600_000,
        cooldown_seconds=0,
    )

    assert pool.analyze(b"image", page_number=1).provider == "fallback"
    assert pool.analyze(b"image", page_number=2).provider == "strongest"
    assert pool.status()["active_slot"] == 1
