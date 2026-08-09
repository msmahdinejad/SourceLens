"""Pure, evidence-first planning for autonomous slide decks.

This module owns the planning decisions that should happen before presentation
normalization or rendering.  Its single public operation, :func:`plan_deck`,
turns grounded evidence into a variable-length deck plan.  It never pads a
deck, invents a chart, or treats an unresolved media idea as an asset.

The module is deliberately side-effect free.  API-backed media is represented
by a verified :class:`MediaAsset`; missing media becomes a
:class:`MediaRequest` for an adapter to resolve before replanning.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse


class DeckPlanningError(ValueError):
    """Raised when no grounded deck can be planned from the supplied evidence."""


class PlanningMode(StrEnum):
    AUTONOMOUS = "autonomous"
    GUIDED = "guided"


class ContentDensity(StrEnum):
    CONCISE = "concise"
    BALANCED = "balanced"
    DEEP = "deep"


class MediaDensity(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    BALANCED = "balanced"
    RICH = "rich"


class EvidenceKind(StrEnum):
    CONTEXT = "context"
    CONCEPT = "concept"
    INSIGHT = "insight"
    PROCESS = "process"
    COMPARISON = "comparison"
    METRIC = "metric"
    TIMELINE = "timeline"
    CASE_STUDY = "case_study"
    QUOTE = "quote"
    LIMITATION = "limitation"
    ACTION = "action"
    SYNTHESIS = "synthesis"


@dataclass(frozen=True, slots=True)
class ChartSeries:
    """One numeric series copied from cited source evidence."""

    name: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ChartEvidence:
    """A chart candidate whose values and labels are traceable to citations."""

    chart_type: str
    title: str
    labels: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    citations: tuple[str, ...]
    unit: str = ""
    illustrative: bool = False


@dataclass(frozen=True, slots=True)
class MediaAsset:
    """A real asset returned by a remote media or source-extraction API.

    ``verified`` means the adapter has checked the API response and retained
    both the downloadable asset URL and the public provenance URL.  Local,
    generated, mock, and placeholder assets deliberately fail the planner's
    media gate.
    """

    provider: str
    asset_id: str
    download_url: str
    source_url: str
    mime_type: str
    origin: str = "api"
    verified: bool = False
    attribution: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceUnit:
    """A single presentation-worthy, cited claim or observation."""

    evidence_id: str
    topic: str
    claim: str
    citations: tuple[str, ...]
    kind: EvidenceKind = EvidenceKind.INSIGHT
    importance: float = 0.5
    detail: str = ""
    example: str = ""
    chart: ChartEvidence | None = None
    media_candidates: tuple[MediaAsset, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaRequest:
    """A requirement for an external API adapter, not a renderable asset."""

    query: str
    asset_types: tuple[str, ...]
    provider_policy: tuple[str, ...]
    require_api: bool = True
    allow_generated: bool = False


@dataclass(frozen=True, slots=True)
class SlidePlan:
    """A semantic slide job with only grounded content dependencies."""

    slide_id: str
    role: EvidenceKind
    title: str
    objective: str
    layout: str
    evidence_ids: tuple[str, ...]
    citations: tuple[str, ...]
    content_density: ContentDensity
    chart: ChartEvidence | None = None
    media: MediaAsset | None = None
    media_request: MediaRequest | None = None


@dataclass(frozen=True, slots=True)
class PlanWarning:
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeckPlan:
    """The complete variable-length plan returned through the module interface."""

    title: str
    mode: PlanningMode
    content_density: ContentDensity
    media_density: MediaDensity
    slides: tuple[SlidePlan, ...]
    warnings: tuple[PlanWarning, ...] = ()
    rejected_evidence_ids: tuple[str, ...] = ()

    @property
    def slide_count(self) -> int:
        return len(self.slides)


@dataclass(frozen=True, slots=True)
class _Preferences:
    mode: PlanningMode
    content_density: ContentDensity
    media_density: MediaDensity
    target_slides: int | None
    include_charts: bool
    language: str
    title: str
    image_strategy: str


@dataclass(slots=True)
class _Candidate:
    unit: EvidenceUnit
    source_index: int
    chart: ChartEvidence | None
    media: tuple[MediaAsset, ...]


@dataclass(slots=True)
class _Group:
    topic: str
    role: EvidenceKind
    candidates: list[_Candidate] = field(default_factory=list)

    @property
    def importance(self) -> float:
        return max(item.unit.importance for item in self.candidates)


_PLACEHOLDERS = {
    "action",
    "context",
    "decision",
    "evidence",
    "item",
    "placeholder",
    "point",
    "tbd",
    "todo",
    "اقدام",
    "تصمیم",
    "زمینه",
    "شاهد",
    "مورد",
}
_ALLOWED_CHART_TYPES = {"bar", "line", "pie", "scatter"}
_BLOCKED_MEDIA_PROVIDERS = {"fake", "generated", "local", "mock", "placeholder"}
_MAX_SLIDES = 40
_CITATION_TOKEN = re.compile(r"\[(?:S|E)\d+(?:\s*,\s*(?:S|E)?\d+)*\]", re.IGNORECASE)
_BARE_SOURCE_TOKEN = re.compile(r"\b[SE]\d+\b", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SPACE = re.compile(r"\s+")
_NON_NARRATIVE_TOPIC = re.compile(
    r"(?:^|\b)(?:"
    r"author(?:s)?|reference(?:s)?|bibliography|citation(?: information)?|"
    r"front matter|data availability(?: statement)?|acknowledg(?:e)?ments?|"
    r"supplementary (?:material|information)|conflict of interest|"
    r"funding statement|publication metadata|copyright|all rights reserved|"
    r"making sure you(?:'| a)re not a bot|captcha|"
    r"نویسندگان|منابع|ارجاعات|کتابنامه|دسترسی به داده(?:ها)?|"
    r"تعارض منافع|قدردانی|ضمیمه|پیوست|مطالب تکمیلی|ربات|کپچا"
    r")(?:\b|$)",
    re.IGNORECASE,
)


def plan_deck(
    evidence: Sequence[EvidenceUnit | Mapping[str, Any]],
    options: Mapping[str, Any] | None = None,
) -> DeckPlan:
    """Plan an evidence-grounded deck without padding to a requested count.

    ``options`` accepts the current Studio slide-deck keys.  In particular,
    ``design_control=auto`` activates autonomous mode and intentionally ignores
    the legacy default ``slide_count=12``.  In guided/custom mode ``slide_count``
    is a soft target: a shorter grounded plan is never padded, while a larger
    plan may exceed the target rather than discard important evidence.

    Mapping evidence uses these keys (aliases are accepted by the normalizer):
    ``id``, ``topic``, ``claim``/``text``, ``citations``, ``kind``, optional
    ``chart``, and optional ``media_candidates``.  A media candidate is usable
    only when it explicitly records ``origin=api``, ``verified=true``, an API
    provider/asset id, HTTPS download and provenance URLs, and an image/video
    MIME type.
    """

    raw_options = dict(options or {})
    normalized, warnings, rejected = _ground_evidence(evidence)
    if not normalized:
        raise DeckPlanningError("No cited, non-placeholder evidence is available for a deck")

    preferences = _preferences(raw_options, normalized)
    groups = _group_evidence(normalized, preferences.content_density)
    if preferences.mode is PlanningMode.AUTONOMOUS:
        groups, curation_warnings, curation_rejected = _curate_autonomous_groups(
            groups,
            preferences.content_density,
        )
        warnings.extend(curation_warnings)
        rejected.extend(curation_rejected)
    groups, capacity_warnings, capacity_rejected = _enforce_plan_capacity(groups)
    warnings.extend(capacity_warnings)
    rejected.extend(capacity_rejected)

    if preferences.mode is PlanningMode.GUIDED and preferences.target_slides is not None:
        required = 1 + len(groups) + (1 if len(groups) >= 3 else 0)
        if required > preferences.target_slides:
            warnings.append(
                PlanWarning(
                    code="soft_slide_target_exceeded",
                    message=(
                        f"The grounded narrative needs {required} slides; the requested "
                        f"target of {preferences.target_slides} was not enforced by dropping "
                        "evidence."
                    ),
                )
            )

    media_assignments, media_requests = _plan_media(groups, preferences)
    slides = _build_slides(groups, preferences, media_assignments, media_requests)
    if any(item.media_request for item in slides):
        warnings.append(
            PlanWarning(
                code="api_media_resolution_required",
                message=(
                    "Some semantic media slots have no verified API asset yet. They remain "
                    "requests and are not exposed to the renderer as fake media."
                ),
            )
        )

    plan = DeckPlan(
        title=preferences.title or _deck_title(normalized),
        mode=preferences.mode,
        content_density=preferences.content_density,
        media_density=preferences.media_density,
        slides=tuple(slides),
        warnings=tuple(_deduplicate_warnings(warnings)),
        rejected_evidence_ids=tuple(dict.fromkeys(rejected)),
    )
    _assert_plan_invariants(plan)
    return plan


def _ground_evidence(
    evidence: Sequence[EvidenceUnit | Mapping[str, Any]],
) -> tuple[list[_Candidate], list[PlanWarning], list[str]]:
    candidates: list[_Candidate] = []
    warnings: list[PlanWarning] = []
    rejected: list[str] = []
    claims_seen: set[str] = set()
    ids_seen: set[str] = set()

    for index, raw in enumerate(evidence, start=1):
        try:
            unit = _normalize_evidence(raw, index)
        except (TypeError, ValueError) as error:
            evidence_id = _raw_evidence_id(raw, index)
            rejected.append(evidence_id)
            warnings.append(
                PlanWarning("invalid_evidence_rejected", str(error), (evidence_id,))
            )
            continue

        if _is_non_narrative_evidence(unit):
            rejected.append(unit.evidence_id)
            warnings.append(
                PlanWarning(
                    "non_narrative_evidence_rejected",
                    "Document metadata, bibliography, or bot-check text cannot become a slide.",
                    (unit.evidence_id,),
                )
            )
            continue

        normalized_claim = _normalized_phrase(unit.claim)
        if unit.evidence_id in ids_seen or normalized_claim in claims_seen:
            rejected.append(unit.evidence_id)
            warnings.append(
                PlanWarning(
                    "duplicate_evidence_rejected",
                    "Duplicate evidence is not assigned another slide.",
                    (unit.evidence_id,),
                )
            )
            continue
        ids_seen.add(unit.evidence_id)
        claims_seen.add(normalized_claim)

        chart = unit.chart
        if chart is not None and not _valid_chart(chart, unit.citations):
            warnings.append(
                PlanWarning(
                    "ungrounded_chart_rejected",
                    "Chart data was removed because its numeric provenance is incomplete.",
                    (unit.evidence_id,),
                )
            )
            chart = None

        valid_media: list[MediaAsset] = []
        for media in unit.media_candidates:
            if _valid_media(media):
                valid_media.append(media)
            else:
                warnings.append(
                    PlanWarning(
                        "unverified_media_rejected",
                        "A media candidate was removed because it is not a verified API asset.",
                        (unit.evidence_id,),
                    )
                )
        candidates.append(_Candidate(unit, index, chart, tuple(valid_media)))

    return candidates, warnings, rejected


def _normalize_evidence(
    raw: EvidenceUnit | Mapping[str, Any], index: int
) -> EvidenceUnit:
    if isinstance(raw, EvidenceUnit):
        unit = raw
    elif isinstance(raw, Mapping):
        claim = _clean(raw.get("claim") or raw.get("text") or raw.get("body"))
        topic = _clean(raw.get("topic") or raw.get("title") or raw.get("section"))
        citations = _strings(raw.get("citations") or raw.get("citation"))
        evidence_id = _clean(raw.get("evidence_id") or raw.get("id") or raw.get("label"))
        kind = _kind(raw.get("kind") or raw.get("role_hint") or raw.get("type"), topic, claim)
        importance = _importance(raw.get("importance", raw.get("score", 0.5)))
        chart = _normalize_chart(raw.get("chart"))
        raw_media = raw.get("media_candidates") or raw.get("media") or []
        if isinstance(raw_media, Mapping):
            raw_media = [raw_media]
        media = tuple(
            candidate
            for item in raw_media
            if (candidate := _normalize_media(item)) is not None
        )
        unit = EvidenceUnit(
            evidence_id=evidence_id or f"evidence-{index}",
            topic=topic or _topic_from_claim(claim),
            claim=claim,
            citations=citations,
            kind=kind,
            importance=importance,
            detail=_clean(raw.get("detail") or raw.get("explanation")),
            example=_clean(raw.get("example")),
            chart=chart,
            media_candidates=media,
        )
    else:
        raise TypeError("Every evidence item must be an EvidenceUnit or mapping")

    if not _clean(unit.evidence_id):
        raise ValueError("Evidence id cannot be empty")
    if not _clean(unit.claim) or _is_placeholder(unit.claim):
        raise ValueError("Empty or placeholder claims cannot become slide content")
    if not unit.citations or any(not _clean(item) for item in unit.citations):
        raise ValueError("Every evidence claim must have at least one citation")
    if not _clean(unit.topic) or _is_placeholder(unit.topic):
        raise ValueError("Every evidence claim must have a meaningful topic")
    if not 0 <= unit.importance <= 1:
        raise ValueError("Evidence importance must be between 0 and 1")
    return unit


def _normalize_chart(raw: Any) -> ChartEvidence | None:
    if raw is None or raw == {}:
        return None
    if isinstance(raw, ChartEvidence):
        return raw
    if not isinstance(raw, Mapping):
        return None
    labels = _strings(raw.get("labels"))
    series: list[ChartSeries] = []
    raw_series = raw.get("series") or raw.get("datasets") or []
    if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes)):
        for index, item in enumerate(raw_series, start=1):
            if not isinstance(item, Mapping):
                continue
            values = _numbers(item.get("values") or item.get("data"))
            series.append(
                ChartSeries(
                    name=_clean(item.get("name") or item.get("label")) or f"Series {index}",
                    values=values,
                )
            )
    if not series:
        values = _numbers(raw.get("values"))
        if values:
            series.append(ChartSeries(_clean(raw.get("label")) or "Value", values))
    return ChartEvidence(
        chart_type=_clean(raw.get("chart_type") or raw.get("type")).casefold(),
        title=_clean(raw.get("title")),
        labels=labels,
        series=tuple(series),
        citations=_strings(raw.get("citations") or raw.get("citation")),
        unit=_clean(raw.get("unit")),
        illustrative=bool(raw.get("illustrative", False)),
    )


def _normalize_media(raw: Any) -> MediaAsset | None:
    if isinstance(raw, MediaAsset):
        return raw
    if not isinstance(raw, Mapping):
        return None
    return MediaAsset(
        provider=_clean(raw.get("provider")),
        asset_id=_clean(raw.get("asset_id") or raw.get("id")),
        download_url=_clean(raw.get("download_url") or raw.get("url")),
        source_url=_clean(raw.get("source_url") or raw.get("permalink")),
        mime_type=_clean(raw.get("mime_type") or raw.get("content_type")),
        origin=_clean(raw.get("origin")),
        verified=raw.get("verified") is True,
        attribution=_clean(raw.get("attribution") or raw.get("credit")),
    )


def _valid_chart(chart: ChartEvidence, evidence_citations: tuple[str, ...]) -> bool:
    if chart.illustrative or chart.chart_type not in _ALLOWED_CHART_TYPES:
        return False
    if not chart.title or len(chart.labels) < 2 or len(set(chart.labels)) != len(chart.labels):
        return False
    if any(not label or _is_placeholder(label) for label in chart.labels):
        return False
    if not chart.series or not chart.citations:
        return False
    if not set(chart.citations).issubset(set(evidence_citations)):
        return False
    return all(
        series.name
        and len(series.values) == len(chart.labels)
        and all(math.isfinite(value) for value in series.values)
        for series in chart.series
    )


def _valid_media(media: MediaAsset) -> bool:
    provider = media.provider.casefold()
    return bool(
        media.verified
        and media.origin.casefold() == "api"
        and provider
        and provider not in _BLOCKED_MEDIA_PROVIDERS
        and media.asset_id
        and _https_url(media.download_url)
        and _https_url(media.source_url)
        and media.mime_type.casefold().startswith(("image/", "video/"))
    )


def _preferences(options: Mapping[str, Any], evidence: Sequence[_Candidate]) -> _Preferences:
    control = _clean(options.get("planning_mode") or options.get("design_control")).casefold()
    mode = (
        PlanningMode.GUIDED
        if control in {"custom", "guided", "user"}
        else PlanningMode.AUTONOMOUS
    )
    content_density = _content_density(options, evidence)
    media_density = _media_density(options)
    target = options.get("slide_count") if mode is PlanningMode.GUIDED else None
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        target = None
    language = _clean(options.get("language") or "auto").casefold()
    if language == "auto":
        sample = " ".join(
            f"{item.unit.topic} {item.unit.claim}" for item in evidence[:12]
        )
        language = "fa" if _has_persian(sample) else "en"
    return _Preferences(
        mode=mode,
        content_density=content_density,
        media_density=media_density,
        target_slides=target,
        include_charts=options.get("include_charts", True) is not False,
        language=language,
        title=_clean(options.get("title")),
        image_strategy=_clean(options.get("image_strategy") or "auto").casefold(),
    )


def _content_density(
    options: Mapping[str, Any], evidence: Sequence[_Candidate]
) -> ContentDensity:
    requested = _clean(options.get("content_density") or options.get("content_depth")).casefold()
    aliases = {
        "airy": ContentDensity.CONCISE,
        "concise": ContentDensity.CONCISE,
        "balanced": ContentDensity.BALANCED,
        "dense": ContentDensity.DEEP,
        "deep": ContentDensity.DEEP,
        "detailed": ContentDensity.DEEP,
    }
    if requested in aliases:
        return aliases[requested]

    depth = _clean(options.get("evidence_depth")).casefold()
    if depth == "focused":
        return ContentDensity.CONCISE
    if depth == "comprehensive":
        return ContentDensity.DEEP
    deck_format = _clean(options.get("deck_format")).casefold()
    if deck_format in {"executive_brief", "pitch_story"}:
        return ContentDensity.CONCISE
    if deck_format in {"research_presentation", "teaching_deck"}:
        return ContentDensity.DEEP
    rich_items = sum(bool(item.unit.detail or item.unit.example) for item in evidence)
    return (
        ContentDensity.DEEP
        if len(evidence) >= 8 and rich_items >= len(evidence) / 2
        else ContentDensity.BALANCED
    )


def _media_density(options: Mapping[str, Any]) -> MediaDensity:
    requested = _clean(options.get("media_density")).casefold()
    aliases = {
        "none": MediaDensity.NONE,
        "low": MediaDensity.MINIMAL,
        "minimal": MediaDensity.MINIMAL,
        "balanced": MediaDensity.BALANCED,
        "rich": MediaDensity.RICH,
        "media_rich": MediaDensity.RICH,
    }
    if requested in aliases:
        return aliases[requested]
    image_strategy = _clean(options.get("image_strategy") or "auto").casefold()
    if image_strategy == "none":
        return MediaDensity.NONE
    if image_strategy == "diagrams_only":
        return MediaDensity.MINIMAL
    if image_strategy == "pexels":
        return MediaDensity.RICH
    deck_format = _clean(options.get("deck_format")).casefold()
    if deck_format in {"pitch_story", "conference_talk"}:
        return MediaDensity.RICH
    return MediaDensity.BALANCED


def _group_evidence(candidates: Sequence[_Candidate], density: ContentDensity) -> list[_Group]:
    buckets: dict[tuple[str, EvidenceKind], list[_Candidate]] = {}
    order: list[tuple[str, EvidenceKind]] = []
    for candidate in candidates:
        key = (_normalized_phrase(candidate.unit.topic), candidate.unit.kind)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(candidate)

    groups: list[_Group] = []
    for key in order:
        items = buckets[key]
        role = key[1]
        capacity = _group_capacity(role, density, items)
        for start in range(0, len(items), capacity):
            chunk = items[start : start + capacity]
            groups.append(_Group(chunk[0].unit.topic, _effective_role(role, len(chunk)), chunk))
    return groups


def _group_capacity(
    role: EvidenceKind, density: ContentDensity, candidates: Sequence[_Candidate]
) -> int:
    if any(item.chart is not None for item in candidates):
        return 1
    if role is EvidenceKind.TIMELINE:
        return 6
    if role is EvidenceKind.PROCESS:
        return 5
    if role is EvidenceKind.COMPARISON:
        return 2
    return {
        ContentDensity.CONCISE: 3,
        ContentDensity.BALANCED: 2,
        ContentDensity.DEEP: 1,
    }[density]


def _effective_role(role: EvidenceKind, item_count: int) -> EvidenceKind:
    if role is EvidenceKind.TIMELINE and item_count < 3:
        return EvidenceKind.INSIGHT
    if role in {EvidenceKind.PROCESS, EvidenceKind.COMPARISON} and item_count < 2:
        return EvidenceKind.INSIGHT
    return role


def _enforce_plan_capacity(
    groups: list[_Group],
) -> tuple[list[_Group], list[PlanWarning], list[str]]:
    max_content = _MAX_SLIDES - 2
    if len(groups) <= max_content:
        return groups, [], []
    ranked = sorted(
        enumerate(groups),
        key=lambda item: (-item[1].importance, item[0]),
    )[:max_content]
    keep_indexes = {index for index, _ in ranked}
    kept = [group for index, group in enumerate(groups) if index in keep_indexes]
    removed_ids = [
        candidate.unit.evidence_id
        for index, group in enumerate(groups)
        if index not in keep_indexes
        for candidate in group.candidates
    ]
    warning = PlanWarning(
        "deck_capacity_reached",
        "The 40-slide safety limit retained the highest-importance grounded evidence.",
        tuple(removed_ids),
    )
    return kept, [warning], removed_ids


def _curate_autonomous_groups(
    groups: list[_Group],
    density: ContentDensity,
) -> tuple[list[_Group], list[PlanWarning], list[str]]:
    """Keep automatic decks concise enough to retain a real narrative arc.

    Retrieval can surface many individually valid chunks from one paper. They are
    evidence for a story, not an instruction to create one slide per chunk.
    """
    if len(groups) <= 8:
        return groups, [], []
    ceiling = {
        ContentDensity.CONCISE: 8,
        ContentDensity.BALANCED: 10,
        ContentDensity.DEEP: 12,
    }[density]
    budget = min(ceiling, max(6, math.ceil(math.sqrt(len(groups)) * 2)))
    if len(groups) <= budget:
        return groups, [], []
    ranked = sorted(
        enumerate(groups),
        key=lambda item: (-item[1].importance, item[0]),
    )[:budget]
    selected_indexes = {index for index, _ in ranked}
    kept = [group for index, group in enumerate(groups) if index in selected_indexes]
    rejected_ids = [
        candidate.unit.evidence_id
        for index, group in enumerate(groups)
        if index not in selected_indexes
        for candidate in group.candidates
    ]
    return (
        kept,
        [
            PlanWarning(
                "automatic_narrative_curated",
                (
                    "Automatic mode selected the highest-signal evidence themes instead of "
                    "turning every retrieved chunk into a slide."
                ),
                tuple(rejected_ids),
            )
        ],
        rejected_ids,
    )


def _plan_media(
    groups: Sequence[_Group], preferences: _Preferences
) -> tuple[dict[int, MediaAsset], dict[int, MediaRequest]]:
    ratio = {
        MediaDensity.NONE: 0.0,
        MediaDensity.MINIMAL: 0.2,
        MediaDensity.BALANCED: 0.5,
        MediaDensity.RICH: 0.75,
    }[preferences.media_density]
    desired = min(len(groups), math.ceil(len(groups) * ratio))
    if desired == 0:
        return {}, {}

    ranked_indexes = sorted(
        range(len(groups)),
        key=lambda index: (
            -_media_priority(groups[index].role),
            -groups[index].importance,
            index,
        ),
    )[:desired]
    assignments: dict[int, MediaAsset] = {}
    requests: dict[int, MediaRequest] = {}
    used_assets: set[tuple[str, str]] = set()
    for index in ranked_indexes:
        group = groups[index]
        assets = (
            media
            for candidate in group.candidates
            for media in candidate.media
            if (media.provider.casefold(), media.asset_id) not in used_assets
        )
        asset = next(assets, None)
        if asset is not None:
            assignments[index] = asset
            used_assets.add((asset.provider.casefold(), asset.asset_id))
            continue
        requests[index] = _media_request(group, preferences.image_strategy)
    return assignments, requests


def _media_priority(role: EvidenceKind) -> int:
    return {
        EvidenceKind.CASE_STUDY: 8,
        EvidenceKind.CONTEXT: 7,
        EvidenceKind.CONCEPT: 6,
        EvidenceKind.PROCESS: 5,
        EvidenceKind.TIMELINE: 5,
        EvidenceKind.QUOTE: 5,
        EvidenceKind.INSIGHT: 4,
        EvidenceKind.COMPARISON: 3,
        EvidenceKind.ACTION: 2,
        EvidenceKind.LIMITATION: 2,
        EvidenceKind.METRIC: 1,
        EvidenceKind.SYNTHESIS: 1,
    }[role]


def _media_request(group: _Group, image_strategy: str) -> MediaRequest:
    query = _semantic_media_query(group)
    if image_strategy == "diagrams_only" or group.role in {
        EvidenceKind.PROCESS,
        EvidenceKind.COMPARISON,
        EvidenceKind.TIMELINE,
    }:
        asset_types = ("source_diagram", "source_figure")
        providers = ("source-extraction-api",)
    else:
        asset_types = ("photo", "video_frame")
        providers = ("pexels", "unsplash", "source-extraction-api")
    return MediaRequest(query, asset_types, providers)


def _semantic_media_query(group: _Group) -> str:
    subject = _strip_source_tokens(group.topic)
    if not subject:
        subject = _strip_source_tokens(group.candidates[0].unit.claim[:120])
    role_hint = {
        EvidenceKind.CASE_STUDY: "real world case study documentary",
        EvidenceKind.CONTEXT: "authentic environment overview",
        EvidenceKind.CONCEPT: "clear subject illustration",
        EvidenceKind.PROCESS: "documented process diagram",
        EvidenceKind.COMPARISON: "documented comparison visual",
        EvidenceKind.TIMELINE: "historical archival material",
        EvidenceKind.QUOTE: "authentic portrait editorial",
    }.get(group.role, "authentic documentary subject")
    return _SPACE.sub(" ", f"{subject} {role_hint}").strip()


def _build_slides(
    groups: Sequence[_Group],
    preferences: _Preferences,
    media_assignments: Mapping[int, MediaAsset],
    media_requests: Mapping[int, MediaRequest],
) -> list[SlidePlan]:
    title = preferences.title or _deck_title(
        [candidate for group in groups for candidate in group.candidates]
    )
    slides = [
        SlidePlan(
            slide_id="slide-01",
            role=EvidenceKind.CONTEXT,
            title=title,
            objective=_localized(
                preferences.language,
                fa="موضوع و وعده اصلی ارائه را روشن کند.",
                en="Establish the subject and the deck's central promise.",
            ),
            layout="big_statement",
            evidence_ids=(),
            citations=(),
            content_density=preferences.content_density,
        )
    ]
    for index, group in enumerate(groups):
        chart = next((item.chart for item in group.candidates if item.chart is not None), None)
        if not preferences.include_charts:
            chart = None
        media = media_assignments.get(index)
        slides.append(
            SlidePlan(
                slide_id=f"slide-{len(slides) + 1:02d}",
                role=group.role,
                title=group.topic,
                objective=_objective(group.role, group.topic, preferences.language),
                layout=_layout(group.role, len(group.candidates), chart, media),
                evidence_ids=tuple(item.unit.evidence_id for item in group.candidates),
                citations=_unique_strings(
                    citation
                    for item in group.candidates
                    for citation in item.unit.citations
                ),
                content_density=preferences.content_density,
                chart=chart,
                media=media,
                media_request=media_requests.get(index),
            )
        )

    target = preferences.target_slides
    can_close = len(groups) >= 3 and len(slides) < _MAX_SLIDES
    if target is not None and len(slides) >= target:
        can_close = False
    if can_close:
        top = sorted(
            (item for group in groups for item in group.candidates),
            key=lambda item: (-item.unit.importance, item.source_index),
        )[:3]
        slides.append(
            SlidePlan(
                slide_id=f"slide-{len(slides) + 1:02d}",
                role=EvidenceKind.SYNTHESIS,
                title=_localized(
                    preferences.language,
                    fa="جمع‌بندی و مسیر بعدی",
                    en="Synthesis and next direction",
                ),
                objective=_localized(
                    preferences.language,
                    fa="مهم‌ترین شواهد را به یک نتیجه و اقدام روشن متصل کند.",
                    en="Connect the strongest evidence to a clear conclusion and next action.",
                ),
                layout="closing_actions",
                evidence_ids=tuple(item.unit.evidence_id for item in top),
                citations=_unique_strings(
                    citation for item in top for citation in item.unit.citations
                ),
                content_density=preferences.content_density,
            )
        )
    return slides


def _layout(
    role: EvidenceKind,
    item_count: int,
    chart: ChartEvidence | None,
    media: MediaAsset | None,
) -> str:
    if chart is not None:
        return "chart_focus"
    if role is EvidenceKind.TIMELINE and item_count >= 3:
        return "timeline_story"
    if role is EvidenceKind.PROCESS and item_count >= 2:
        return "process_flow"
    if role is EvidenceKind.COMPARISON and item_count == 2:
        return "comparison_dual"
    if role is EvidenceKind.CASE_STUDY and media is not None:
        return "case_study"
    if role is EvidenceKind.QUOTE and media is not None:
        return "quote_photo"
    if role is EvidenceKind.LIMITATION and item_count >= 2:
        return "table_clean"
    if media is not None:
        return "editorial_split"
    if 2 <= item_count <= 4:
        return "cards_icon"
    return "big_statement"


def _objective(role: EvidenceKind, topic: str, language: str) -> str:
    if language == "fa" or (language == "auto" and _has_persian(topic)):
        verbs = {
            EvidenceKind.CONTEXT: "زمینه را با شواهد معتبر روشن کند",
            EvidenceKind.CONCEPT: "مفهوم را دقیق و قابل‌فهم توضیح دهد",
            EvidenceKind.INSIGHT: "بین شواهد و معنای عملی آن‌ها ارتباط برقرار کند",
            EvidenceKind.PROCESS: "فرایند را به ترتیب و بدون پرش توضیح دهد",
            EvidenceKind.COMPARISON: "تفاوت‌ها و پیامدهای مقایسه را نشان دهد",
            EvidenceKind.METRIC: "نتیجه عددی مستند را تفسیر کند",
            EvidenceKind.TIMELINE: "تحول موضوع را بر اساس نقاط عطف مستند نشان دهد",
            EvidenceKind.CASE_STUDY: "شواهد را در یک نمونه واقعی به کار ببرد",
            EvidenceKind.QUOTE: "دیدگاه منبع را با زمینه لازم ارائه کند",
            EvidenceKind.LIMITATION: "محدودیت‌ها و اثر عملی آن‌ها را روشن کند",
            EvidenceKind.ACTION: "شواهد را به اقدام مشخص تبدیل کند",
            EvidenceKind.SYNTHESIS: "نتیجه نهایی را از شواهد استخراج کند",
        }
        return f"{verbs[role]}: {topic}"
    verbs = {
        EvidenceKind.CONTEXT: "Frame the context with grounded evidence",
        EvidenceKind.CONCEPT: "Explain the concept precisely",
        EvidenceKind.INSIGHT: "Connect the evidence to its practical meaning",
        EvidenceKind.PROCESS: "Explain the process in a defensible sequence",
        EvidenceKind.COMPARISON: "Show the comparison and its implications",
        EvidenceKind.METRIC: "Interpret the documented numeric result",
        EvidenceKind.TIMELINE: "Show the evolution through documented milestones",
        EvidenceKind.CASE_STUDY: "Apply the evidence to a real case",
        EvidenceKind.QUOTE: "Present the source perspective with context",
        EvidenceKind.LIMITATION: "Clarify limitations and their practical effect",
        EvidenceKind.ACTION: "Turn the evidence into a concrete action",
        EvidenceKind.SYNTHESIS: "Derive the final conclusion from the evidence",
    }
    return f"{verbs[role]}: {topic}"


def _assert_plan_invariants(plan: DeckPlan) -> None:
    if not plan.slides:
        raise DeckPlanningError("A deck plan must contain at least one slide")
    ids: set[str] = set()
    for index, slide in enumerate(plan.slides):
        if slide.slide_id in ids:
            raise DeckPlanningError("Deck plan contains duplicate slide ids")
        ids.add(slide.slide_id)
        if not slide.title or _is_placeholder(slide.title) or not slide.objective:
            raise DeckPlanningError("Deck plan contains empty or placeholder slide content")
        if index > 0 and (not slide.evidence_ids or not slide.citations):
            raise DeckPlanningError("Every non-cover slide must be grounded in cited evidence")
        if slide.layout == "chart_focus" and slide.chart is None:
            raise DeckPlanningError("A chart layout cannot exist without grounded chart data")
        if slide.chart is not None and not _valid_chart(slide.chart, slide.citations):
            raise DeckPlanningError("Deck plan contains ungrounded chart data")
        if slide.media is not None and not _valid_media(slide.media):
            raise DeckPlanningError("Deck plan contains unverified media")
        if slide.media is None and slide.layout in {"case_study", "quote_photo"}:
            raise DeckPlanningError("A photo-dependent layout cannot use unresolved media")
        if slide.media_request is not None and (
            not slide.media_request.require_api or slide.media_request.allow_generated
        ):
            raise DeckPlanningError("Media requests must require a real API asset")


def _kind(raw: Any, topic: str, claim: str) -> EvidenceKind:
    value = _clean(raw).casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "definition": EvidenceKind.CONCEPT,
        "framework": EvidenceKind.CONCEPT,
        "case": EvidenceKind.CASE_STUDY,
        "example": EvidenceKind.CASE_STUDY,
        "chart": EvidenceKind.METRIC,
        "data": EvidenceKind.METRIC,
        "limitations": EvidenceKind.LIMITATION,
        "recommendation": EvidenceKind.ACTION,
        "conclusion": EvidenceKind.SYNTHESIS,
    }
    if value in aliases:
        return aliases[value]
    try:
        return EvidenceKind(value)
    except ValueError:
        pass
    text = f"{topic} {claim}".casefold()
    patterns = (
        (r"(?:timeline|history|milestone|تاریخ|نقطه عطف)", EvidenceKind.TIMELINE),
        (r"(?:compare|versus|\bvs\b|مقایسه|تفاوت)", EvidenceKind.COMPARISON),
        (r"(?:process|workflow|step|فرایند|مراحل)", EvidenceKind.PROCESS),
        (r"(?:case study|example|application|مطالعه موردی|کاربرد)", EvidenceKind.CASE_STUDY),
        (r"(?:metric|percent|rate|score|درصد|نرخ|امتیاز)", EvidenceKind.METRIC),
        (r"(?:definition|concept|چیست|تعریف|مفهوم)", EvidenceKind.CONCEPT),
        (r"(?:limit|challenge|risk|محدودیت|چالش|ریسک)", EvidenceKind.LIMITATION),
        (r"(?:recommend|action|next step|پیشنهاد|اقدام)", EvidenceKind.ACTION),
        (r"(?:conclusion|summary|جمع.?بندی|نتیجه)", EvidenceKind.SYNTHESIS),
    )
    return next(
        (kind for pattern, kind in patterns if re.search(pattern, text)),
        EvidenceKind.INSIGHT,
    )


def _importance(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, value))


def _deck_title(evidence: Sequence[_Candidate]) -> str:
    ranked = sorted(evidence, key=lambda item: (-item.unit.importance, item.source_index))
    return ranked[0].unit.topic


def _topic_from_claim(claim: str) -> str:
    clean = _clean(claim)
    return re.split(r"[.!?؟؛:]", clean, maxsplit=1)[0][:96].strip()


def _strip_source_tokens(value: str) -> str:
    clean = _MARKDOWN_LINK.sub(r"\1", value)
    clean = _CITATION_TOKEN.sub(" ", clean)
    clean = _BARE_SOURCE_TOKEN.sub(" ", clean)
    clean = re.sub(r"https?://\S+", " ", clean)
    clean = re.sub(r"\b(?:publication|doi|arxiv)/[^\s)\]]+", " ", clean, flags=re.I)
    return _SPACE.sub(" ", clean).strip(" -_:;,.")


def _is_non_narrative_evidence(unit: EvidenceUnit) -> bool:
    topic = _strip_source_tokens(unit.topic)
    claim = _strip_source_tokens(unit.claim)
    combined = f"{topic} {claim}"
    if _NON_NARRATIVE_TOPIC.search(topic) or _NON_NARRATIVE_TOPIC.search(combined):
        return True
    normalized = _normalized_phrase(combined)
    if "not a bot" in normalized or "captcha" in normalized:
        return True
    raw = f"{unit.topic} {unit.claim}"
    if len(re.findall(r"https?://|\bdoi\b|\barxiv\b|researchgate", raw, flags=re.I)) >= 2:
        return True
    if len(re.findall(r"\b(?:19|20)\d{2}[a-z]?\b", raw)) >= 3 and len(
        re.findall(r"\b[A-Z][A-Za-z'-]+,\s*[A-Z](?:\.|\b)", raw)
    ) >= 2:
        return True
    if len(re.findall(r"\.{4,}\s*\d+", raw)) >= 2:
        return True
    return bool(re.fullmatch(r"[A-Z][A-Za-z'-]+,?\s+[A-Z](?:\.|$).{0,30}", topic))


def _https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.casefold() == "https" and bool(parsed.netloc)


def _numbers(value: Any) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return ()
        numbers.append(float(item))
    return tuple(numbers)


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return _unique_strings(_clean(item) for item in value if _clean(item))


def _unique_strings(values: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _raw_evidence_id(raw: Any, index: int) -> str:
    if isinstance(raw, EvidenceUnit):
        return raw.evidence_id or f"evidence-{index}"
    if isinstance(raw, Mapping):
        return _clean(raw.get("evidence_id") or raw.get("id") or raw.get("label")) or (
            f"evidence-{index}"
        )
    return f"evidence-{index}"


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _normalized_phrase(value: str) -> str:
    return re.sub(r"[^\w\u0600-\u06ff]+", " ", value.casefold()).strip()


def _is_placeholder(value: str) -> bool:
    return _normalized_phrase(value) in _PLACEHOLDERS


def _has_persian(value: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", value))


def _localized(language: str, *, fa: str, en: str) -> str:
    return fa if language == "fa" else en


def _deduplicate_warnings(warnings: Sequence[PlanWarning]) -> list[PlanWarning]:
    deduplicated: list[PlanWarning] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for warning in warnings:
        key = (warning.code, warning.evidence_ids)
        if key not in seen:
            seen.add(key)
            deduplicated.append(warning)
    return deduplicated
