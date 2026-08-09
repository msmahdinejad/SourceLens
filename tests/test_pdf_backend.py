from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from paperlens.extraction_policy import PageVisualSignals
from paperlens.fonts import FontRegistry
from paperlens.pdf_backend import open_image, open_pdf, render_pdf_page


def _pdf(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=letter)
    canvas.drawString(72, 720, "SourceLens PDF backend")
    canvas.drawString(72, 700, "Exact identifier ZXQ-4097 and equation E = mc2")
    canvas.rect(72, 480, 280, 160, stroke=1, fill=0)
    canvas.showPage()
    canvas.drawString(72, 720, "Second physical page")
    canvas.save()


def test_pdf_backend_extracts_text_geometry_and_physical_pages(tmp_path: Path) -> None:
    source = tmp_path / "backend.pdf"
    _pdf(source)

    with open_pdf(source) as document:
        first = document[0]
        signals = PageVisualSignals.from_page(first)

        assert len(document) == 2
        assert "ZXQ-4097" in first.text()
        assert "Second physical page" in document[1].text()
        assert first.width == 612
        assert first.height == 792
        assert signals.drawing_count >= 1


def test_pdfium_rendering_is_serialized_and_preserves_requested_scale(tmp_path: Path) -> None:
    source = tmp_path / "render.pdf"
    _pdf(source)

    with ThreadPoolExecutor(max_workers=4) as executor:
        rendered = list(
            executor.map(lambda _index: render_pdf_page(source, 0, scale=1.5), range(8))
        )

    for content in rendered:
        with Image.open(BytesIO(content)) as image:
            assert image.size == (918, 1188)
            assert image.mode == "RGB"


def test_pdf_render_preserves_fixture_geometry_at_expected_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "geometry.pdf"
    _pdf(source)

    with Image.open(BytesIO(render_pdf_page(source, 0, scale=1))) as image:
        rectangle_region = image.convert("L").crop((68, 148, 356, 316))

    assert sum(rectangle_region.histogram()[:128]) >= 1_700


def test_pdf_backend_preserves_visual_reading_order_and_rotated_pages(tmp_path: Path) -> None:
    source = tmp_path / "reading-order.pdf"
    canvas = Canvas(str(source), pagesize=letter)
    canvas.drawString(72, 720, "LEFT-A")
    canvas.drawString(72, 700, "LEFT-B")
    canvas.drawString(330, 720, "RIGHT-A")
    canvas.drawString(330, 700, "RIGHT-B")
    canvas.showPage()
    canvas.setPageRotation(90)
    canvas.drawString(72, 720, "Rotated identifier R-90")
    canvas.save()

    with open_pdf(source.read_bytes()) as document:
        assert document[0].text().splitlines() == ["LEFT-A RIGHT-A", "LEFT-B RIGHT-B"]
        assert " ".join(document[1].text().split()) == "Rotated identifier R-90"
        with Image.open(BytesIO(document[1].render_png(scale=1))) as image:
            assert image.size == (612, 792)


def test_image_page_uses_original_pixels_without_pdf_round_trip() -> None:
    source = BytesIO()
    Image.new("RGB", (320, 180), "white").save(source, format="PNG")

    page = open_image(source.getvalue())
    signals = PageVisualSignals.from_page(page)

    assert page.text() == ""
    assert signals.full_page_raster is True
    with Image.open(BytesIO(page.render_png(scale=2))) as image:
        assert image.size == (640, 360)


def test_pdf_backend_preserves_persian_exact_identifiers_and_formulas(tmp_path: Path) -> None:
    source = tmp_path / "persian-formula.pdf"
    font_name = "Vazirmatn-PDF-parity"
    pdfmetrics.registerFont(
        TTFont(
            font_name,
            str(FontRegistry.default_bundled_root() / "Vazirmatn" / "Vazirmatn-Regular.ttf"),
        )
    )
    text = "شناسه دقیق ZXQ-4097 و معادله E = mc²"
    canvas = Canvas(str(source), pagesize=letter)
    canvas.setFont(font_name, 18)
    canvas.drawRightString(540, 720, text)
    canvas.save()

    with open_pdf(source) as document:
        extracted = document[0].text()

    assert text in extracted
