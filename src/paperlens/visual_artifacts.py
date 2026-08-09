from __future__ import annotations

import asyncio
import copy
import math
import time
import wave
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperlens.fonts import FontRegistry
from paperlens.podcast import (
    GeminiLivePodcastRenderer,
    GroqOrpheusPodcastRenderer,
    PiperPodcastRenderer,
    PodcastConfig,
    PodcastParticipant,
    PodcastRenderRequest,
    PodcastScriptTurn,
    ResilientPodcastRenderer,
)
from paperlens.studio import StudioArtifact, StudioArtifactStore
from paperlens.visual_direction import direct_presentation, direct_video
from paperlens.visual_engine_bridge import (
    VisualEngineBridge,
    VisualEngineCancelled,
    VisualEngineEvent,
    VisualEngineUnavailable,
)
from paperlens.visual_media import JamendoMusicResolver, MediaAsset, PexelsMediaResolver


def _video_duration_seconds(options: Mapping[str, Any]) -> int:
    """Return the public seconds-based target while preserving old saved artifacts."""

    raw = options.get("duration_seconds")
    if raw is None and options.get("duration_minutes") is not None:
        try:
            raw = float(options["duration_minutes"]) * 60
        except (TypeError, ValueError):
            raw = None
    try:
        seconds = round(float(raw if raw is not None else 180))
    except (TypeError, ValueError):
        seconds = 180
    return max(30, min(1_200, seconds))


class PresentationRenderer:
    """Prepare licensed media and fonts for the authoritative Node deck renderer."""

    _PHOTO_LAYOUTS = {
        "cover_cinematic": 1,
        "editorial_split": 1,
        "photo_mosaic": 3,
        "quote_photo": 1,
        "case_study": 2,
    }
    _MEDIA_FALLBACK_LAYOUTS = {
        "cover_cinematic": "big_statement",
        "editorial_split": "big_statement",
        "photo_mosaic": "cards_icon",
        "quote_photo": "big_statement",
        "case_study": "cards_icon",
    }

    def __init__(
        self,
        media: PexelsMediaResolver | None = None,
        fonts: FontRegistry | None = None,
    ) -> None:
        self.media = media
        self.fonts = fonts

    def _font_family(self, options: Mapping[str, Any], text: str = "") -> str:
        return self._font_selection(options, text)[0]

    def _font_selection(
        self, options: Mapping[str, Any], text: str = ""
    ) -> tuple[str, Path | None]:
        requested = str(options.get("font_family") or "auto").strip()
        if requested.casefold() == "auto":
            if self.fonts is not None:
                asset = self.fonts.best_for_text(text)
                if asset is not None:
                    return asset.family, asset.path.parent
            return "Vazirmatn", None
        if self.fonts is not None:
            with suppress(KeyError):
                asset = self.fonts.resolve(requested)
                return asset.family, asset.path.parent
        return requested, None

    def prepare_engine_request(
        self,
        artifact: StudioArtifact,
        output_dir: Path,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Resolve host-owned media and fonts before rendering a SourceLens deck."""

        plan = copy.deepcopy(artifact.data)
        slides = list(plan.get("slides") or [])
        if not slides:
            raise ValueError("The presentation plan contains no slides")
        text = " ".join(
            str(value or "")
            for slide in slides
            for value in (slide.get("title"), slide.get("subtitle"), slide.get("body"))
        )
        font_family, font_dir = self._font_selection(artifact.options, text)
        warnings: list[str] = []
        credits: list[dict[str, str]] = []
        media_dir = output_dir / "media"
        def resolve(index: int, slide: dict[str, Any]) -> tuple[list[MediaAsset], list[str]]:
            local_warnings: list[str] = []
            layout = str(slide.get("layout") or "cards_icon")
            media_layout = self._requested_media_layout(slide, layout)
            media = self._resolve_slide_media(
                slide,
                index=index,
                layout=media_layout,
                strategy=str(artifact.options.get("image_strategy") or "auto"),
                media_dir=media_dir,
                warnings=local_warnings,
            )
            return media, local_warnings

        workers = min(4, max(1, len(slides)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="slide-media") as pool:
            futures = {
                index: pool.submit(resolve, index, slide)
                for index, slide in enumerate(slides, start=1)
            }
            resolved = {index: future.result() for index, future in futures.items()}

        used_source_urls: set[str] = set()
        for index, slide in enumerate(slides, start=1):
            media, local_warnings = resolved[index]
            warnings.extend(local_warnings)
            unique_media = []
            for asset in media:
                if asset.source_url in used_source_urls:
                    warnings.append(
                        f"Slide {index}: duplicate Pexels asset was removed from the deck"
                    )
                    continue
                used_source_urls.add(asset.source_url)
                unique_media.append(asset)
            media = unique_media
            layout = str(slide.get("layout") or "cards_icon")
            media_layout = self._requested_media_layout(slide, layout)
            self._apply_unresolved_media_fallback(
                slide,
                index=index,
                layout=layout,
                media_layout=media_layout,
                media=media,
                warnings=warnings,
            )
            slide["resolved_assets"] = [
                {
                    "path": str(asset.path.resolve()),
                    "id": f"slide-{index}-{slot}",
                    "credit": asset.creator,
                    "source_url": asset.source_url,
                }
                for slot, asset in enumerate(media, start=1)
            ]
            credits.extend(asset.credit() for asset in media)
        plan["slides"] = slides
        options = {**artifact.options, "font_family": font_family}
        if font_dir is not None:
            options["font_dir"] = str(font_dir.resolve())
        return plan, options, {
            "media_credits": credits,
            "render_warnings": warnings,
            "font_family": font_family,
        }

    def _resolve_slide_media(
        self,
        item: Mapping[str, Any],
        *,
        index: int,
        layout: str,
        strategy: str,
        media_dir: Path,
        warnings: list[str],
        used_source_urls: set[str] | None = None,
    ) -> list[MediaAsset]:
        needed = self._PHOTO_LAYOUTS.get(layout, 0)
        required = bool(item.get("media_required"))
        if not needed:
            return []
        if strategy in {"none", "diagrams_only"}:
            if required:
                warnings.append(
                    f"Slide {index}: licensed API media was not requested because "
                    f"image strategy is {strategy}"
                )
            return []
        if self.media is None or not self.media.available:
            if required:
                warnings.append(
                    f"Slide {index}: licensed API media was unavailable because no Pexels "
                    "credential is configured"
                )
            return []
        queries = item.get("image_queries") if isinstance(item.get("image_queries"), list) else []
        queries = [str(value).strip() for value in queries if str(value).strip()]
        fallback_query = str(item.get("image_query") or "").strip()
        if fallback_query and fallback_query not in queries:
            queries.append(fallback_query)
        if not queries:
            if required:
                warnings.append(
                    f"Slide {index}: licensed API media was unavailable because no valid "
                    "query was supplied"
                )
            return []
        output = []
        excluded = used_source_urls if used_source_urls is not None else set()
        for slot in range(min(needed, len(queries))):
            try:
                asset = self.media.photo(
                    queries[slot],
                    media_dir / f"slide-{index:02d}-{slot + 1}.jpg",
                    exclude_urls=excluded,
                )
                output.append(asset)
                excluded.add(asset.source_url)
            except Exception as error:  # noqa: BLE001 - abstract visual fallback is intentional
                warnings.append(f"Slide {index} media {slot + 1}: {type(error).__name__}: {error}")
        if required and len(output) < needed:
            warnings.append(
                f"Slide {index}: required {needed} licensed API media asset(s), but "
                f"resolved {len(output)}"
            )
        return output

    def _requested_media_layout(self, item: Mapping[str, Any], layout: str) -> str:
        preferred = str(item.get("layout_if_media_resolves") or "").strip()
        return preferred if preferred in self._PHOTO_LAYOUTS else layout

    def _apply_unresolved_media_fallback(
        self,
        item: dict[str, Any],
        *,
        index: int,
        layout: str,
        media_layout: str,
        media: Sequence[MediaAsset],
        warnings: list[str],
    ) -> str:
        """Use a substantive layout when a real API media lookup has no result.

        The slide remains evidence-grounded and clearly records the failed lookup.
        It never substitutes generated, local, or placeholder imagery.
        """
        needed = self._PHOTO_LAYOUTS.get(media_layout, 0)
        if needed and len(media) >= needed:
            if media_layout != layout:
                item["layout"] = media_layout
            item["media_required"] = True
            return media_layout
        if media_layout not in self._PHOTO_LAYOUTS:
            return layout
        fallback = str(item.get("media_fallback_layout") or "").strip()
        if fallback not in {"big_statement", "cards_icon", "key_points"}:
            fallback = self._MEDIA_FALLBACK_LAYOUTS[media_layout]
        items = item.get("items") if isinstance(item.get("items"), list) else []
        if fallback == "cards_icon" and len(items) < 2:
            fallback = "big_statement"
        item["layout"] = fallback
        item["media_required"] = False
        item["image_queries"] = []
        item["image_query"] = ""
        item["media_unavailable"] = True
        warnings.append(
            f"Slide {index}: reflowed from {media_layout} to {fallback} because licensed "
            f"API media could not be fully resolved ({len(media)}/{needed}); "
            "no substitute media was used"
        )
        return fallback

    @staticmethod
    def _chart_payload(
        chart: Any,
    ) -> tuple[list[str], list[tuple[str, list[float]]], str] | None:
        if not isinstance(chart, dict):
            return None
        labels = chart.get("labels")
        if (
            not isinstance(labels, list)
            or len(labels) < 2
            or not all(str(value).strip() for value in labels)
        ):
            return None
        series = []
        if isinstance(chart.get("values"), list):
            series.append((str(chart.get("label") or "Value"), chart["values"]))
        elif isinstance(chart.get("series"), list):
            series.extend(
                (str(item.get("name") or "Series"), item.get("values"))
                for item in chart["series"]
                if isinstance(item, dict)
            )
        normalized_series: list[tuple[str, list[float]]] = []
        for name, values in series:
            if not isinstance(values, list) or len(values) != len(labels):
                return None
            try:
                numeric = [float(value) for value in values]
            except (TypeError, ValueError):
                return None
            if any(isinstance(value, bool) for value in values) or not all(
                math.isfinite(value) for value in numeric
            ):
                return None
            normalized_series.append((name, numeric))
        if not normalized_series:
            return None
        return (
            [str(value).strip() for value in labels],
            normalized_series[:3],
            str(chart.get("type") or "bar"),
        )

@dataclass(frozen=True)
class NarrationResult:
    path: Path
    duration_seconds: float
    attempts: list[dict[str, Any]] = field(default_factory=list)


class GeminiVideoNarrator:
    """Reuse the resilient Gemini Live audio pool for a scene-bounded narrated track."""

    def __init__(
        self,
        api_keys: Sequence[str],
        *,
        groq_api_keys: Sequence[str] = (),
        piper_renderer: PiperPodcastRenderer | None = None,
        turn_timeout_seconds: float,
        minimum_key_gap_seconds: float,
        idle_refresh_seconds: float,
    ) -> None:
        self.api_keys = tuple(dict.fromkeys(key.strip() for key in api_keys if key.strip()))
        renderers = []
        if self.api_keys:
            renderers.append(
                GeminiLivePodcastRenderer(
                    turn_timeout_seconds=turn_timeout_seconds,
                    minimum_key_gap_seconds=minimum_key_gap_seconds,
                    idle_refresh_seconds=idle_refresh_seconds,
                )
            )
        groq_renderer = GroqOrpheusPodcastRenderer(groq_api_keys)
        if groq_renderer.api_keys:
            renderers.append(groq_renderer)
        if piper_renderer is not None and piper_renderer.configured:
            renderers.append(piper_renderer)
        self.renderer = (
            ResilientPodcastRenderer(renderers[0], *renderers[1:]) if renderers else None
        )

    def render(self, artifact: StudioArtifact, target: Path) -> NarrationResult:
        if self.renderer is None:
            raise ValueError(
                "Voice narration requires Gemini, Groq, or a configured local Piper voice"
            )
        scenes = artifact.data.get("scenes") or []
        script = self._narration_blocks(scenes)
        if not script:
            raise ValueError("Gemini voice narration requires at least one narrated scene")
        voice = str(artifact.options.get("voice") or "Kore")
        participant = PodcastParticipant(
            participant_id="narrator",
            name="Narrator",
            voice=voice,
            role="Warm, personable video presenter and subject-matter storyteller",
            delivery=(
                "Warm, conversational, expressive, and easy to follow; vary emphasis and "
                "sentence melody naturally without sounding theatrical"
            ),
            temperature=0.35,
        )
        config = PodcastConfig(
            production_mode="narrated",
            model=str(artifact.options.get("narration_model") or "gemini-3.1-flash-live-preview"),
            duration_minutes=max(
                2,
                min(20, (_video_duration_seconds(artifact.options) + 59) // 60),
            ),
            format="overview",
            tone="conversational",
            participants=[participant],
            pause_milliseconds=120,
            custom_instructions=(
                "Deliver this as a polished human presentation, not an announcement. Use "
                "natural phrasing, gentle emphasis, and subtle energy. Say every sentence "
                "once, then stop after the final word. Never rush to fit a time limit."
            ),
        )
        request = PodcastRenderRequest(
            artifact_id=artifact.artifact_id,
            title=artifact.title,
            language=artifact.language,
            config=config,
            # A whole video can exceed Gemini Live's per-turn audio resources.
            # Scene-level turns keep generation bounded and make failures
            # attributable and retryable without regenerating a long monologue.
            script=script,
            plan=(),
            source_context="",
            prior_turns=(),
            api_keys=self.api_keys,
        )
        pcm = bytearray()
        attempts = []
        completed_scenes = 0

        async def checkpoint(turn) -> None:
            nonlocal completed_scenes
            scene_id = getattr(turn, "turn_id", None) or script[completed_scenes].turn_id
            rendered_pcm = self._trim_turn_silence(turn.pcm)
            if pcm:
                pcm.extend(
                    b"\x00\x00"
                    * round(24_000 * config.pause_milliseconds / 1_000)
                )
            pcm.extend(rendered_pcm)
            attempts.extend(
                {**attempt, "scene_id": scene_id} for attempt in turn.attempts
            )
            completed_scenes += 1

        asyncio.run(self.renderer.render(request, checkpoint, lambda: False))
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24_000)
            wav_file.writeframes(bytes(pcm))
        return NarrationResult(
            path=target,
            duration_seconds=len(pcm) / (24_000 * 2),
            attempts=attempts,
        )

    @staticmethod
    def _narration_blocks(scenes: Sequence[Mapping[str, Any]]) -> tuple[PodcastScriptTurn, ...]:
        """Group short scenes so the presenter keeps one natural vocal flow."""

        blocks: list[PodcastScriptTurn] = []
        texts: list[str] = []
        titles: list[str] = []
        citations: list[str] = []
        word_count = 0

        def flush() -> None:
            nonlocal word_count
            if not texts:
                return
            blocks.append(
                PodcastScriptTurn(
                    turn_id=f"narration-block-{len(blocks) + 1}",
                    participant_id="narrator",
                    chapter=" — ".join(titles)[:160],
                    text="\n\n".join(texts),
                    citations=list(dict.fromkeys(citations))[:20],
                )
            )
            texts.clear()
            titles.clear()
            citations.clear()
            word_count = 0

        for item in scenes:
            narration = str(item.get("narration") or "").strip()
            if not narration:
                continue
            words = len(narration.split())
            if texts and (word_count + words > 80 or len(texts) >= 3):
                flush()
            texts.append(narration)
            titles.append(str(item.get("title") or "").strip())
            citations.extend(str(value) for value in (item.get("citations") or []))
            word_count += words
        flush()
        return tuple(blocks)

    @staticmethod
    def _trim_turn_silence(pcm: bytes, *, sample_rate: int = 24_000) -> bytes:
        """Remove transport silence while keeping a small natural breath at each edge."""

        if len(pcm) < 2:
            return pcm
        samples = memoryview(pcm[: len(pcm) - len(pcm) % 2]).cast("h")
        chunk_samples = max(1, round(sample_rate * 0.01))
        threshold_squared = 180 * 180
        active: list[int] = []
        for start in range(0, len(samples), chunk_samples):
            chunk = samples[start : start + chunk_samples]
            mean_square = (
                sum(int(value) * int(value) for value in chunk) / len(chunk) if chunk else 0
            )
            if mean_square >= threshold_squared:
                active.append(start)
        if not active:
            return b""
        keep_samples = round(sample_rate * 0.035)
        first = max(0, active[0] - keep_samples)
        last = min(len(samples), active[-1] + chunk_samples + keep_samples)
        return pcm[first * 2 : last * 2]


class VideoOverviewRenderer:
    """Render deterministic, resume-friendly visual scenes with FFmpeg."""

    def __init__(
        self,
        *,
        media: PexelsMediaResolver | None = None,
        music: JamendoMusicResolver | None = None,
        narrator: GeminiVideoNarrator | None = None,
        fonts: FontRegistry | None = None,
        media_workers: int = 4,
    ) -> None:
        self.media = media
        self.music = music
        self.narrator = narrator
        self.fonts = fonts
        self.media_workers = max(1, min(4, media_workers))

    def prepare_engine_request(
        self,
        artifact: StudioArtifact,
        output_dir: Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_media_progress: Callable[[int, int, int, str], None] | None = None,
        on_stage_progress: Callable[[str, str, str], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Resolve host-owned narration, media, timing, and fonts for the video engine."""

        plan = copy.deepcopy(artifact.data)
        scenes = [dict(item) for item in plan.get("scenes") or []]
        if not scenes:
            raise ValueError("The video plan contains no scenes")
        output_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        target_duration = _video_duration_seconds(artifact.options)
        effective_duration = float(target_duration)
        narration = None
        requested_narration_mode = str(artifact.options.get("narration") or "auto")
        narration_mode = requested_narration_mode
        if narration_mode == "auto":
            narration_mode = "gemini_voice" if self.narrator is not None else "subtitles"
        narration_fit = {
            "source_duration_seconds": 0.0,
            "target_duration_seconds": target_duration,
            "tempo_ratio": 1.0,
        }
        if narration_mode == "gemini_voice":
            if on_stage_progress is not None:
                on_stage_progress("narration", "running", "Generating natural narration")
            if self.narrator is None:
                raise ValueError("Gemini voice narration is not configured")
            directed_artifact = artifact.model_copy(update={"data": plan})
            try:
                narration = self.narrator.render(directed_artifact, output_dir / "narration.wav")
            except Exception as error:
                if requested_narration_mode != "auto":
                    raise
                narration_mode = "subtitles"
                warnings.append(
                    "Gemini narration was unavailable; the automatic mode continued with "
                    f"subtitles ({type(error).__name__})"
                )
                if on_stage_progress is not None:
                    on_stage_progress(
                        "narration",
                        "fallback",
                        "Narration unavailable; continuing with subtitles",
                    )
            if narration is not None:
                if narration.duration_seconds > target_duration:
                    effective_duration = float(round(narration.duration_seconds + 0.5, 1))
                    warnings.append(
                        "The video was extended slightly to preserve the narrator's natural "
                        "speaking speed; narration is never accelerated."
                    )
                narration_fit = {
                    "source_duration_seconds": round(narration.duration_seconds, 3),
                    "target_duration_seconds": effective_duration,
                    "tempo_ratio": 1.0,
                    "speed_changed": False,
                }
                if on_stage_progress is not None:
                    on_stage_progress("narration", "completed", "Narration track prepared")
        elif on_stage_progress is not None:
            on_stage_progress("narration", "skipped", "Voice narration was not requested")
        music_asset = None
        music_mode = str(artifact.options.get("music") or "off").lower()
        if music_mode != "off":
            if on_stage_progress is not None:
                on_stage_progress("music", "running", "Resolving noncommercial music")
            if self.music is None or not self.music.available:
                if music_mode == "required":
                    raise RuntimeError(
                        "Required background music is unavailable because Jamendo is not configured"
                    )
                warnings.append("Background music was skipped because Jamendo is not configured")
                if on_stage_progress is not None:
                    on_stage_progress("music", "skipped", "Background music is unavailable")
            else:
                try:
                    music_asset = self.music.resolve(
                        f"instrumental {artifact.title}",
                        output_dir / "background-music.mp3",
                        usage="noncommercial",
                    )
                    if on_stage_progress is not None:
                        on_stage_progress("music", "completed", "Background music prepared")
                except Exception as error:  # noqa: BLE001 - optional music remains optional
                    if music_mode == "required":
                        raise
                    warnings.append(f"Music: {type(error).__name__}: {error}")
                    if on_stage_progress is not None:
                        on_stage_progress("music", "skipped", "Background music was skipped")
        elif on_stage_progress is not None:
            on_stage_progress("music", "skipped", "Background music was not requested")
        self._retime(scenes, effective_duration)
        if on_stage_progress is not None:
            on_stage_progress("media_search", "running", "Resolving production media")
        media_started = time.monotonic()
        media_assets = self._resolve_media(
            artifact,
            scenes,
            output_dir,
            warnings,
            should_cancel=should_cancel,
            on_progress=on_media_progress,
        )
        media_metrics = {
            "duration_ms": round((time.monotonic() - media_started) * 1000),
            "cache_hit": bool(media_assets) and all(
                asset is None or asset.cache_hit for asset in media_assets
            ),
            "cache_hits": sum(bool(asset and asset.cache_hit) for asset in media_assets),
            "byte_count": sum(asset.byte_count for asset in media_assets if asset),
            "attempt": sum(asset.attempts for asset in media_assets if asset),
        }
        text = " ".join(
            str(value or "")
            for scene in scenes
            for value in (
                scene.get("title"),
                scene.get("on_screen_text"),
                scene.get("narration"),
            )
        )
        font_family, font_dir, font_file = self._font(artifact.options, text)
        credits: list[dict[str, str]] = []
        for index, (scene, asset) in enumerate(zip(scenes, media_assets, strict=True), start=1):
            if asset is None:
                scene["resolved_media"] = {
                    "kind": "card",
                    "id": f"motion-card-{index}",
                    "fallback_reason": (
                        "motion-graphics-requested"
                        if artifact.options.get("media_mode") == "motion_graphics"
                        else "stock-media-unavailable"
                    ),
                }
                continue
            scene["resolved_media"] = {
                "kind": asset.kind,
                "path": str(asset.path.resolve()),
                "id": f"resolved-{index}",
                "selected_query": str(scene.get("selected_media_query") or ""),
                "fallback_reason": "resolved-by-sourcelens",
                "credit": asset.creator,
                "source_url": asset.source_url,
                "license_url": asset.license_url,
                "cache_hit": asset.cache_hit,
                "byte_count": asset.byte_count,
                "attempts": asset.attempts,
            }
            credits.append(asset.credit())
        plan["scenes"] = scenes
        options = {
            **artifact.options,
            "duration_seconds": round(sum(float(item["duration_seconds"]) for item in scenes), 3),
            "font_family": font_family,
            "media_metrics": media_metrics,
        }
        if font_dir is not None:
            options["font_dir"] = str(font_dir.resolve())
        if font_file is not None:
            options["font_file"] = str(font_file.resolve())
        if narration is not None:
            options["voiceover_audio"] = str(narration.path.resolve())
            options["voiceover_source_duration_seconds"] = round(
                narration.duration_seconds,
                3,
            )
        if music_asset is not None:
            options["music_audio"] = str(music_asset.path.resolve())
            options["music_provenance"] = {
                "title": music_asset.title,
                "artist": music_asset.creator,
                "source_url": music_asset.source_url,
                "license_url": music_asset.license_url,
            }
            credits.append(music_asset.credit())
        return plan, options, {
            "media_credits": credits,
            "media_metrics": media_metrics,
            "render_warnings": warnings,
            "narration_attempts": narration.attempts if narration else [],
            "narration_mode": narration_mode,
            "narration_fit": narration_fit,
            "narration_ready": narration is not None,
            "font_family": font_family,
        }

    def _resolve_media(
        self,
        artifact,
        scenes,
        target_dir,
        warnings,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int, int, str], None] | None = None,
    ):
        mode = str(artifact.options.get("media_mode") or "auto")
        if mode == "motion_graphics" or self.media is None or not self.media.available:
            if mode != "motion_graphics" and any(
                bool(scene.get("media_required")) for scene in scenes
            ):
                raise RuntimeError(
                    "Verified API media is required for this video, but Pexels is not configured"
                )
            if mode != "motion_graphics":
                warnings.append("Stock media was skipped because Pexels is not configured")
            for current, _scene in enumerate(scenes, start=1):
                if on_progress is not None:
                    on_progress(current, current, len(scenes), "skipped")
            return [None] * len(scenes)
        requested_aspect = str(artifact.options.get("aspect_ratio") or "landscape")
        orientation = (
            requested_aspect
            if requested_aspect in {"portrait", "square"}
            else "landscape"
        )
        cancelled = should_cancel or (lambda: False)

        def resolve_scene(index_scene, exclude_urls: set[str] | None = None):
            index, scene = index_scene
            if cancelled():
                raise VisualEngineCancelled("Video media preparation was cancelled")
            raw_queries = scene.get("media_queries")
            queries = raw_queries if isinstance(raw_queries, list) else []
            queries = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in [
                        *queries,
                        scene.get("media_query"),
                    ]
                    if str(item or "").strip()
                )
            )[:4]
            if not queries:
                if scene.get("media_required"):
                    warnings.append(
                        f"Scene {index}: verified API media was required but no grounded query "
                        "was available"
                    )
                return None
            failures = []
            selected = None
            for query in queries:
                if cancelled():
                    raise VisualEngineCancelled("Video media preparation was cancelled")
                try:
                    candidate = self.media.video_or_photo(
                        query,
                        target_dir / "media",
                        mode=mode,
                        orientation=orientation,
                        index=index,
                        duration_seconds=float(scene.get("duration_seconds") or 4),
                        exclude_urls=set(exclude_urls or ()),
                        should_cancel=cancelled,
                    )
                    selected = candidate
                    scene["selected_media_query"] = query
                    break
                except VisualEngineCancelled:
                    raise
                except Exception as error:  # noqa: BLE001 - try the next semantic query
                    failures.append(f"{type(error).__name__}: {str(error)[:180]}")
            if selected is None:
                detail = "; ".join(failures[-2:]) or "no usable media query"
                if scene.get("media_required"):
                    raise RuntimeError(
                        f"Scene {index} requires verified API media but it could not be resolved: "
                        f"{detail}"
                    )
                warnings.append(f"Scene {index}: media fallback used ({detail})")
            return selected

        indexed_scenes = list(enumerate(scenes, start=1))

        def request_key(index_scene):
            _index, scene = index_scene
            queries = scene.get("media_queries") or [scene.get("media_query")]
            return (
                tuple(str(item or "").strip() for item in queries),
                mode,
                orientation,
                round(float(scene.get("duration_seconds") or 4), 1),
            )

        assets = [None] * len(indexed_scenes)
        completed = 0
        with ThreadPoolExecutor(max_workers=self.media_workers) as executor:
            requests = {}
            future_scenes = {}
            for index_scene in indexed_scenes:
                key = request_key(index_scene)
                future = requests.get(key)
                if future is None:
                    future = executor.submit(resolve_scene, index_scene)
                    requests[key] = future
                future_scenes.setdefault(future, []).append(index_scene)
            for future in as_completed(future_scenes):
                linked_scenes = future_scenes[future]
                try:
                    asset = future.result()
                except Exception:
                    for index, _scene in linked_scenes:
                        if on_progress is not None:
                            on_progress(index, completed, len(scenes), "failed")
                    raise
                for index, _scene in linked_scenes:
                    assets[index - 1] = asset
                    completed += 1
                    if on_progress is not None:
                        on_progress(
                            index,
                            completed,
                            len(scenes),
                            "resolved" if asset is not None else "skipped",
                        )
        used_urls: set[str] = set()
        for index, (scene, asset) in enumerate(zip(scenes, assets, strict=True), start=1):
            if asset is None:
                continue
            if asset.source_url in used_urls:
                if on_progress is not None:
                    on_progress(index, completed, len(scenes), "retrying")
                try:
                    replacement = resolve_scene((index, scene), used_urls)
                except VisualEngineCancelled:
                    raise
                except Exception as error:  # noqa: BLE001 - verified reuse is recoverable
                    replacement = None
                    warnings.append(
                        f"Scene {index}: a unique licensed alternative was unavailable "
                        f"({type(error).__name__}); the verified API asset was reused"
                    )
                if replacement is not None and replacement.source_url not in used_urls:
                    asset = replacement
                    assets[index - 1] = replacement
                    if on_progress is not None:
                        on_progress(index, completed, len(scenes), "resolved")
                else:
                    warnings.append(
                        f"Scene {index}: provider exhausted unique results; a verified licensed "
                        "asset was reused"
                    )
                    if on_progress is not None:
                        on_progress(index, completed, len(scenes), "reused")
            used_urls.add(asset.source_url)
        return assets

    @staticmethod
    def _retime(scenes: list[dict[str, Any]], total_seconds: float) -> None:
        weights = []
        for scene in scenes:
            try:
                planned = float(scene.get("duration_seconds") or 0)
            except (TypeError, ValueError):
                planned = 0.0
            weights.append(
                planned
                if planned > 0
                else max(1, len(str(scene.get("narration") or "").split()))
            )
        available = max(30.0, total_seconds)
        total_weight = sum(weights)
        durations = [max(3.0, available * weight / total_weight) for weight in weights]
        scale = available / sum(durations)
        for scene, duration in zip(scenes, durations, strict=True):
            scene["duration_seconds"] = round(duration * scale, 3)

    def _font(
        self,
        options: Mapping[str, Any],
        text: str = "",
    ) -> tuple[str, Path | None, Path | None]:
        requested = str(options.get("font_family") or "auto").strip()
        if requested.casefold() == "auto":
            registry = self.fonts or FontRegistry(Path("data/fonts"))
            asset = registry.best_for_text(text)
            if asset is not None:
                font_file = None if asset.source == "bundled" else asset.path
                return asset.family, asset.path.parent, font_file
            return "Arial", None, None
        if self.fonts is not None:
            with suppress(KeyError):
                asset = self.fonts.resolve(requested)
                font_file = None if asset.source == "bundled" else asset.path
                return asset.family, asset.path.parent, font_file
        return requested, None, None

class VisualArtifactService:
    """Materialize source-grounded visual plans into downloadable production files."""

    def __init__(
        self,
        *,
        artifacts: StudioArtifactStore,
        presentations: PresentationRenderer,
        videos: VideoOverviewRenderer,
        bridge: VisualEngineBridge | None = None,
        engine_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.presentations = presentations
        self.videos = videos
        self.bridge = bridge or VisualEngineBridge()
        self.engine_environment = dict(engine_environment or {})

    def materialize(self, artifact: StudioArtifact) -> StudioArtifact:
        if artifact.artifact_type not in {"slide_deck", "video_overview"}:
            return artifact
        cancelled = self._cancelled_artifact(artifact)
        if cancelled is not None:
            return cancelled
        input_warnings: list[str] = []
        directed_data = (
            direct_presentation(artifact.data, artifact.options, seed=artifact.artifact_id)
            if artifact.artifact_type == "slide_deck"
            else direct_video(artifact.data, artifact.options, seed=artifact.artifact_id)
        )
        if artifact.artifact_type == "slide_deck" and artifact.options.get("include_charts"):
            directed_slides = directed_data.get("slides") or []
            for index, slide in enumerate(artifact.data.get("slides") or [], start=1):
                layout = str(slide.get("layout") or "") if isinstance(slide, dict) else ""
                directed = directed_slides[index - 1] if index <= len(directed_slides) else {}
                chart = directed.get("chart") if isinstance(directed, dict) else None
                if layout in {"chart", "chart_focus", "dashboard"} and (
                    PresentationRenderer._chart_payload(chart) is None
                ):
                    input_warnings.append(
                        f"Slide {index}: chart skipped because categories and numeric values "
                        "are missing or misaligned"
                    )
        artifact = self.artifacts.save(
            artifact.model_copy(
                update={
                    "status": "generating",
                    "stage": "rendering_file",
                    "progress": 82,
                    "data": directed_data,
                }
            )
        )
        units = len(
            directed_data.get("slides")
            if artifact.artifact_type == "slide_deck"
            else directed_data.get("scenes")
            or []
        )
        self.artifacts.record_event(
            artifact.workspace_id,
            artifact.artifact_id,
            stage=("preparation" if artifact.artifact_type == "video_overview" else "media_search"),
            status="running",
            message=(
                "Preparing video production assets"
                if artifact.artifact_type == "video_overview"
                else "Resolving production media"
            ),
            current=0,
            total=max(1, units),
        )
        try:
            metadata: dict[str, Any]
            try:
                def on_stage_progress(stage: str, status: str, message: str) -> None:
                    current_artifact = self.artifacts.get(
                        artifact.workspace_id, artifact.artifact_id
                    )
                    self.artifacts.save(current_artifact.model_copy(update={"stage": stage}))
                    self.artifacts.record_event(
                        artifact.workspace_id,
                        artifact.artifact_id,
                        stage=stage,
                        status=status,
                        message=message,
                        current=1 if status in {"completed", "skipped", "fallback"} else 0,
                        total=1,
                    )

                def on_media_progress(
                    scene: int, current: int, total: int, media_status: str
                ) -> None:
                    progress = min(90, 82 + round(current / max(1, total) * 8))
                    current_artifact = self.artifacts.get(
                        artifact.workspace_id, artifact.artifact_id
                    )
                    self.artifacts.save(
                        current_artifact.model_copy(
                            update={"stage": "media_search", "progress": progress}
                        )
                    )
                    messages = {
                        "resolved": f"Scene {scene} media ready",
                        "retrying": f"Scene {scene} media was duplicated; finding an alternative",
                        "reused": f"Scene {scene} uses verified licensed media",
                        "skipped": f"Scene {scene} uses motion graphics",
                        "failed": f"Scene {scene} media lookup failed",
                    }
                    self.artifacts.record_event(
                        artifact.workspace_id,
                        artifact.artifact_id,
                        stage="media_search",
                        status="running",
                        message=messages.get(media_status, f"Scene {scene} media updated"),
                        current=current,
                        total=max(1, total),
                        details={"scene": scene, "media_status": media_status},
                    )

                output_dir = self.artifacts.asset_path(
                    artifact.workspace_id,
                    artifact.artifact_id,
                    "visual-engine-manifest.json",
                ).parent
                if artifact.artifact_type == "slide_deck":
                    plan, options, prepared = self.presentations.prepare_engine_request(
                        artifact, output_dir
                    )
                else:
                    plan, options, prepared = self.videos.prepare_engine_request(
                        artifact,
                        output_dir,
                        should_cancel=lambda: self._cancelled_artifact(artifact) is not None,
                        on_media_progress=on_media_progress,
                        on_stage_progress=on_stage_progress,
                    )
                self.artifacts.save(
                    self.artifacts.get(artifact.workspace_id, artifact.artifact_id).model_copy(
                        update={"stage": "media_download", "progress": 90}
                    )
                )
                self.artifacts.record_event(
                    artifact.workspace_id,
                    artifact.artifact_id,
                    stage="media_download",
                    status="completed",
                    message="Production media prepared",
                    current=units,
                    total=max(1, units),
                    duration_ms=prepared.get("media_metrics", {}).get("duration_ms"),
                    cache_hit=prepared.get("media_metrics", {}).get("cache_hit"),
                    byte_count=prepared.get("media_metrics", {}).get("byte_count"),
                    attempt=prepared.get("media_metrics", {}).get("attempt"),
                    details=prepared.get("media_metrics", {}),
                )
                def on_event(event: VisualEngineEvent) -> None:
                    if self._cancelled_artifact(artifact) is not None:
                        return
                    current_artifact = self.artifacts.get(
                        artifact.workspace_id, artifact.artifact_id
                    )
                    self.artifacts.save(
                        current_artifact.model_copy(
                            update={
                                "stage": f"visual_engine_{event.stage}",
                                "progress": min(99, 90 + round(event.progress * 0.09)),
                            }
                        )
                    )
                    self.artifacts.record_event(
                        artifact.workspace_id,
                        artifact.artifact_id,
                        stage=event.stage,
                        status="running",
                        message=event.message or "Visual renderer advanced",
                        current=event.progress,
                        total=100,
                        duration_ms=event.details.get("duration_ms"),
                        cache_hit=event.details.get("cache_hit"),
                        byte_count=event.details.get("byte_count"),
                        attempt=event.details.get("attempt"),
                        details=event.details,
                    )

                execution = self.bridge.render(
                    artifact_type=artifact.artifact_type,
                    artifact_id=artifact.artifact_id,
                    workspace_id=artifact.workspace_id,
                    language=artifact.language,
                    plan=plan,
                    options=options,
                    output_dir=output_dir,
                    on_event=on_event,
                    should_cancel=lambda: self._cancelled_artifact(artifact) is not None,
                    environment=self.engine_environment,
                )
                metadata = {**prepared, **execution.artifact_data()}
                if artifact.artifact_type == "video_overview":
                    authored_scenes = artifact.data.get("scenes") or []
                    prepared_scenes = plan.get("scenes") or []
                    if len(authored_scenes) == len(prepared_scenes):
                        metadata["scenes"] = [
                            {
                                **scene,
                                "duration_seconds": prepared_scene["duration_seconds"],
                            }
                            for scene, prepared_scene in zip(
                                authored_scenes, prepared_scenes, strict=True
                            )
                        ]
                self.artifacts.record_event(
                    artifact.workspace_id,
                    artifact.artifact_id,
                    stage=(
                        "slide_render"
                        if artifact.artifact_type == "slide_deck"
                        else "scene_render"
                    ),
                    status="completed",
                    message="Visual units rendered",
                    current=units,
                    total=max(1, units),
                )
                metadata["render_warnings"] = [
                    *prepared.get("render_warnings", []),
                    *execution.warnings,
                ]
                if not metadata.get("media_credits"):
                    metadata["media_credits"] = prepared.get("media_credits", [])
            except VisualEngineUnavailable:
                raise
            metadata["render_warnings"] = list(
                dict.fromkeys([*input_warnings, *metadata.get("render_warnings", [])])
            )
            cancelled = self._cancelled_artifact(artifact)
            if cancelled is not None:
                return cancelled
            current_artifact = self.artifacts.get(
                artifact.workspace_id, artifact.artifact_id
            )
            completed = self.artifacts.save(
                current_artifact.model_copy(
                    update={
                        "status": "ready",
                        "stage": "ready",
                        "progress": 100,
                        "data": {**current_artifact.data, **metadata},
                        "error": None,
                    }
                )
            )
            self.artifacts.record_event(
                artifact.workspace_id,
                artifact.artifact_id,
                stage="package",
                status="completed",
                message="Production files packaged",
                current=1,
                total=1,
            )
            self.artifacts.record_event(
                artifact.workspace_id,
                artifact.artifact_id,
                stage="completion",
                status="completed",
                message="Visual artifact completed",
                current=1,
                total=1,
            )
            return completed
        except Exception as error:
            cancelled = self._cancelled_artifact(artifact)
            if cancelled is not None:
                return cancelled
            current_artifact = self.artifacts.get(
                artifact.workspace_id, artifact.artifact_id
            )
            failed = self.artifacts.save(
                current_artifact.model_copy(
                    update={
                        "status": "failed",
                        "stage": "render_failed",
                        "error": f"{type(error).__name__}: {str(error)[:1200]}",
                    }
                )
            )
            self.artifacts.record_event(
                artifact.workspace_id,
                artifact.artifact_id,
                stage="render",
                status="failed",
                message="Visual rendering failed",
                current=failed.progress,
                total=100,
                details={"error_type": type(error).__name__},
            )
            return failed

    def _cancelled_artifact(self, artifact: StudioArtifact) -> StudioArtifact | None:
        current = self.artifacts.get(artifact.workspace_id, artifact.artifact_id)
        if current.status == "cancelled" or current.data.get("cancel_requested"):
            return current
        return None


def create_visual_artifact_service(
    *,
    settings,
    artifacts: StudioArtifactStore,
    fonts: FontRegistry | None = None,
):
    fonts = fonts or FontRegistry(
        settings.resolve_path(Path("data/fonts"), Path.cwd()),
        bundled_root=FontRegistry.default_bundled_root(),
    )
    pexels = (
        PexelsMediaResolver(
            settings.pexels_keys(),
            timeout_seconds=settings.research_api_timeout_seconds,
            cooldown_seconds=settings.research_api_cooldown_seconds,
            cache_dir=settings.resolve_path(Path("data/cache/visual-media"), Path.cwd()),
        )
        if settings.pexels_keys()
        else None
    )
    jamendo = (
        JamendoMusicResolver(
            settings.jamendo_ids(), timeout_seconds=settings.research_api_timeout_seconds
        )
        if settings.jamendo_ids()
        else None
    )
    piper = PiperPodcastRenderer(
        settings.piper_command_path(),
        en_voice_models=settings.piper_voice_models("en"),
        fa_voice_models=settings.piper_voice_models("fa"),
        timeout_seconds=settings.piper_timeout_seconds,
    )
    narrator = (
        GeminiVideoNarrator(
            settings.google_llm_keys(),
            groq_api_keys=settings.groq_speech_keys(),
            piper_renderer=piper,
            turn_timeout_seconds=settings.podcast_turn_timeout_seconds,
            minimum_key_gap_seconds=settings.podcast_minimum_key_gap_seconds,
            idle_refresh_seconds=settings.podcast_idle_refresh_seconds,
        )
        if settings.google_llm_keys() or settings.groq_speech_keys() or piper.configured
        else None
    )
    engine_environment = {}
    if settings.pexels_keys():
        engine_environment["PEXELS_API_KEY"] = settings.pexels_keys()[0]
    if settings.jamendo_ids():
        engine_environment["JAMENDO_CLIENT_ID"] = settings.jamendo_ids()[0]
    return VisualArtifactService(
        artifacts=artifacts,
        presentations=PresentationRenderer(pexels, fonts),
        videos=VideoOverviewRenderer(
            media=pexels,
            music=jamendo,
            narrator=narrator,
            fonts=fonts,
        ),
        bridge=VisualEngineBridge(),
        engine_environment=engine_environment,
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
