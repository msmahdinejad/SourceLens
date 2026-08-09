from __future__ import annotations

import re


def is_multi_paper_question(question: str) -> bool:
    text = _normalize(question)
    return _contains(
        text,
        "چهار مقاله",
        "مقالات",
        "روش ها",
        "تفاوت روش",
        "مقایسه روش",
        "all four",
        "all papers",
        "among the papers",
        "methods differ",
        "architectural differences",
    )


def is_multi_aspect_comparison(question: str) -> bool:
    """Detect broad comparisons that must not collapse to one matching facet."""
    text = _normalize(question)
    if not _contains(text, "مقایسه", "compare", "comparison", "تفاوت"):
        return False
    facets = (
        _contains(text, "ایده", "نوآوری", "main idea", "innovation"),
        _contains(text, "معماری", "ساختار", "architecture", "structural"),
        _contains(text, "مزیت", "مزایا", "نقطه قوت", "advantage", "strength"),
        _contains(text, "محدودیت", "نقاط ضعف", "limitation", "weakness"),
        _contains(text, "تکامل", "روند پیشرفت", "مسیر پیشرفت", "evolution", "progression"),
    )
    return sum(facets) >= 2


def _normalize(value: str) -> str:
    value = value.casefold().replace("ي", "ی").replace("ك", "ک")
    value = value.replace("‌", " ").replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", value).strip()


def _contains(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)
