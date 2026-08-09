from __future__ import annotations

from collections.abc import Callable, Sequence
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

PageDrawer = Callable[[Canvas], None]


def pdf_bytes(
    pages: Sequence[str | PageDrawer],
    *,
    pagesize: tuple[float, float] = letter,
) -> bytes:
    output = BytesIO()
    canvas = Canvas(output, pagesize=pagesize)
    for content in pages:
        if callable(content):
            content(canvas)
        else:
            text = canvas.beginText(72, pagesize[1] - 72)
            text.setLeading(18)
            for line in content.splitlines():
                text.textLine(line)
            canvas.drawText(text)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def write_text_pdf(
    path: Path,
    pages: str | Sequence[str],
    *,
    pagesize: tuple[float, float] = letter,
) -> None:
    values = [pages] if isinstance(pages, str) else list(pages)
    path.write_bytes(pdf_bytes(values, pagesize=pagesize))


def png_bytes(width: int = 320, height: int = 180, text: str = "") -> bytes:
    image = Image.new("RGB", (width, height), "white")
    if text:
        ImageDraw.Draw(image).text((24, 40), text, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def write_scanned_pdf(
    path: Path,
    *,
    page_count: int = 1,
    width: int = 300,
    height: int = 220,
) -> None:
    def scanned_page(index: int) -> PageDrawer:
        def draw(canvas: Canvas) -> None:
            image = png_bytes(width, height, f"Unique scanned prose page {index + 1}")
            canvas.drawImage(
                ImageReader(BytesIO(image)),
                0,
                0,
                width=width,
                height=height,
            )

        return draw

    path.write_bytes(
        pdf_bytes(
            [scanned_page(index) for index in range(page_count)],
            pagesize=(width, height),
        )
    )
