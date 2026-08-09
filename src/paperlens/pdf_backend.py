from __future__ import annotations

import threading
from io import BytesIO
from pathlib import Path
from typing import Protocol, Self

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

Rectangle = tuple[float, float, float, float]
PDFInput = Path | bytes
_PDFIUM_LOCK = threading.RLock()


class PageGeometry(Protocol):
    width: float
    height: float

    def text(self) -> str: ...

    def image_boxes(self) -> list[Rectangle]: ...

    def drawing_boxes(self) -> list[Rectangle]: ...

    def render_png(self, *, scale: float = 2.0) -> bytes: ...


class PDFDocument:
    """Layout extraction with serialized, permissively licensed PDFium rendering."""

    def __init__(self, source: PDFInput) -> None:
        self.source = source.resolve() if isinstance(source, Path) else bytes(source)
        self._buffer = BytesIO(self.source) if isinstance(self.source, bytes) else None
        self._document = pdfplumber.open(self._buffer or self.source)

    def __len__(self) -> int:
        return len(self._document.pages)

    def __getitem__(self, index: int) -> PDFPage:
        return PDFPage(self, index)

    def __iter__(self):
        for index in range(len(self)):
            yield self[index]

    def close(self) -> None:
        self._document.close()
        if self._buffer is not None:
            self._buffer.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class PDFPage:
    def __init__(self, document: PDFDocument, index: int) -> None:
        self.document = document
        self.index = index
        self._page = document._document.pages[index]
        self.width = float(self._page.width)
        self.height = float(self._page.height)

    def text(self) -> str:
        return self._page.extract_text(x_tolerance=3, y_tolerance=3) or ""

    def image_boxes(self) -> list[Rectangle]:
        return [_box(item) for item in self._page.images]

    def drawing_boxes(self) -> list[Rectangle]:
        objects = [*self._page.rects, *self._page.curves, *self._page.lines]
        return [_box(item) for item in objects]

    def render_png(self, *, scale: float = 2.0) -> bytes:
        return render_pdf_page(self.document.source, self.index, scale=scale)


class ImagePage:
    def __init__(self, content: bytes) -> None:
        try:
            with Image.open(BytesIO(content)) as source:
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("An image source must contain exactly one frame")
                source.load()
                self._image = source.convert("RGB")
        except (OSError, ValueError) as error:
            raise ValueError("Unsupported image") from error
        self.width = float(self._image.width)
        self.height = float(self._image.height)

    def text(self) -> str:
        return ""

    def image_boxes(self) -> list[Rectangle]:
        return [(0.0, 0.0, self.width, self.height)]

    def drawing_boxes(self) -> list[Rectangle]:
        return []

    def render_png(self, *, scale: float = 2.0) -> bytes:
        image = self._image
        if scale != 1:
            image = image.resize(
                (round(self.width * scale), round(self.height * scale)),
                Image.Resampling.LANCZOS,
            )
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def open_pdf(source: PDFInput) -> PDFDocument:
    return PDFDocument(source)


def open_image(content: bytes) -> ImagePage:
    return ImagePage(content)


def render_pdf_page(source: PDFInput, page_index: int, *, scale: float = 2.0) -> bytes:
    if scale <= 0:
        raise ValueError("PDF render scale must be positive")
    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(source)
        try:
            if page_index < 0 or page_index >= len(document):
                raise IndexError(page_index)
            page = document[page_index]
            try:
                bitmap = page.render(scale=scale, rev_byteorder=True)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    output = BytesIO()
                    image.save(output, format="PNG")
                    return output.getvalue()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()


def _box(item: dict) -> Rectangle:
    return (
        float(item.get("x0", 0.0)),
        float(item.get("top", 0.0)),
        float(item.get("x1", 0.0)),
        float(item.get("bottom", 0.0)),
    )
