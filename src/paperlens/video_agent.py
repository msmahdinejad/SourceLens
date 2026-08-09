"""Pure, evidence-first planning for agentic video overviews.

The module decides narrative shape, scene count, pacing, and grounded visual
requirements before a renderer or narration provider is involved.  It never
pads a requested count, fabricates chart values, or turns a generic visual idea
into a media query.  Media requests contain only subjects copied from cited
evidence and must be resolved by a real adapter later in the pipeline.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class VideoPlanningError(ValueError):
    """Raised when a grounded video cannot be planned."""


class PlanningMode(StrEnum):
    AUTONOMOUS = "autonomous"
    GUIDED = "guided"


class Pace(StrEnum):
    CALM = "calm"
    BALANCED = "balanced"
    DYNAMIC = "dynamic"


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


class NarrativePhase(StrEnum):
    COMPLETE = "complete"
    HOOK = "hook"
    DEVELOPMENT = "development"
    PROOF = "proof"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class ChartSeries:
    name: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ChartEvidence:
    """Numeric evidence whose labels, series, and citations come from sources."""

    chart_type: str
    title: str
    labels: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    citations: tuple[str, ...]
    unit: str = ""
    illustrative: bool = False


@dataclass(frozen=True, slots=True)
class VideoEvidence:
    """One cited fact available to the narrative planner."""

    evidence_id: str
    topic: str
    claim: str
    citations: tuple[str, ...]
    kind: EvidenceKind = EvidenceKind.INSIGHT
    importance: float = 0.5
    complexity: float | None = None
    detail: str = ""
    example: str = ""
    chart: ChartEvidence | None = None


@dataclass(frozen=True, slots=True)
class MediaRequest:
    """A source-derived lookup request; it is not itself renderable media."""

    query: str
    source_evidence_ids: tuple[str, ...]
    citations: tuple[str, ...]
    asset_types: tuple[str, ...]
    provider_policy: tuple[str, ...]
    require_api: bool = True
    allow_generated: bool = False


@dataclass(frozen=True, slots=True)
class ScenePlan:
    scene_id: str
    phase: NarrativePhase
    role: EvidenceKind
    title: str
    objective: str
    duration_seconds: int
    narration_word_budget: int
    visual_intent: str
    evidence_ids: tuple[str, ...]
    citations: tuple[str, ...]
    chart: ChartEvidence | None = None
    media_request: MediaRequest | None = None


@dataclass(frozen=True, slots=True)
class PlanWarning:
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VideoPlan:
    title: str
    mode: PlanningMode
    pace: Pace
    narrative_arc: str
    scenes: tuple[ScenePlan, ...]
    warnings: tuple[PlanWarning, ...] = ()
    rejected_evidence_ids: tuple[str, ...] = ()

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    @property
    def total_duration_seconds(self) -> int:
        return sum(scene.duration_seconds for scene in self.scenes)


@dataclass(frozen=True, slots=True)
class _Preferences:
    mode: PlanningMode
    pace: Pace
    narrative_arc: str
    evidence_depth: str
    target_scenes: int | None
    scene_count_strategy: str
    target_duration_seconds: int | None
    include_charts: bool
    media_mode: str
    language: str
    title: str


@dataclass(slots=True)
class _Candidate:
    evidence: VideoEvidence
    source_index: int
    complexity: float
    chart: ChartEvidence | None


@dataclass(slots=True)
class _SceneGroup:
    topic: str
    role: EvidenceKind
    candidates: list[_Candidate] = field(default_factory=list)

    @property
    def complexity(self) -> float:
        return sum(item.complexity for item in self.candidates) / len(self.candidates)

    @property
    def importance(self) -> float:
        return max(item.evidence.importance for item in self.candidates)


_PLACEHOLDERS = {
    "action",
    "context",
    "decision",
    "evidence",
    "filler",
    "placeholder",
    "scene",
    "tbd",
    "todo",
    "اقدام",
    "تصمیم",
    "زمینه",
    "شاهد",
    "صحنه",
}
_ALLOWED_CHART_TYPES = {"bar", "line", "pie", "scatter"}
_MIN_TOTAL_SECONDS = 30
_MAX_SCENES = 24
_SOURCE_TOKEN = re.compile(r"\[(?:S|E)\d+(?:\s*[,;]\s*(?:S|E)?\d+)*\]", re.I)
_BARE_SOURCE_TOKEN = re.compile(r"\b[SE]\d+\b", re.I)
_URL = re.compile(r"https?://\S+", re.I)
_SPACE = re.compile(r"\s+")


def plan_video(
    evidence: Sequence[VideoEvidence | Mapping[str, Any]],
    options: Mapping[str, Any] | None = None,
) -> VideoPlan:
    """Create a variable-length, source-grounded video plan.

    Current Studio option names are accepted.  Scene count is derived from
    grounded evidence in autonomous mode.  Guided scene counts are upper/soft
    targets: the planner will neither add filler scenes nor clone evidence.
    A supplied ``duration_seconds`` is an exact public runtime contract.

    The returned duration is always at least 30 seconds.  When evidence is too
    sparse for that duration, real scenes receive visual breathing room and a
    warning is emitted; no synthetic scene or claim is created.
    """

    normalized, warnings, rejected = _ground_evidence(evidence)
    if not normalized:
        raise VideoPlanningError("No cited, non-placeholder evidence is available for a video")
    preferences = _preferences(dict(options or {}), normalized)
    groups = _group_evidence(normalized, preferences.evidence_depth)
    groups, capacity_warnings, capacity_rejected = _limit_groups(groups, preferences)
    warnings.extend(capacity_warnings)
    rejected.extend(capacity_rejected)

    if (
        preferences.scene_count_strategy == "fixed"
        and preferences.target_scenes is not None
        and len(groups) != preferences.target_scenes
    ):
        warnings.append(
            PlanWarning(
                "soft_scene_target_not_forced",
                (
                    f"Grounded narrative produced {len(groups)} scenes; requested target "
                    f"{preferences.target_scenes} was not padded or trimmed."
                ),
            )
        )

    natural_durations = [_natural_duration(group, preferences.pace) for group in groups]
    target_total, duration_warning = _target_duration(
        natural_durations,
        groups,
        preferences.target_duration_seconds,
    )
    if duration_warning is not None:
        warnings.append(duration_warning)
    durations = _allocate_seconds(natural_durations, target_total)
    scenes = _build_scenes(groups, durations, preferences)
    plan = VideoPlan(
        title=preferences.title or _video_title(normalized),
        mode=preferences.mode,
        pace=preferences.pace,
        narrative_arc=preferences.narrative_arc,
        scenes=tuple(scenes),
        warnings=tuple(_deduplicate_warnings(warnings)),
        rejected_evidence_ids=tuple(dict.fromkeys(rejected)),
    )
    _assert_plan_invariants(plan)
    return plan


def _ground_evidence(
    evidence: Sequence[VideoEvidence | Mapping[str, Any]],
) -> tuple[list[_Candidate], list[PlanWarning], list[str]]:
    candidates: list[_Candidate] = []
    warnings: list[PlanWarning] = []
    rejected: list[str] = []
    seen_ids: set[str] = set()
    seen_claims: set[str] = set()
    for index, raw in enumerate(evidence, start=1):
        try:
            item = _normalize_evidence(raw, index)
        except (TypeError, ValueError) as error:
            evidence_id = _raw_id(raw, index)
            rejected.append(evidence_id)
            warnings.append(
                PlanWarning("invalid_evidence_rejected", str(error), (evidence_id,))
            )
            continue
        claim_key = _normalized_phrase(item.claim)
        if item.evidence_id in seen_ids or claim_key in seen_claims:
            rejected.append(item.evidence_id)
            warnings.append(
                PlanWarning(
                    "duplicate_evidence_rejected",
                    "Duplicate evidence cannot create another scene.",
                    (item.evidence_id,),
                )
            )
            continue
        seen_ids.add(item.evidence_id)
        seen_claims.add(claim_key)
        chart = item.chart
        if chart is not None and not _valid_chart(chart, item.citations):
            chart = None
            warnings.append(
                PlanWarning(
                    "ungrounded_chart_rejected",
                    "Chart data was removed because its numeric provenance is incomplete.",
                    (item.evidence_id,),
                )
            )
        candidates.append(_Candidate(item, index, _complexity(item, chart), chart))
    return candidates, warnings, rejected


def _normalize_evidence(
    raw: VideoEvidence | Mapping[str, Any], index: int
) -> VideoEvidence:
    if isinstance(raw, VideoEvidence):
        item = raw
    elif isinstance(raw, Mapping):
        claim = _clean(raw.get("claim") or raw.get("text") or raw.get("body"))
        topic = _clean(raw.get("topic") or raw.get("title") or raw.get("section"))
        item = VideoEvidence(
            evidence_id=_clean(
                raw.get("evidence_id") or raw.get("id") or raw.get("label")
            )
            or f"evidence-{index}",
            topic=topic or _topic_from_claim(claim),
            claim=claim,
            citations=_strings(raw.get("citations") or raw.get("citation")),
            kind=_kind(raw.get("kind") or raw.get("role_hint") or raw.get("type"), topic, claim),
            importance=_bounded_float(raw.get("importance", raw.get("score", 0.5)), 0.5),
            complexity=_optional_bounded_float(raw.get("complexity")),
            detail=_clean(raw.get("detail") or raw.get("explanation")),
            example=_clean(raw.get("example")),
            chart=_normalize_chart(raw.get("chart")),
        )
    else:
        raise TypeError("Every evidence item must be a VideoEvidence or mapping")

    if _looks_like_formula_noise(item.topic):
        item = VideoEvidence(
            evidence_id=item.evidence_id,
            topic=_topic_from_claim(item.claim),
            claim=item.claim,
            citations=item.citations,
            kind=item.kind,
            importance=item.importance,
            complexity=item.complexity,
            detail=item.detail,
            example=item.example,
            chart=item.chart,
        )
    if not _clean(item.evidence_id):
        raise ValueError("Evidence id cannot be empty")
    if not item.claim or _is_placeholder(item.claim):
        raise ValueError("Empty or placeholder claims cannot become video scenes")
    if not item.topic or _is_placeholder(item.topic):
        raise ValueError("Every video claim must have a meaningful topic")
    if not item.citations or any(not citation for citation in item.citations):
        raise ValueError("Every video claim must have at least one citation")
    if not 0 <= item.importance <= 1:
        raise ValueError("Evidence importance must be between 0 and 1")
    if item.complexity is not None and not 0 <= item.complexity <= 1:
        raise ValueError("Evidence complexity must be between 0 and 1")
    return item


def _normalize_chart(raw: Any) -> ChartEvidence | None:
    if raw in (None, {}):
        return None
    if isinstance(raw, ChartEvidence):
        return raw
    if not isinstance(raw, Mapping):
        return None
    labels = _strings(raw.get("labels"))
    series: list[ChartSeries] = []
    raw_series = raw.get("series") or raw.get("datasets") or []
    if isinstance(raw_series, Sequence) and not isinstance(raw_series, (str, bytes)):
        for index, candidate in enumerate(raw_series, start=1):
            if not isinstance(candidate, Mapping):
                continue
            series.append(
                ChartSeries(
                    _clean(candidate.get("name") or candidate.get("label"))
                    or f"Series {index}",
                    _numbers(candidate.get("values") or candidate.get("data")),
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


def _valid_chart(chart: ChartEvidence, evidence_citations: tuple[str, ...]) -> bool:
    if chart.illustrative or chart.chart_type not in _ALLOWED_CHART_TYPES:
        return False
    if not chart.title or len(chart.labels) < 2 or len(set(chart.labels)) != len(chart.labels):
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


def _complexity(item: VideoEvidence, chart: ChartEvidence | None) -> float:
    if item.complexity is not None:
        return item.complexity
    words = _word_count(" ".join((item.claim, item.detail, item.example)))
    score = 0.2 + min(0.35, words / 180)
    if item.detail:
        score += 0.1
    if item.example:
        score += 0.08
    if chart is not None:
        score += 0.16
    if item.kind in {
        EvidenceKind.PROCESS,
        EvidenceKind.COMPARISON,
        EvidenceKind.TIMELINE,
        EvidenceKind.METRIC,
    }:
        score += 0.1
    return min(1.0, score)


def _preferences(options: Mapping[str, Any], evidence: Sequence[_Candidate]) -> _Preferences:
    control = _clean(options.get("planning_mode") or options.get("design_control")).casefold()
    mode = (
        PlanningMode.GUIDED
        if control in {"custom", "guided", "user"}
        else PlanningMode.AUTONOMOUS
    )
    pace = _pace(options, evidence)
    strategy = _clean(options.get("scene_count_strategy") or "auto").casefold()
    if strategy not in {"auto", "fixed", "maximum"}:
        strategy = "auto"
    target_scenes = (
        options.get("scene_count")
        if mode is PlanningMode.GUIDED and strategy in {"fixed", "maximum"}
        else None
    )
    if not isinstance(target_scenes, int) or isinstance(target_scenes, bool) or target_scenes < 1:
        target_scenes = None
    seconds = options.get("duration_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        target_duration = max(_MIN_TOTAL_SECONDS, round(seconds))
    else:
        # Hidden migration path for plans saved before the seconds-based contract.
        minutes = options.get("duration_minutes")
        target_duration = (
            max(_MIN_TOTAL_SECONDS, round(minutes * 60))
            if isinstance(minutes, (int, float))
            and not isinstance(minutes, bool)
            and minutes > 0
            else None
        )
    language = _clean(options.get("language") or "auto").casefold()
    if language == "auto":
        sample = " ".join(
            f"{item.evidence.topic} {item.evidence.claim}" for item in evidence[:12]
        )
        language = "fa" if _has_persian(sample) else "en"
    return _Preferences(
        mode=mode,
        pace=pace,
        narrative_arc=_narrative_arc(options, evidence),
        evidence_depth=_clean(options.get("evidence_depth") or "balanced").casefold(),
        target_scenes=target_scenes,
        scene_count_strategy=strategy,
        target_duration_seconds=target_duration,
        include_charts=options.get("include_charts", True) is not False,
        media_mode=_clean(options.get("media_mode") or "auto").casefold(),
        language=language,
        title=_clean(options.get("title")),
    )


def _pace(options: Mapping[str, Any], evidence: Sequence[_Candidate]) -> Pace:
    requested = _clean(options.get("pace")).casefold()
    if requested in {item.value for item in Pace}:
        return Pace(requested)
    average = sum(item.complexity for item in evidence) / len(evidence)
    if average >= 0.68:
        return Pace.CALM
    if len(evidence) >= 12 and average < 0.42:
        return Pace.DYNAMIC
    return Pace.BALANCED


def _narrative_arc(options: Mapping[str, Any], evidence: Sequence[_Candidate]) -> str:
    requested = _clean(options.get("narrative_arc")).casefold()
    if requested and requested != "auto":
        return requested
    kinds = [item.evidence.kind for item in evidence]
    if EvidenceKind.LIMITATION in kinds and EvidenceKind.ACTION in kinds:
        return "problem_solution"
    if kinds.count(EvidenceKind.COMPARISON) >= 2:
        return "comparison"
    if EvidenceKind.CASE_STUDY in kinds:
        return "story"
    if any(item.chart is not None for item in evidence):
        return "evidence_explanation"
    return "explanation"


def _group_evidence(candidates: Sequence[_Candidate], evidence_depth: str) -> list[_SceneGroup]:
    buckets: dict[tuple[str, EvidenceKind], list[_Candidate]] = {}
    order: list[tuple[str, EvidenceKind]] = []
    for candidate in candidates:
        key = (_normalized_phrase(candidate.evidence.topic), candidate.evidence.kind)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(candidate)
    groups: list[_SceneGroup] = []
    for key in order:
        items = buckets[key]
        capacity = _group_capacity(key[1], evidence_depth, items)
        for start in range(0, len(items), capacity):
            chunk = items[start : start + capacity]
            groups.append(
                _SceneGroup(
                    chunk[0].evidence.topic,
                    _effective_role(key[1], len(chunk)),
                    chunk,
                )
            )
    return groups


def _group_capacity(
    role: EvidenceKind,
    evidence_depth: str,
    candidates: Sequence[_Candidate],
) -> int:
    if any(item.chart is not None for item in candidates):
        return 1
    if role is EvidenceKind.TIMELINE:
        return 6
    if role is EvidenceKind.PROCESS:
        return 5
    if role is EvidenceKind.COMPARISON:
        return 2
    return {"focused": 3, "balanced": 2, "comprehensive": 1}.get(evidence_depth, 2)


def _effective_role(role: EvidenceKind, count: int) -> EvidenceKind:
    if role is EvidenceKind.TIMELINE and count < 3:
        return EvidenceKind.INSIGHT
    if role in {EvidenceKind.PROCESS, EvidenceKind.COMPARISON} and count < 2:
        return EvidenceKind.INSIGHT
    return role


def _limit_groups(
    groups: list[_SceneGroup],
    preferences: _Preferences,
) -> tuple[list[_SceneGroup], list[PlanWarning], list[str]]:
    limits = [_MAX_SCENES]
    if (
        preferences.target_duration_seconds is not None
        and preferences.scene_count_strategy != "fixed"
    ):
        # Automatic videos need enough time for a complete idea, natural speech,
        # and visual comprehension. Shorter beats feel like robotic slide reading.
        limits.append(max(1, preferences.target_duration_seconds // 12))
    if preferences.target_scenes is not None:
        limits.append(preferences.target_scenes)
    capacity = min(limits)
    if len(groups) <= capacity:
        return groups, [], []
    ranked = sorted(
        enumerate(groups),
        key=lambda item: (-item[1].importance, item[0]),
    )[:capacity]
    keep = {index for index, _ in ranked}
    removed = [
        candidate.evidence.evidence_id
        for index, group in enumerate(groups)
        if index not in keep
        for candidate in group.candidates
    ]
    return (
        [group for index, group in enumerate(groups) if index in keep],
        [
            PlanWarning(
                "scene_capacity_reached",
                (
                    f"The grounded {capacity}-scene capacity retained the "
                    "highest-importance evidence without padding."
                ),
                tuple(removed),
            )
        ],
        removed,
    )


def _natural_duration(group: _SceneGroup, pace: Pace) -> float:
    words = sum(
        _word_count(
            " ".join(
                (candidate.evidence.claim, candidate.evidence.detail, candidate.evidence.example)
            )
        )
        for candidate in group.candidates
    )
    words_per_second = {Pace.CALM: 1.45, Pace.BALANCED: 1.65, Pace.DYNAMIC: 1.85}[pace]
    spoken = words / words_per_second
    comprehension = 5.0 + group.complexity * 5.0
    if any(candidate.chart is not None for candidate in group.candidates):
        comprehension += 3.0
    if group.role in {EvidenceKind.PROCESS, EvidenceKind.COMPARISON, EvidenceKind.TIMELINE}:
        comprehension += 2.0
    return max(6.0, min(35.0, spoken + comprehension))


def _target_duration(
    natural: Sequence[float],
    groups: Sequence[_SceneGroup],
    requested: int | None,
) -> tuple[int, PlanWarning | None]:
    natural_total = sum(natural)
    minimum = _MIN_TOTAL_SECONDS
    if requested is None:
        if natural_total >= minimum:
            return round(natural_total), None
        return (
            minimum,
            PlanWarning(
                "minimum_duration_uses_visual_hold",
                (
                    "Evidence supports less than 30 seconds of narration; the minimum is met "
                    "by holding real evidence scenes, not by adding filler."
                ),
                tuple(
                    candidate.evidence.evidence_id
                    for group in groups
                    for candidate in group.candidates
                ),
            ),
        )

    supported_low = max(minimum, round(natural_total * 0.8))
    supported_high = max(minimum, round(natural_total * 1.35))
    if requested < supported_low:
        return (
            max(minimum, requested),
            PlanWarning(
                "duration_target_compresses_evidence",
                (
                    "The exact requested runtime is shorter than the natural narration; "
                    "the planner retained only the strongest evidence and tightened pacing."
                ),
            ),
        )
    if requested > supported_high:
        return (
            requested,
            PlanWarning(
                "duration_target_uses_visual_hold",
                (
                    "The exact requested runtime exceeds natural narration; real evidence "
                    "scenes receive visual breathing room and no filler scene is added."
                ),
            ),
        )
    return max(minimum, requested), None


def _allocate_seconds(natural: Sequence[float], target: int) -> list[int]:
    if not natural:
        return []
    total = sum(natural)
    raw = [value / total * target for value in natural]
    allocated = [max(1, math.floor(value)) for value in raw]
    difference = target - sum(allocated)
    order = sorted(
        range(len(raw)),
        key=lambda index: (-(raw[index] - math.floor(raw[index])), index),
    )
    if difference > 0:
        for position in range(difference):
            allocated[order[position % len(order)]] += 1
    elif difference < 0:
        removable = sorted(range(len(allocated)), key=lambda index: (-allocated[index], index))
        for position in range(-difference):
            index = removable[position % len(removable)]
            if allocated[index] > 1:
                allocated[index] -= 1
    return allocated


def _build_scenes(
    groups: Sequence[_SceneGroup], durations: Sequence[int], preferences: _Preferences
) -> list[ScenePlan]:
    scenes: list[ScenePlan] = []
    for index, (group, duration) in enumerate(zip(groups, durations, strict=True)):
        chart = next(
            (candidate.chart for candidate in group.candidates if candidate.chart is not None),
            None,
        )
        if not preferences.include_charts:
            chart = None
        visual_intent = _visual_intent(group.role, chart)
        citations = _unique_strings(
            citation
            for candidate in group.candidates
            for citation in candidate.evidence.citations
        )
        evidence_ids = tuple(candidate.evidence.evidence_id for candidate in group.candidates)
        source_words = sum(
            _word_count(
                " ".join(
                    (
                        candidate.evidence.claim,
                        candidate.evidence.detail,
                        candidate.evidence.example,
                    )
                )
            )
            for candidate in group.candidates
        )
        speaking_seconds = max(2.0, duration - min(1.2, duration * 0.12))
        spoken_capacity = math.floor(
            speaking_seconds
            * {Pace.CALM: 1.45, Pace.BALANCED: 1.65, Pace.DYNAMIC: 1.85}[
                preferences.pace
            ]
        )
        narration_budget = min(spoken_capacity, max(10, math.ceil(source_words * 1.35)))
        media_request = (
            None
            if chart is not None
            else _media_request(group, evidence_ids, citations, preferences.media_mode)
        )
        scenes.append(
            ScenePlan(
                scene_id=f"scene-{index + 1:02d}",
                phase=_phase(index, len(groups), group.role, chart),
                role=group.role,
                title=group.topic,
                objective=_objective(group.role, group.topic, preferences.language),
                duration_seconds=duration,
                narration_word_budget=narration_budget,
                visual_intent=visual_intent,
                evidence_ids=evidence_ids,
                citations=citations,
                chart=chart,
                media_request=media_request,
            )
        )
    return scenes


def _phase(
    index: int,
    total: int,
    role: EvidenceKind,
    chart: ChartEvidence | None,
) -> NarrativePhase:
    if total == 1:
        return NarrativePhase.COMPLETE
    if index == 0:
        return NarrativePhase.HOOK
    if index == total - 1:
        return NarrativePhase.CLOSE
    if chart is not None or role in {
        EvidenceKind.METRIC,
        EvidenceKind.CASE_STUDY,
        EvidenceKind.COMPARISON,
    }:
        return NarrativePhase.PROOF
    return NarrativePhase.DEVELOPMENT


def _visual_intent(role: EvidenceKind, chart: ChartEvidence | None) -> str:
    if chart is not None:
        return "data"
    return {
        EvidenceKind.PROCESS: "process",
        EvidenceKind.COMPARISON: "comparison",
        EvidenceKind.TIMELINE: "timeline",
        EvidenceKind.QUOTE: "quote",
        EvidenceKind.ACTION: "statement",
        EvidenceKind.SYNTHESIS: "statement",
    }.get(role, "footage")


def _media_request(
    group: _SceneGroup,
    evidence_ids: tuple[str, ...],
    citations: tuple[str, ...],
    media_mode: str,
) -> MediaRequest:
    query = _source_query(group)
    if media_mode == "motion_graphics" or group.role in {
        EvidenceKind.PROCESS,
        EvidenceKind.COMPARISON,
        EvidenceKind.TIMELINE,
        EvidenceKind.CONCEPT,
    }:
        asset_types = ("source_figure", "source_diagram")
        providers = ("source-extraction-api",)
    elif media_mode == "photo_first":
        asset_types = ("photo", "video")
        providers = ("pexels", "unsplash", "source-extraction-api")
    else:
        asset_types = ("video", "photo")
        providers = ("pexels", "source-extraction-api")
    return MediaRequest(query, evidence_ids, citations, asset_types, providers)


def _source_query(group: _SceneGroup) -> str:
    fragments = [group.topic]
    fragments.extend(
        _human_readable_source(candidate.evidence.claim) for candidate in group.candidates[:2]
    )
    clean = _strip_source_tokens(" ".join(fragments))
    if not clean:
        raise VideoPlanningError("Source evidence could not produce a meaningful media query")
    return " ".join(clean.split()[:28])


def _objective(role: EvidenceKind, topic: str, language: str) -> str:
    if language == "fa":
        verbs = {
            EvidenceKind.CONTEXT: "زمینه موضوع را با شواهد روشن کند",
            EvidenceKind.CONCEPT: "مفهوم را دقیق و تصویری توضیح دهد",
            EvidenceKind.INSIGHT: "معنای عملی شواهد را آشکار کند",
            EvidenceKind.PROCESS: "فرایند را مرحله‌به‌مرحله توضیح دهد",
            EvidenceKind.COMPARISON: "تفاوت و پیامد مقایسه را نشان دهد",
            EvidenceKind.METRIC: "داده عددی مستند را تفسیر کند",
            EvidenceKind.TIMELINE: "تحول مستند موضوع را روایت کند",
            EvidenceKind.CASE_STUDY: "شواهد را در یک نمونه واقعی نشان دهد",
            EvidenceKind.QUOTE: "دیدگاه منبع را همراه با زمینه ارائه کند",
            EvidenceKind.LIMITATION: "محدودیت و اثر آن را روشن کند",
            EvidenceKind.ACTION: "شواهد را به اقدام مشخص متصل کند",
            EvidenceKind.SYNTHESIS: "نتیجه نهایی را از شواهد استخراج کند",
        }
        return f"{verbs[role]}: {topic}"
    verbs = {
        EvidenceKind.CONTEXT: "Frame the subject with grounded evidence",
        EvidenceKind.CONCEPT: "Explain the concept precisely and visually",
        EvidenceKind.INSIGHT: "Reveal the practical meaning of the evidence",
        EvidenceKind.PROCESS: "Explain the process in a defensible sequence",
        EvidenceKind.COMPARISON: "Show the comparison and its implications",
        EvidenceKind.METRIC: "Interpret the documented numeric evidence",
        EvidenceKind.TIMELINE: "Narrate the documented evolution",
        EvidenceKind.CASE_STUDY: "Show the evidence in a real case",
        EvidenceKind.QUOTE: "Present the source perspective with context",
        EvidenceKind.LIMITATION: "Clarify the limitation and its effect",
        EvidenceKind.ACTION: "Connect the evidence to a concrete action",
        EvidenceKind.SYNTHESIS: "Derive the conclusion from the evidence",
    }
    return f"{verbs[role]}: {topic}"


def _assert_plan_invariants(plan: VideoPlan) -> None:
    if not plan.scenes:
        raise VideoPlanningError("A video plan must contain at least one scene")
    if plan.total_duration_seconds < _MIN_TOTAL_SECONDS:
        raise VideoPlanningError("Video plan must be at least 30 seconds")
    scene_ids: set[str] = set()
    for scene in plan.scenes:
        if scene.scene_id in scene_ids:
            raise VideoPlanningError("Video plan contains duplicate scene ids")
        scene_ids.add(scene.scene_id)
        if not scene.title or _is_placeholder(scene.title) or not scene.objective:
            raise VideoPlanningError("Video plan contains empty or placeholder scene content")
        if _looks_like_formula_noise(scene.title):
            raise VideoPlanningError("OCR-damaged formula text cannot become a video title")
        if not scene.evidence_ids or not scene.citations:
            raise VideoPlanningError("Every video scene must be grounded in cited evidence")
        if scene.duration_seconds < 1 or scene.narration_word_budget < 1:
            raise VideoPlanningError("Every scene must have a positive grounded duration")
        if scene.chart is not None and not _valid_chart(scene.chart, scene.citations):
            raise VideoPlanningError("Video plan contains ungrounded chart data")
        if scene.visual_intent == "data" and scene.chart is None:
            raise VideoPlanningError("Data visual intent requires documented chart data")
        request = scene.media_request
        if request is not None:
            if not request.query or _contains_source_token(request.query):
                raise VideoPlanningError("Media query must be meaningful and source-token free")
            if _looks_like_formula_noise(request.query):
                raise VideoPlanningError("OCR-damaged formula text cannot become a media query")
            if request.source_evidence_ids != scene.evidence_ids:
                raise VideoPlanningError("Media query provenance must match scene evidence")
            if not request.require_api or request.allow_generated:
                raise VideoPlanningError("Media request must require a real API asset")


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
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return _unique_strings(_clean(item) for item in value if _clean(item))


def _unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _bounded_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, result))


def _optional_bounded_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _word_count(value: str) -> int:
    return len(re.findall(r"[\w\u0600-\u06ff]+", value))


def _raw_id(raw: Any, index: int) -> str:
    if isinstance(raw, VideoEvidence):
        return raw.evidence_id or f"evidence-{index}"
    if isinstance(raw, Mapping):
        return _clean(raw.get("evidence_id") or raw.get("id") or raw.get("label")) or (
            f"evidence-{index}"
        )
    return f"evidence-{index}"


def _video_title(evidence: Sequence[_Candidate]) -> str:
    ranked = sorted(evidence, key=lambda item: (-item.evidence.importance, item.source_index))
    return ranked[0].evidence.topic


def _topic_from_claim(claim: str) -> str:
    readable = _human_readable_source(claim)
    return re.split(r"[.!?؟؛:]", readable, maxsplit=1)[0][:96].strip()


def _human_readable_source(value: str) -> str:
    """Drop leading OCR math debris before deriving audience-facing copy."""

    clean = _strip_source_tokens(_clean(value))
    persian = re.search(
        r"[\u0600-\u06ff][\u0600-\u06ff\u200c]*(?:\s+[^\s.!?؟؛:]+){2,}",
        clean,
    )
    if persian:
        return persian.group(0).strip()
    tokens = clean.split()
    math_words = {"sin", "cos", "tan", "log", "ln", "lim", "wait", "zdt"}
    while tokens and (
        len(re.sub(r"[^A-Za-z]", "", tokens[0])) <= 1
        or tokens[0].casefold().strip("()[]{}") in math_words
        or re.search(r"[=∑∫√×÷^_]", tokens[0])
    ):
        tokens.pop(0)
    return " ".join(tokens).strip()


def _looks_like_formula_noise(value: str) -> bool:
    clean = _clean(value)
    if not clean:
        return False
    math_marks = len(
        re.findall(r"[=∑∫√×÷^_]|\\(?:sum|frac|int|sqrt|gamma)", clean, re.I)
    )
    tokens = re.findall(r"[\w\u0600-\u06ff]+", clean)
    short_tokens = sum(len(token) <= 2 for token in tokens)
    known_math = len(re.findall(r"(?i)\b(?:sin|cos|tan|log|lim|zdt|wait)\b", clean))
    return math_marks >= 2 or known_math >= 2 or (
        math_marks >= 1 and bool(tokens) and short_tokens / len(tokens) >= 0.5
    )


def _strip_source_tokens(value: str) -> str:
    clean = _SOURCE_TOKEN.sub(" ", value)
    clean = _BARE_SOURCE_TOKEN.sub(" ", clean)
    clean = _URL.sub(" ", clean)
    return _SPACE.sub(" ", clean).strip(" -_:;,.")


def _contains_source_token(value: str) -> bool:
    return bool(_SOURCE_TOKEN.search(value) or _BARE_SOURCE_TOKEN.search(value))


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _normalized_phrase(value: str) -> str:
    return re.sub(r"[^\w\u0600-\u06ff]+", " ", value.casefold()).strip()


def _is_placeholder(value: str) -> bool:
    return _normalized_phrase(value) in _PLACEHOLDERS


def _has_persian(value: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", value))


def _deduplicate_warnings(warnings: Sequence[PlanWarning]) -> list[PlanWarning]:
    deduplicated: list[PlanWarning] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for warning in warnings:
        key = (warning.code, warning.evidence_ids)
        if key not in seen:
            seen.add(key)
            deduplicated.append(warning)
    return deduplicated
