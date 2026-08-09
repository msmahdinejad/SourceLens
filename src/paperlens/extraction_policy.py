from __future__ import annotations

import re
from dataclasses import dataclass

from paperlens.pdf_backend import PageGeometry, Rectangle

_VISUAL_LANGUAGE = re.compile(
    r"\b(?:figure|fig\.?|chart|diagram|plot|graph|architecture|flowchart|illustration)\b|"
    r"(?:شکل|نمودار|دیاگرام|معماری|فلوچارت|تصویر)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PageVisualSignals:
    """Geometry-based page signals with decorative PDF assets filtered out."""

    image_count: int
    max_image_ratio: float
    image_coverage_ratio: float
    drawing_count: int
    semantic_drawing_count: int
    max_drawing_ratio: float
    drawing_coverage_ratio: float

    @classmethod
    def from_page(cls, page: PageGeometry) -> PageVisualSignals:
        page_area = max(page.width * page.height, 1.0)
        image_ratios = [_area_ratio(item, page_area) for item in page.image_boxes()]
        drawing_ratios = [_area_ratio(item, page_area) for item in page.drawing_boxes()]
        # Full-page rectangles are usually slide backgrounds or page borders;
        # tiny paths are bullets, glyph decorations, and separators.
        semantic_drawings = [ratio for ratio in drawing_ratios if 0.006 <= ratio <= 0.64]
        visible_images = [ratio for ratio in image_ratios if ratio >= 0.004]
        return cls(
            image_count=len(image_ratios),
            max_image_ratio=max(image_ratios, default=0.0),
            image_coverage_ratio=min(1.0, sum(visible_images)),
            drawing_count=len(drawing_ratios),
            semantic_drawing_count=len(semantic_drawings),
            max_drawing_ratio=max(semantic_drawings, default=0.0),
            drawing_coverage_ratio=min(1.0, sum(semantic_drawings)),
        )

    @property
    def full_page_raster(self) -> bool:
        return self.max_image_ratio >= 0.72

    @property
    def meaningful_visual(self) -> bool:
        large_raster = self.max_image_ratio >= 0.34 or self.image_coverage_ratio >= 0.48
        structured_vector = (
            self.semantic_drawing_count >= 12
            and self.max_drawing_ratio >= 0.08
            and self.drawing_coverage_ratio >= 0.24
        )
        return large_raster or structured_vector


class ExtractionPolicy:
    """Choose paid/remote extraction only when local page evidence is insufficient."""

    def embedded_text_is_sufficient(
        self,
        *,
        quality_passed: bool,
        text: str,
        visuals: PageVisualSignals,
    ) -> bool:
        if not quality_passed:
            return False
        compact_length = len(re.sub(r"\s+", "", text))
        # A scan may contain a tiny invisible text layer. Do not trust it when a
        # page-sized raster dominates the page.
        return not (visuals.full_page_raster and compact_length < 220)

    def needs_vlm(
        self,
        *,
        embedded_text_usable: bool,
        extracted_text: str,
        ocr_confidence: float,
        visuals: PageVisualSignals,
    ) -> bool:
        compact = re.sub(r"\s+", "", extracted_text)
        if not embedded_text_usable:
            if not compact or len(compact) < 45 or (ocr_confidence and ocr_confidence < 0.58):
                return True
            return visuals.meaningful_visual and bool(_VISUAL_LANGUAGE.search(extracted_text))

        # Native text wins for ordinary documents and slide decks. Enrich only
        # genuinely large semantic visuals, never logos, bullets, or backgrounds.
        return visuals.meaningful_visual and bool(_VISUAL_LANGUAGE.search(extracted_text))


def _area_ratio(rectangle: Rectangle, page_area: float) -> float:
    left, top, right, bottom = rectangle
    area = max(0.0, right - left) * max(0.0, bottom - top)
    return area / page_area
