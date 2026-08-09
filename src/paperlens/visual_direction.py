from __future__ import annotations

import hashlib
import math
import random
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

PRESENTATION_LAYOUTS = (
    "cover_cinematic",
    "editorial_split",
    "cards_icon",
    "metrics_dashboard",
    "timeline_story",
    "comparison_dual",
    "chart_focus",
    "matrix_2x2",
    "process_flow",
    "photo_mosaic",
    "quote_photo",
    "big_statement",
    "case_study",
    "table_clean",
    "funnel",
    "dashboard",
    "closing_actions",
)

ICON_NAMES = (
    "cart",
    "store",
    "robot",
    "sparkles",
    "user",
    "users",
    "globe",
    "bolt",
    "chart",
    "trend",
    "database",
    "gear",
    "shield",
    "mobile",
    "search",
    "package",
    "truck",
    "wallet",
    "heart",
    "eye",
    "target",
    "clock",
    "check",
    "layers",
    "cloud",
    "brain",
    "link",
    "rocket",
    "compass",
    "message",
    "lock",
    "filter",
    "play",
    "star",
)

VIDEO_LAYOUTS = (
    "hero_cinematic",
    "lower_third",
    "split_panel",
    "glass_card",
    "spotlight",
    "editorial",
    "minimal_type",
    "data_bars",
    "process",
    "quote",
    "comparison",
    "timeline",
    "closing_statement",
)

_LEGACY_PRESENTATION_LAYOUTS = {
    "cover": "cover_cinematic",
    "section": "big_statement",
    "key_points": "cards_icon",
    "metrics": "metrics_dashboard",
    "chart": "chart_focus",
    "comparison": "comparison_dual",
    "timeline": "timeline_story",
    "process": "process_flow",
    "quote": "quote_photo",
    "image_focus": "editorial_split",
    "closing": "closing_actions",
}

_PRESENTATION_THEMES = {
    "forest_cream": {
        "primary": "173D36",
        "secondary": "4E7468",
        "accent": "D6B35D",
        "accent2": "7CA99A",
        "background": "F5F1E7",
        "surface": "FFFCF5",
        "text": "17211E",
        "muted": "66716C",
    },
    "warm_neutral": {
        "primary": "3E3935",
        "secondary": "866D59",
        "accent": "C57B57",
        "accent2": "D9B89C",
        "background": "F5F0E9",
        "surface": "FFFCF8",
        "text": "211E1B",
        "muted": "746B64",
    },
    "midnight": {
        "primary": "101827",
        "secondary": "263B59",
        "accent": "E0B863",
        "accent2": "73A9B7",
        "background": "F4F6F8",
        "surface": "FFFFFF",
        "text": "172033",
        "muted": "667085",
    },
    "ocean": {
        "primary": "103C4A",
        "secondary": "257786",
        "accent": "D6A84A",
        "accent2": "63B2B0",
        "background": "F1F6F5",
        "surface": "FFFFFF",
        "text": "14292E",
        "muted": "62777A",
    },
}

_VIDEO_STYLES = {
    "cinematic": {
        "label": "Cinematic",
        "theme": {
            "primary": "365CF5",
            "secondary": "19A7A0",
            "accent": "FFBF47",
            "background": "07111F",
            "surface": "101D31",
            "text": "FFFFFF",
            "muted": "D6E0EE",
        },
        "grade": "cinematic",
    },
    "editorial": {
        "label": "Editorial",
        "theme": {
            "primary": "C85D3B",
            "secondary": "3B7183",
            "accent": "E6C36A",
            "background": "171514",
            "surface": "2A2522",
            "text": "FFFFFF",
            "muted": "E7DED0",
        },
        "grade": "warm",
    },
    "neon": {
        "label": "Neon Technology",
        "theme": {
            "primary": "7C5CFC",
            "secondary": "06D6A0",
            "accent": "41EAD4",
            "background": "080B19",
            "surface": "121A38",
            "text": "FFFFFF",
            "muted": "C7D2FE",
        },
        "grade": "vibrant",
    },
    "ocean": {
        "label": "Ocean Modern",
        "theme": {
            "primary": "0EA5E9",
            "secondary": "14B8A6",
            "accent": "FDE047",
            "background": "071B2B",
            "surface": "0D2B3E",
            "text": "FFFFFF",
            "muted": "D1EAF5",
        },
        "grade": "cool",
    },
    "warm": {
        "label": "Warm Documentary",
        "theme": {
            "primary": "D97757",
            "secondary": "6B8E7D",
            "accent": "F2C14E",
            "background": "171514",
            "surface": "2B2521",
            "text": "FFFFFF",
            "muted": "E9DED5",
        },
        "grade": "warm",
    },
    "mono": {
        "label": "Monochrome Premium",
        "theme": {
            "primary": "D9D9D9",
            "secondary": "8E9AAF",
            "accent": "FFFFFF",
            "background": "0B0B0C",
            "surface": "1C1C1F",
            "text": "FFFFFF",
            "muted": "C8C8CC",
        },
        "grade": "mono",
    },
}

_PROFILE_RULES = (
    (
        "technology",
        re.compile(
            r"(?:هوش|فناوری|تکنولوژی|دیجیتال|نرم.?افزار|ربات|داده|\bai\b|"
            r"technology|software|digital|robot|data)",
            re.I,
        ),
    ),
    ("education", re.compile(r"(?:آموزش|یادگیری|دانشجو|درس|راهنما|learn|education|student)", re.I)),
    (
        "business",
        re.compile(r"(?:کسب.?وکار|فروش|بازار|مشتری|مدیر|محصول|business|market|customer)", re.I),
    ),
    (
        "social",
        re.compile(
            r"(?:شبکه اجتماعی|اینستاگرام|موبایل|جامعه|social media|instagram|"
            r"mobile|society)",
            re.I,
        ),
    ),
    (
        "inspirational",
        re.compile(r"(?:انگیزه|رشد فردی|عادت|موفقیت|امید|motivation|habit|success|hope)", re.I),
    ),
    (
        "documentary",
        re.compile(r"(?:تاریخ|جامعه|بحران|محیط|واقعیت|بررسی|history|climate|crisis)", re.I),
    ),
)

_PROFILE_DESIGN = {
    "technology": ("midnight", "neon", "dynamic"),
    "education": ("ocean", "ocean", "balanced"),
    "business": ("warm_neutral", "editorial", "balanced"),
    "social": ("warm_neutral", "editorial", "dynamic"),
    "inspirational": ("forest_cream", "cinematic", "balanced"),
    "documentary": ("warm_neutral", "warm", "balanced"),
    "general": ("forest_cream", "cinematic", "balanced"),
}

_PROFILE_VIDEO_MUSIC = {
    "technology": ("ambient", ["calm", "focused", "modern"], ["soft synthesizer", "piano", "pads"]),
    "education": ("ambient", ["calm", "focused", "gentle"], ["piano", "pads"]),
    "documentary": ("ambient", ["thoughtful", "calm", "warm"], ["piano", "soft strings", "pads"]),
    "social": (
        "lounge",
        ["calm", "modern", "focused"],
        ["soft synthesizer", "light piano", "pads"],
    ),
    "inspirational": ("ambient", ["hopeful", "calm", "gentle"], ["piano", "soft strings", "pads"]),
    "business": ("lounge", ["calm", "professional", "focused"], ["piano", "soft guitar", "pads"]),
    "general": ("ambient", ["calm", "gentle", "focused"], ["piano", "pads"]),
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _profile(plan: Mapping[str, Any]) -> str:
    slides = plan.get("slides") or plan.get("scenes") or []
    text = " ".join(
        [
            _clean(plan.get("title")),
            _clean(plan.get("subtitle")),
            *(
                f"{_clean(item.get('title'))} {_clean(item.get('body'))} "
                f"{_clean(item.get('on_screen_text'))}"
                for item in slides
                if isinstance(item, Mapping)
            ),
        ]
    )
    for name, pattern in _PROFILE_RULES:
        if pattern.search(text):
            return name
    return "general"


def _rng(seed: str, title: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}|{title}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _hex(value: Any, fallback: str) -> str:
    candidate = _clean(value).removeprefix("#").upper()
    return candidate if re.fullmatch(r"[0-9A-F]{6}", candidate) else fallback


def _theme(raw: Any, fallback: Mapping[str, str]) -> dict[str, str]:
    source = raw if isinstance(raw, Mapping) else {}
    return {key: _hex(source.get(key), value) for key, value in fallback.items()}


def _items(slide: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_items = slide.get("items") if isinstance(slide.get("items"), list) else []
    output = []
    for index, item in enumerate(raw_items[:6], start=1):
        if isinstance(item, Mapping):
            output.append(
                {
                    "icon": (
                        _clean(item.get("icon"))
                        if _clean(item.get("icon")) in ICON_NAMES
                        else ICON_NAMES[index % len(ICON_NAMES)]
                    ),
                    "value": _clean(item.get("value")),
                    "title": _clean(item.get("title")) or f"Point {index}",
                    "body": _clean(item.get("body")),
                    "tone": _clean(item.get("tone")),
                }
            )
        elif _clean(item):
            output.append({"icon": "sparkles", "value": "", "title": _clean(item), "body": ""})
    if not output:
        for index, value in enumerate(slide.get("bullets") or [], start=1):
            if _clean(value):
                output.append(
                    {
                        "icon": "sparkles",
                        "value": f"{index:02d}",
                        "title": _clean(value),
                        "body": "",
                    }
                )
    if not output:
        for metric in slide.get("metrics") or []:
            if isinstance(metric, Mapping):
                output.append(
                    {
                        "icon": "chart",
                        "value": _clean(metric.get("value")),
                        "title": _clean(metric.get("label")),
                        "body": "",
                    }
                )
    return output[:6]


def _normalize_item_list(value: Any, *, maximum: int = 6) -> list[dict[str, str]]:
    return _items({"items": value})[:maximum]


_PLACEHOLDER_ITEM_TITLES = frozenset(
    {
        "context",
        "evidence",
        "decision",
        "action",
        "current approach",
        "constraint",
        "risk",
        "improved approach",
        "opportunity",
        "outcome",
    }
)


def _without_placeholders(items: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Keep authored content only; never manufacture cards to satisfy a layout."""

    output: list[dict[str, str]] = []
    for item in items:
        candidate = dict(item)
        title = _clean(candidate.get("title"))
        body = _clean(candidate.get("body"))
        value = _clean(candidate.get("value"))
        if not title and not body and not value:
            continue
        if title and not body and not value:
            continue
        if (
            (
                title.casefold() in _PLACEHOLDER_ITEM_TITLES
                or re.fullmatch(r"point\s+\d+", title, re.I)
            )
            and not body
            and (not value or bool(re.fullmatch(r"[0-9\u0660-\u0669\u06f0-\u06f9]{1,3}", value)))
        ):
            continue
        output.append(candidate)
    return output


def _is_rtl_text(*values: Any) -> bool:
    text = " ".join(_clean(value) for value in values)
    rtl = len(re.findall(r"[\u0590-\u08ff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return rtl > latin


def _rtl_items(items: Sequence[dict[str, str]], layout: str) -> list[dict[str, str]]:
    """Map logical RTL order to the renderer's left-to-right placement order."""

    output = [dict(item) for item in items]
    if len(output) < 2:
        return output
    if layout in {
        "process_flow",
        "timeline_story",
        "closing_actions",
        "funnel",
        "metrics_dashboard",
        "dashboard",
    }:
        return list(reversed(output))
    if layout == "cards_icon":
        columns = 2 if len(output) == 4 else min(3, len(output))
        return [
            item
            for start in range(0, len(output), columns)
            for item in reversed(output[start : start + columns])
        ]
    return output


_PHOTO_LAYOUT_COUNTS = {
    "cover_cinematic": 1,
    "editorial_split": 1,
    "photo_mosaic": 3,
    "quote_photo": 1,
    "case_study": 2,
}

_MEDIA_QUERY_STOPWORDS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "source",
        "sources",
        "citation",
        "citations",
        "reference",
        "references",
        "people",
        "research",
        "workspace",
    }
)


def _sanitize_media_query(value: Any) -> str:
    query = _clean(value)
    if not query:
        return ""
    query = re.sub(
        r"[\[(]\s*(?:s(?:ource)?|src|ref(?:erence)?|citation)?\s*[-:#]?\s*\d+\s*[\])]",
        " ",
        query,
        flags=re.I,
    )
    query = re.sub(
        r"\b(?:s|src|source|ref(?:erence)?|citation)\s*[-:#]?\s*\d+\b|\b\d+\s*s\b",
        " ",
        query,
        flags=re.I,
    )
    query = re.sub(r"[\[\](){}]", " ", query)
    query = re.sub(r"\s+", " ", query).strip(" ,;:|-/")
    if not query:
        return ""
    latin_tokens = re.findall(r"[A-Za-z][A-Za-z0-9+.-]*", query)
    if latin_tokens and not any(
        token.casefold() not in _MEDIA_QUERY_STOPWORDS for token in latin_tokens
    ):
        return ""
    return query


def _normalized_chart(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if bool(value.get("illustrative", False)):
        return None
    labels = [_clean(item)[:28] for item in value.get("labels") or [] if _clean(item)][:10]
    if len(labels) < 2:
        return None
    raw_series = value.get("series") if isinstance(value.get("series"), list) else []
    if not raw_series and isinstance(value.get("values"), list):
        raw_series = [{"name": value.get("label") or "Value", "values": value["values"]}]
    series = []
    for position, entry in enumerate(raw_series[:3], start=1):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("values"), list):
            continue
        values = entry["values"][: len(labels)]
        if len(values) != len(labels) or any(isinstance(number, bool) for number in values):
            continue
        try:
            numeric = [float(number) for number in values]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(number) for number in numeric):
            continue
        series.append(
            {"name": _clean(entry.get("name")) or f"Series {position}", "values": numeric}
        )
    if not series:
        return None
    title = _clean(value.get("title"))[:72]
    conceptual_signature = [label.casefold() for label in labels] == [
        "context",
        "evidence",
        "decision",
    ] and any(entry["values"] == [1, 2, 3] for entry in series)
    if title.casefold() == "conceptual decision path" or conceptual_signature:
        return None
    kind = _clean(value.get("type")).casefold()
    return {
        "type": kind if kind in {"bar", "line", "doughnut", "area", "column", "pie"} else "bar",
        "title": title,
        "labels": labels,
        "series": series,
        "illustrative": False,
    }


def _valid_chart(value: Any) -> bool:
    return _normalized_chart(value) is not None


def _presentation_layout(slide: Mapping[str, Any], index: int, total: int) -> str:
    if index == 0:
        return "cover_cinematic"
    if index == total - 1:
        return "closing_actions"
    explicit = _LEGACY_PRESENTATION_LAYOUTS.get(
        _clean(slide.get("layout")).casefold(), _clean(slide.get("layout")).casefold()
    )
    if explicit in PRESENTATION_LAYOUTS:
        return explicit
    title = f"{_clean(slide.get('title'))} {_clean(slide.get('subtitle'))}".casefold()
    if _valid_chart(slide.get("chart")):
        return "dashboard" if slide.get("metrics") else "chart_focus"
    if (
        slide.get("left_items")
        or slide.get("right_items")
        or re.search(r"(?:مقایسه|در برابر|تفاوت|\bvs\b|compare)", title)
    ):
        return "comparison_dual"
    if re.search(r"(?:خط زمانی|تاریخچه|timeline|chronolog)", title):
        return "timeline_story"
    if re.search(r"(?:فرایند|مراحل|گام|workflow|process|steps)", title):
        return "process_flow"
    if slide.get("quote"):
        return "quote_photo"
    if slide.get("metrics"):
        return "metrics_dashboard"
    if slide.get("rows"):
        return "table_clean"
    queries = slide.get("image_queries") or []
    if isinstance(queries, list) and len([item for item in queries if _clean(item)]) >= 3:
        return "photo_mosaic"
    if re.search(r"(?:قیف|funnel|conversion)", title):
        return "funnel"
    if re.search(r"(?:مطالعه موردی|قبل و بعد|case study|before.*after)", title):
        return "case_study"
    items = _items(slide)
    if len(items) == 4 and re.search(r"(?:اولویت|ماتریس|matrix|priority)", title):
        return "matrix_2x2"
    if not items and len(_clean(slide.get("body"))) < 80:
        return "big_statement"
    if slide.get("image_query") or queries:
        return "editorial_split"
    return "cards_icon"


def _alternative_layout(slide: Mapping[str, Any], selected: str) -> str:
    # Variety must never force a layout whose semantic dependencies are absent.
    if selected == "cards_icon" and (slide.get("image_query") or slide.get("image_queries")):
        return "editorial_split"
    if selected == "big_statement" and (slide.get("image_query") or slide.get("image_queries")):
        return "editorial_split"
    if selected == "dashboard" and _valid_chart(slide.get("chart")):
        return "chart_focus"
    return selected


def _fallback_layout(slide: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    if len(items) >= 2:
        return "cards_icon"
    if slide.get("image_query") or slide.get("image_queries"):
        return "editorial_split"
    return "big_statement"


def _content_safe_layout(
    slide: Mapping[str, Any],
    layout: str,
    *,
    items: Sequence[Mapping[str, Any]],
    left_items: Sequence[Mapping[str, Any]],
    right_items: Sequence[Mapping[str, Any]],
    chart: Mapping[str, Any] | None,
) -> str:
    """Reflow a slide instead of inventing content for a demanding layout."""

    raw_queries = slide.get("image_queries") or slide.get("imageQueries") or []
    if not isinstance(raw_queries, list):
        raw_queries = [raw_queries]
    query_count = len(
        {
            query
            for query in (
                _sanitize_media_query(value)
                for value in [
                    *raw_queries,
                    slide.get("image_query"),
                    slide.get("imageQuery"),
                ]
            )
            if query
        }
    )
    needed_media = _PHOTO_LAYOUT_COUNTS.get(layout, 0)
    if needed_media and query_count < needed_media:
        return _fallback_layout(slide, items)

    if layout in {"chart_focus", "dashboard"} and chart is None:
        if len(items) >= 2 and slide.get("metrics"):
            return "metrics_dashboard"
        return _fallback_layout(slide, items)
    if layout == "dashboard" and len(items) < 2:
        return "chart_focus"
    if layout == "metrics_dashboard" and len(items) < 2:
        return "chart_focus" if chart is not None else _fallback_layout(slide, items)
    if layout == "matrix_2x2" and len(items) != 4:
        return _fallback_layout(slide, items)
    if layout == "comparison_dual" and (not left_items or not right_items):
        return _fallback_layout(slide, [*left_items, *right_items, *items])
    if layout in {"process_flow", "timeline_story", "funnel"} and len(items) < 2:
        return _fallback_layout(slide, items)
    if layout == "case_study" and len(items) < 2:
        return _fallback_layout(slide, items)
    if layout == "cards_icon" and not items:
        return _fallback_layout(slide, items)
    return layout


def direct_presentation(
    plan: Mapping[str, Any], options: Mapping[str, Any], *, seed: str = ""
) -> dict[str, Any]:
    """Turn an LLM-authored narrative into a safe, varied, editable deck specification."""

    directed = deepcopy(dict(plan))
    slides = [
        deepcopy(dict(item)) for item in plan.get("slides") or [] if isinstance(item, Mapping)
    ]
    if not slides:
        return directed
    profile = _profile(plan)
    automatic_theme, automatic_style, _ = _PROFILE_DESIGN[profile]
    requested_theme = _clean(options.get("color_theme")).casefold()
    selected_theme = requested_theme if requested_theme in _PRESENTATION_THEMES else automatic_theme
    fallback_theme = _PRESENTATION_THEMES[selected_theme]
    raw_theme = plan.get("theme") if isinstance(plan.get("theme"), Mapping) else {}
    image_treatment = _clean(
        raw_theme.get("image_treatment") or raw_theme.get("imageTreatment")
    ).casefold()
    radius = _clean(raw_theme.get("radius")).casefold()
    directed["theme"] = {
        **_theme(raw_theme, fallback_theme),
        "preset": _clean(raw_theme.get("preset")) or selected_theme,
        "image_treatment": (
            image_treatment
            if image_treatment in {"cinematic", "natural", "duotone", "clean"}
            else "cinematic"
        ),
        "radius": radius if radius in {"none", "small", "medium", "large"} else "large",
    }
    requested_style = _clean(options.get("design_style")).casefold()
    style = requested_style if requested_style and requested_style != "auto" else automatic_style
    previous = ""
    counts: dict[str, int] = {}
    for index, slide in enumerate(slides):
        items = _without_placeholders(_items(slide))
        left_items = _without_placeholders(
            _normalize_item_list(slide.get("left_items") or slide.get("leftItems"), maximum=5)
        )
        right_items = _without_placeholders(
            _normalize_item_list(slide.get("right_items") or slide.get("rightItems"), maximum=5)
        )
        chart = _normalized_chart(slide.get("chart"))
        rtl = _is_rtl_text(
            plan.get("title"),
            plan.get("subtitle"),
            slide.get("title"),
            slide.get("subtitle"),
            slide.get("body"),
        )
        layout = _presentation_layout(slide, index, len(slides))
        if layout == "comparison_dual" and not left_items and not right_items and len(items) >= 2:
            split_at = (len(items) + 1) // 2
            first, second = items[:split_at], items[split_at:]
            if rtl:
                right_items, left_items = first, second
            else:
                left_items, right_items = first, second
            items = []
        layout = _content_safe_layout(
            slide,
            layout,
            items=items,
            left_items=left_items,
            right_items=right_items,
            chart=chart,
        )
        if index not in {0, len(slides) - 1} and (layout == previous or counts.get(layout, 0) >= 2):
            alternative = _alternative_layout(slide, layout)
            layout = _content_safe_layout(
                slide,
                alternative,
                items=items,
                left_items=left_items,
                right_items=right_items,
                chart=chart,
            )
        if layout != "comparison_dual" and (left_items or right_items):
            items = _without_placeholders([*items, *left_items, *right_items])[:6]
            left_items = []
            right_items = []
        if rtl:
            items = _rtl_items(items, layout)
        previous = layout
        counts[layout] = counts.get(layout, 0) + 1
        if layout in {"cover_cinematic", "photo_mosaic", "quote_photo", "case_study"}:
            mode = "photo"
        elif layout in {"comparison_dual", "process_flow", "closing_actions"}:
            mode = "dark"
        elif layout in {"timeline_story", "big_statement", "funnel"} and index % 2:
            mode = "accent"
        else:
            requested_mode = _clean(slide.get("mode")).casefold()
            mode = (
                requested_mode
                if requested_mode in {"light", "dark", "accent", "photo"}
                else "light"
            )
        footnote = _clean(slide.get("footnote"))
        raw_queries = slide.get("image_queries") or slide.get("imageQueries") or []
        if not isinstance(raw_queries, list):
            raw_queries = [raw_queries]
        queries = list(
            dict.fromkeys(
                _sanitize_media_query(item)
                for item in [
                    *raw_queries,
                    slide.get("image_query"),
                    slide.get("imageQuery"),
                ]
                if _sanitize_media_query(item)
            )
        )[:3]
        slide.update(
            {
                "layout": layout,
                "mode": mode,
                "reading_direction": "rtl" if rtl else "ltr",
                "kicker": _clean(slide.get("kicker")) or f"{index + 1:02d}",
                "items": items,
                "left_items": left_items,
                "right_items": right_items,
                "left_title": _clean(slide.get("left_title") or slide.get("leftTitle")),
                "right_title": _clean(slide.get("right_title") or slide.get("rightTitle")),
                "chart": chart or {},
                "footnote": footnote,
                "image_queries": queries,
                "image_query": queries[0] if queries else "",
                "focal_point": _clean(slide.get("focal_point") or slide.get("focalPoint"))
                or "center",
            }
        )
    directed["slides"] = slides
    directed["direction"] = {
        "contract_version": "paperlens.visual-plan.v1",
        "version": "5.0-paperlens",
        "mode": "autonomous" if options.get("design_control", "auto") == "auto" else "custom",
        "seed": seed,
        "profile": profile,
        "style": style,
        "theme": selected_theme,
        "layout_count": len(set(item["layout"] for item in slides)),
        "render_engine": "sourcelens-deck-renderer",
    }
    return directed


def _video_intent(scene: Mapping[str, Any], index: int, total: int) -> str:
    explicit = _clean(scene.get("visual_intent") or scene.get("intent")).casefold()
    allowed = {"footage", "data", "process", "comparison", "timeline", "quote", "statement"}
    if explicit in allowed:
        return explicit
    for intent in allowed - {"footage"}:
        if re.search(rf"\b{re.escape(intent)}\b", explicit):
            return intent
    text = f"{_clean(scene.get('title'))} {_clean(scene.get('on_screen_text'))}".casefold()
    if index in {0, total - 1}:
        return "statement"
    rules = (
        ("comparison", r"(?:مقایسه|در برابر|تفاوت|\bvs\b|compare)"),
        ("process", r"(?:مرحله|گام|فرایند|workflow|process)"),
        ("data", r"(?:درصد|آمار|رشد|کاهش|افزایش|data|metric|percent)"),
        ("timeline", r"(?:خط زمانی|گذشته|امروز|آینده|timeline|year)"),
        ("quote", r"(?:نقل قول|باور|اصل|quote)"),
    )
    for intent, pattern in rules:
        if re.search(pattern, text):
            return intent
    return "footage"


def _video_layout(intent: str, index: int, total: int, rng: random.Random) -> str:
    if index == 0:
        return "hero_cinematic"
    if index == total - 1:
        return "closing_statement"
    semantic = {
        "data": "data_bars",
        "process": "process",
        "comparison": "comparison",
        "timeline": "timeline",
        "quote": "quote",
    }
    if intent in semantic:
        return semantic[intent]
    return rng.choice(
        ("lower_third", "split_panel", "glass_card", "spotlight", "editorial", "minimal_type")
    )


def _parts(value: Any, fallback: Sequence[str]) -> list[str]:
    parts = [part for part in re.split(r"[،,:؛•|]", _clean(value)) if _clean(part)]
    return (parts if len(parts) >= 2 else list(fallback))[:3]


def _video_icon(scene: Mapping[str, Any]) -> str:
    explicit = _clean(scene.get("icon"))
    if explicit in ICON_NAMES:
        return explicit
    text = f"{_clean(scene.get('title'))} {_clean(scene.get('on_screen_text'))}".casefold()
    rules = (
        (r"(?:هوش|مصنوعی|\bai\b|machine)", "brain"),
        (r"(?:رشد|افزایش|پیشرفت|نتیجه|trend)", "trend"),
        (r"(?:داده|آمار|تحلیل|نمودار|data|metric)", "chart"),
        (r"(?:زمان|سرعت|آینده|time|future)", "clock"),
        (r"(?:هدف|تمرکز|اولویت|focus|target)", "target"),
        (r"(?:امنیت|اعتماد|ریسک|security|risk)", "shield"),
        (r"(?:تیم|افراد|همکاری|team|people)", "users"),
        (r"(?:شروع|اقدام|اجرا|start|launch)", "rocket"),
        (r"(?:فرایند|سیستم|تنظیم|process|system)", "gear"),
        (r"(?:ارتباط|شبکه|اتصال|network|link)", "link"),
        (r"(?:موبایل|گوشی|mobile|phone)", "mobile"),
        (r"(?:جستجو|کشف|search|discover)", "search"),
        (r"(?:تأیید|موفق|جمع.?بندی|success|complete)", "check"),
    )
    return next((icon for pattern, icon in rules if re.search(pattern, text, re.I)), "sparkles")


def _normalized_video_chart(value: Any) -> dict[str, Any] | None:
    chart = _normalized_chart(value)
    if chart is None:
        return None
    return {
        "type": chart["type"],
        "title": chart["title"],
        "labels": chart["labels"],
        "values": chart["series"][0]["values"],
        "illustrative": False,
    }


def _typing_direction(
    scene: Mapping[str, Any],
    *,
    index: int,
    total: int,
    intent: str,
    energy: str,
    rng: random.Random,
) -> dict[str, Any]:
    length = len(_clean(scene.get("title")))
    candidate = (
        index in {0, total - 1}
        or intent in {"quote", "statement"}
        or bool(re.search(r"[؟?!]", _clean(scene.get("title"))))
    )
    probability = {"dynamic": 0.48, "calm": 0.28, "balanced": 0.38}[energy]
    enabled = length <= 42 and (candidate or rng.random() < probability * 0.45)
    base_cps = {"dynamic": 8.5, "calm": 6.2, "balanced": 7.2}[energy]
    return {
        "enabled": enabled,
        "target": "title",
        "cps": round(base_cps + rng.random() * 1.5, 2),
        "variance": 0.2,
        "cursor": True,
        "cursor_hold": 0.34,
        "cursor_blink": False,
        "initial_delay": 0.22 if index == 0 else 0.14,
    }


def direct_video(
    plan: Mapping[str, Any], options: Mapping[str, Any], *, seed: str = ""
) -> dict[str, Any]:
    """Apply SourceLens video direction to a source-grounded plan."""

    directed = deepcopy(dict(plan))
    scenes = [
        deepcopy(dict(item)) for item in plan.get("scenes") or [] if isinstance(item, Mapping)
    ]
    if not scenes:
        return directed
    profile = _profile(plan)
    _, automatic_style, automatic_energy = _PROFILE_DESIGN[profile]
    aliases = {
        "documentary": "warm",
        "academic": "ocean",
        "minimal": "mono",
        "data_story": "neon",
    }
    requested_style = aliases.get(
        _clean(options.get("video_style")).casefold(),
        _clean(options.get("video_style")).casefold(),
    )
    style_key = requested_style if requested_style in _VIDEO_STYLES else automatic_style
    style = _VIDEO_STYLES[style_key]
    requested_energy = _clean(options.get("pace")).casefold()
    energy = (
        requested_energy
        if requested_energy in {"calm", "balanced", "dynamic"}
        else automatic_energy
    )
    rng = _rng(seed, _clean(plan.get("title")))
    natural_transitions = ["fade", "dissolve", "smoothleft", "smoothright", "smoothup", "hblur"]
    expressive_transitions = ["zoomin", "circleopen", "revealleft", "revealup", "fadewhite"]
    treatments = ["clean", "cinematic", "vibrant", "mono", "soft", "warm", "cool"]
    transition_request = _clean(options.get("transition_style")).casefold()
    media_mode = _clean(options.get("media_mode")).casefold()
    previous_layout = ""
    previous_transition = ""
    layout_counts: dict[str, int] = {}
    for index, scene in enumerate(scenes):
        intent = _video_intent(scene, index, len(scenes))
        layout = _video_layout(intent, index, len(scenes), rng)
        video_chart = _normalized_video_chart(scene.get("chart"))
        if layout == "data_bars" and video_chart is None:
            layout = "editorial"
        if layout == previous_layout or (
            layout_counts.get(layout, 0) >= 2 and index not in {0, len(scenes) - 1}
        ):
            alternatives = [
                value
                for value in (
                    "lower_third",
                    "split_panel",
                    "glass_card",
                    "spotlight",
                    "editorial",
                    "minimal_type",
                )
                if value != previous_layout and layout_counts.get(value, 0) < 2
            ]
            if alternatives:
                layout = rng.choice(alternatives)
        previous_layout = layout
        layout_counts[layout] = layout_counts.get(layout, 0) + 1
        if transition_request and transition_request not in {"auto", "dynamic"}:
            transition = "smoothleft" if transition_request == "slide" else transition_request
        else:
            transition_pool = list(natural_transitions)
            if energy == "dynamic" and intent != "quote" and rng.random() > 0.62:
                transition_pool.extend(expressive_transitions)
            transition = rng.choice(
                [value for value in transition_pool if value != previous_transition]
                or transition_pool
            )
        previous_transition = transition
        typing = _typing_direction(
            scene,
            index=index,
            total=len(scenes),
            intent=intent,
            energy=energy,
            rng=rng,
        )
        if typing["enabled"]:
            motion = "type_reveal"
        elif layout in {"data_bars", "process", "comparison", "timeline"}:
            motion = "reveal_up"
        elif layout == "hero_cinematic":
            motion = "scale_in" if energy != "calm" else "fade"
        elif layout == "quote":
            motion = "float_right"
        else:
            motion = rng.choice(("fade", "slide_up", "slide_left", "reveal_up"))
        raw_queries = scene.get("media_queries") or []
        if not isinstance(raw_queries, list):
            raw_queries = []
        queries = list(
            dict.fromkeys(
                _sanitize_media_query(item)
                for item in [
                    *raw_queries,
                    scene.get("media_query"),
                ]
                if _sanitize_media_query(item)
            )
        )[:8]
        preference = (
            "card"
            if media_mode == "motion_graphics"
            else "photo"
            if media_mode == "photo_first"
            else "video"
        )
        policy = (
            ["card"]
            if preference == "card"
            else ["photo", "video", "card"]
            if preference == "photo"
            else ["video", "photo", "card"]
        )
        scene["media_queries"] = queries
        scene["media_query"] = queries[0] if queries else ""
        scene["media_preference"] = preference
        scene["media_policy"] = policy
        scene["focal_point"] = (
            _clean(scene.get("focal_point") or scene.get("focalPoint")) or "center"
        )
        scene["visual_intent"] = intent
        scene["icon"] = _video_icon(scene)
        scene["visual"] = {
            **(scene.get("visual") if isinstance(scene.get("visual"), Mapping) else {}),
            "intent": intent,
            "layout": layout,
            "motion": motion,
            "transition": transition,
            "transition_duration": {"calm": 0.62, "balanced": 0.52, "dynamic": 0.42}[energy],
            "treatment": (
                "mono" if style["grade"] == "mono" else rng.choice([style["grade"], *treatments])
            ),
            "icon": scene["icon"],
            "typing": typing,
            "chart": video_chart,
            "steps": _parts(scene.get("on_screen_text"), ("Observe", "Test", "Improve"))
            if intent == "process"
            else None,
            "comparison": _parts(scene.get("on_screen_text"), ("Before", "After"))[:2]
            if intent == "comparison"
            else None,
            "timeline": _parts(scene.get("on_screen_text"), ("Past", "Today", "Next"))
            if intent == "timeline"
            else None,
            "number": f"{index + 1:02d}",
            "decorative_seed": hashlib.sha256(
                f"{plan.get('title')}|{seed}|{index}".encode()
            ).hexdigest()[:10],
        }
    directed["scenes"] = scenes
    directed["theme"] = _theme(plan.get("theme"), style["theme"])
    genre, moods, instruments = _PROFILE_VIDEO_MUSIC[profile]
    directed["direction"] = {
        "contract_version": "paperlens.visual-plan.v1",
        "version": "7.0.4-paperlens",
        "mode": "autonomous" if options.get("design_control", "auto") == "auto" else "custom",
        "seed": seed,
        "profile": profile,
        "style": style_key,
        "style_label": style["label"],
        "energy": energy,
        "media_policy": "video-first",
        "render_engine": "sourcelens-video-renderer",
        "music": {
            "genre": genre,
            "moods": moods,
            "instruments": instruments,
            "speed": ["low", "medium"] if energy != "calm" else ["low"],
            "instrumental": True,
            "purpose": "voiceover-bed",
            "foreground": False,
            "rhythm_density": "low",
            "avoid": [
                "vocals",
                "gloomy",
                "dark",
                "dramatic",
                "epic",
                "aggressive",
                "heavy percussion",
            ],
            "energy_curve": "low-steady-soft-resolve",
        },
    }
    return directed
