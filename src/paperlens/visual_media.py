from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from paperlens.provider_failover import (
    ProviderFailoverError,
    ProviderPool,
    retryable_search_provider_error,
)
from paperlens.visual_engine_bridge import VisualEngineCancelled


@dataclass(frozen=True)
class MediaAsset:
    path: Path
    kind: Literal["photo", "video", "audio"]
    source_url: str
    creator: str = ""
    creator_url: str = ""
    license_url: str = ""
    title: str = ""
    cache_hit: bool = False
    byte_count: int = 0
    attempts: int = 1

    def credit(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "source_url": self.source_url,
            "creator": self.creator,
            "creator_url": self.creator_url,
            "license_url": self.license_url,
        }


class LicensedMediaCache:
    """Private, expiring cache for downloaded media and its real provenance."""

    def __init__(self, root: Path, *, ttl_seconds: int = 7 * 24 * 60 * 60) -> None:
        self.root = root.resolve()
        self.ttl_seconds = max(60, ttl_seconds)
        self.files = self.root / "files"
        self.database = self.root / "media-cache.sqlite3"
        self.files.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    cache_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    creator TEXT NOT NULL,
                    creator_url TEXT NOT NULL,
                    license_url TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def key(*parts: object) -> str:
        raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, cache_key: str, target: Path) -> MediaAsset | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT kind, source_url, creator, creator_url, license_url,
                          file_path, byte_count, expires_at
                   FROM assets WHERE cache_key = ?""",
                (cache_key,),
            ).fetchone()
        if row is None or float(row[7]) <= time.time():
            return None
        cached_path = Path(str(row[5]))
        if not cached_path.is_file():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached_path, target)
        return MediaAsset(
            path=target,
            kind=row[0],
            source_url=row[1],
            creator=row[2],
            creator_url=row[3],
            license_url=row[4],
            cache_hit=True,
            byte_count=int(row[6]),
            attempts=0,
        )

    def put(self, cache_key: str, asset: MediaAsset) -> MediaAsset:
        suffix = asset.path.suffix.lower() or ".bin"
        cached_path = self.files / f"{cache_key}{suffix}"
        shutil.copy2(asset.path, cached_path)
        byte_count = cached_path.stat().st_size
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO assets (
                       cache_key, kind, source_url, creator, creator_url, license_url,
                       file_path, byte_count, expires_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       kind=excluded.kind, source_url=excluded.source_url,
                       creator=excluded.creator, creator_url=excluded.creator_url,
                       license_url=excluded.license_url, file_path=excluded.file_path,
                       byte_count=excluded.byte_count, expires_at=excluded.expires_at,
                       updated_at=excluded.updated_at""",
                (
                    cache_key,
                    asset.kind,
                    asset.source_url,
                    asset.creator,
                    asset.creator_url,
                    asset.license_url,
                    str(cached_path),
                    byte_count,
                    now + self.ttl_seconds,
                    now,
                ),
            )
        return MediaAsset(
            path=asset.path,
            kind=asset.kind,
            source_url=asset.source_url,
            creator=asset.creator,
            creator_url=asset.creator_url,
            license_url=asset.license_url,
            cache_hit=False,
            byte_count=byte_count,
            attempts=asset.attempts,
        )


class _RotatingHttpPool:
    """Media HTTP adapter backed by the shared provider scheduler."""

    def __init__(
        self,
        keys: Sequence[str],
        *,
        timeout_seconds: float = 35,
        cooldown_seconds: float = 45,
        minimum_gap_seconds: float = 0.35,
    ) -> None:
        self.keys = tuple(dict.fromkeys(key.strip() for key in keys if key.strip()))
        self.timeout_seconds = timeout_seconds
        self._pool = (
            ProviderPool(
                self.keys,
                provider="Licensed media API",
                cooldown_seconds=cooldown_seconds,
                retryable=retryable_search_provider_error,
                minimum_interval_seconds=minimum_gap_seconds,
                retry_rounds=2,
                wait_for_cooldown=True,
                restore_priority_after_cooldown=False,
                max_wait_seconds=600,
            )
            if self.keys
            else None
        )

    def get_json(
        self,
        url: str,
        *,
        header_name: str | None = None,
        query_key: str | None = None,
        params: dict[str, Any] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if self._pool is None:
            raise RuntimeError("No media API credentials are configured")

        def request(key: str, _slot: int) -> dict[str, Any]:
            if should_cancel and should_cancel():
                raise VisualEngineCancelled("Media provider request was cancelled")
            request_params = dict(params or {})
            if query_key:
                request_params[query_key] = key
            headers = {header_name: key} if header_name else {}
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = client.get(url, params=request_params, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Media provider returned invalid JSON")
            return payload

        try:
            return self._pool.run(request, should_cancel=should_cancel)
        except ProviderFailoverError as error:
            if should_cancel is not None and should_cancel():
                raise VisualEngineCancelled("Media provider wait was cancelled") from error
            raise

    def failover_status(self) -> dict[str, object]:
        return self._pool.status() if self._pool is not None else {"credential_count": 0}


class PexelsMediaResolver:
    """Resolve licensed photos/videos while preserving attribution metadata."""

    def __init__(
        self,
        api_keys: Sequence[str],
        *,
        timeout_seconds: float = 35,
        cooldown_seconds: float = 45,
        cache_dir: Path | None = None,
        cache_ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        self.pool = _RotatingHttpPool(
            api_keys,
            timeout_seconds=timeout_seconds,
            cooldown_seconds=cooldown_seconds,
        )
        self.cache = (
            LicensedMediaCache(cache_dir, ttl_seconds=cache_ttl_seconds)
            if cache_dir is not None
            else None
        )

    @property
    def available(self) -> bool:
        return bool(self.pool.keys)

    def photo(
        self,
        query: str,
        target: Path,
        *,
        orientation: str = "landscape",
        exclude_urls: set[str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> MediaAsset:
        cache_key = LicensedMediaCache.key("pexels", "photo", query, orientation)
        if self.cache is not None:
            cached = self.cache.get(cache_key, target)
            if cached is not None and cached.source_url not in (exclude_urls or set()):
                return cached
        payload = self.pool.get_json(
            "https://api.pexels.com/v1/search",
            header_name="Authorization",
            params={"query": query, "per_page": 12, "orientation": orientation},
            should_cancel=should_cancel,
        )
        photos = payload.get("photos") or []
        if not photos:
            raise RuntimeError(f"Pexels returned no photo for: {query}")
        excluded = exclude_urls or set()
        target_ratio = 9 / 16 if orientation == "portrait" else 16 / 9
        candidates = []
        for rank, photo in enumerate(photos):
            source_url = str(photo.get("url") or "https://www.pexels.com")
            if source_url in excluded:
                continue
            width = int(photo.get("width") or 0)
            height = int(photo.get("height") or 0)
            ratio = width / max(1, height)
            relevance_score = max(0.0, 1 - rank / max(1, len(photos)))
            ratio_score = max(0.0, 1 - abs(ratio - target_ratio) / target_ratio)
            resolution_score = min(1.0, (width * height) / (1920 * 1080))
            candidates.append(
                (relevance_score * 6 + ratio_score * 2 + resolution_score * 2, photo)
            )
        candidates.sort(key=lambda entry: entry[0], reverse=True)
        failures = []
        for attempt, (_, photo) in enumerate(candidates[:6], start=1):
            source = photo.get("src") or {}
            url = str(source.get("large2x") or source.get("large") or source.get("original") or "")
            try:
                target.unlink(missing_ok=True)
                download_options = {"maximum_bytes": 24 * 1024 * 1024}
                if should_cancel is not None:
                    download_options["should_cancel"] = should_cancel
                self._download(url, target, **download_options)
                asset = MediaAsset(
                    path=target,
                    kind="photo",
                    source_url=str(photo.get("url") or "https://www.pexels.com"),
                    creator=str(photo.get("photographer") or ""),
                    creator_url=str(photo.get("photographer_url") or ""),
                    license_url="https://www.pexels.com/license/",
                    byte_count=target.stat().st_size,
                    attempts=attempt,
                )
                return self.cache.put(cache_key, asset) if self.cache is not None else asset
            except VisualEngineCancelled:
                raise
            except Exception as error:  # noqa: BLE001 - try another licensed asset
                failures.append(f"{type(error).__name__}: {str(error)[:160]}")
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Pexels photo downloads failed for {query}: {'; '.join(failures[-3:])}")

    def video_or_photo(
        self,
        query: str,
        target_dir: Path,
        *,
        mode: str,
        orientation: str,
        index: int,
        duration_seconds: float = 5,
        exclude_urls: set[str] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> MediaAsset:
        cache_key = LicensedMediaCache.key(
            "pexels", "video-or-photo", query, mode, orientation, round(duration_seconds, 1)
        )
        cached_target = target_dir / f"scene-{index:02d}.bin"
        if self.cache is not None:
            cached = self.cache.get(cache_key, cached_target)
            if cached is not None and cached.source_url not in (exclude_urls or set()):
                suffix = ".mp4" if cached.kind == "video" else ".jpg"
                final_target = target_dir / f"scene-{index:02d}{suffix}"
                cached.path.replace(final_target)
                return MediaAsset(
                    path=final_target,
                    kind=cached.kind,
                    source_url=cached.source_url,
                    creator=cached.creator,
                    creator_url=cached.creator_url,
                    license_url=cached.license_url,
                    cache_hit=True,
                    byte_count=cached.byte_count,
                    attempts=0,
                )
        excluded = exclude_urls or set()
        if mode not in {"photo_first", "motion_graphics"}:
            try:
                payload = self.pool.get_json(
                    "https://api.pexels.com/v1/videos/search",
                    header_name="Authorization",
                    params={"query": query, "per_page": 10, "orientation": orientation},
                    should_cancel=should_cancel,
                )
                videos = payload.get("videos") or []
                candidates = []
                for video in videos:
                    source_url = str(video.get("url") or "https://www.pexels.com/videos/")
                    if source_url in excluded:
                        continue
                    files = video.get("video_files") or []
                    for media_file in files:
                        width = int(media_file.get("width") or 0)
                        height = int(media_file.get("height") or 0)
                        link = str(media_file.get("link") or "")
                        if width >= 720 and height >= 720 and link:
                            media_duration = float(video.get("duration") or 0)
                            ratio = width / max(1, height)
                            target_ratio = 9 / 16 if orientation == "portrait" else 16 / 9
                            ratio_score = max(0.0, 1 - abs(ratio - target_ratio) / target_ratio)
                            duration_score = min(1.0, media_duration / max(1.0, duration_seconds))
                            resolution_score = min(1.0, (width * height) / (1920 * 1080))
                            score = ratio_score * 5 + duration_score * 3 + resolution_score * 2
                            candidates.append((score, width * height, video, media_file))
                if candidates:
                    target_width, target_height = {
                        "portrait": (1080, 1920),
                        "square": (1080, 1080),
                    }.get(orientation, (1920, 1080))
                    sufficient = [
                        item
                        for item in candidates
                        if int(item[3].get("width") or 0) >= target_width
                        and int(item[3].get("height") or 0) >= target_height
                    ]
                    if sufficient:
                        _, _, video, media_file = min(
                            sufficient,
                            key=lambda item: (item[1], -item[0]),
                        )
                    else:
                        _, _, video, media_file = max(
                            candidates,
                            key=lambda item: (item[0], item[1]),
                        )
                    target = target_dir / f"scene-{index:02d}.mp4"
                    download_options = {"maximum_bytes": 80 * 1024 * 1024}
                    if should_cancel is not None:
                        download_options["should_cancel"] = should_cancel
                    self._download(str(media_file["link"]), target, **download_options)
                    user = video.get("user") or {}
                    asset = MediaAsset(
                        path=target,
                        kind="video",
                        source_url=str(video.get("url") or "https://www.pexels.com/videos/"),
                        creator=str(user.get("name") or ""),
                        creator_url=str(user.get("url") or ""),
                        license_url="https://www.pexels.com/license/",
                        byte_count=target.stat().st_size,
                    )
                    return self.cache.put(cache_key, asset) if self.cache is not None else asset
            except VisualEngineCancelled:
                raise
            except Exception:
                if mode == "video_first":
                    pass
        if mode == "motion_graphics":
            raise RuntimeError("Motion graphics mode does not request stock media")
        photo_target = target_dir / f"scene-{index:02d}.jpg"
        photo = self.photo(
            query,
            photo_target,
            orientation=orientation,
            exclude_urls=excluded,
            should_cancel=should_cancel,
        )
        if photo.source_url in excluded:
            raise RuntimeError(f"Pexels returned an already used photo for: {query}")
        if self.cache is not None:
            photo = self.cache.put(cache_key, photo)
        return photo

    @staticmethod
    def _download(
        url: str,
        target: Path,
        *,
        maximum_bytes: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        if not url.startswith("https://"):
            raise RuntimeError("Media provider returned an unsafe download URL")
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
            response.raise_for_status()
            with target.open("wb") as stream:
                for chunk in response.iter_bytes():
                    if should_cancel and should_cancel():
                        raise VisualEngineCancelled("Media download was cancelled")
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise RuntimeError("Downloaded media exceeds the safe size limit")
                    stream.write(chunk)


class JamendoMusicResolver:
    def __init__(self, client_ids: Sequence[str], *, timeout_seconds: float = 35) -> None:
        self.pool = _RotatingHttpPool(client_ids, timeout_seconds=timeout_seconds)

    @property
    def available(self) -> bool:
        return bool(self.pool.keys)

    def resolve(self, query: str, target: Path, *, usage: str) -> MediaAsset:
        if usage == "commercial":
            raise RuntimeError(
                "Free Jamendo catalog tracks are not automatically cleared for commercial use"
            )
        track = None
        search_queries = dict.fromkeys([query, "instrumental ambient", "cinematic background"])
        for search_query in search_queries:
            payload = self.pool.get_json(
                "https://api.jamendo.com/v3.0/tracks/",
                query_key="client_id",
                params={
                    "format": "json",
                    "limit": 20,
                    "search": search_query,
                    "include": "musicinfo",
                    "audioformat": "mp32",
                },
            )
            track = next(
                (
                    item
                    for item in payload.get("results") or []
                    if re.match(
                        r"^https?://creativecommons\.org/",
                        str(item.get("license_ccurl") or ""),
                        flags=re.IGNORECASE,
                    )
                    and not re.search(
                        r"/licenses/[^/]*(?:nd|sa)/",
                        str(item.get("license_ccurl") or ""),
                        flags=re.IGNORECASE,
                    )
                ),
                None,
            )
            if track is not None:
                break
        if track is None:
            raise RuntimeError("Jamendo returned no Creative Commons track")
        url = str(track.get("audiodownload") or track.get("audio") or "")
        PexelsMediaResolver._download(url, target, maximum_bytes=30 * 1024 * 1024)
        license_url = str(track.get("license_ccurl") or "")
        if license_url.startswith("http://"):
            license_url = f"https://{license_url.removeprefix('http://')}"
        return MediaAsset(
            path=target,
            kind="audio",
            source_url=str(track.get("shareurl") or "https://www.jamendo.com"),
            creator=str(track.get("artist_name") or ""),
            creator_url=str(track.get("artist_idstr") or ""),
            license_url=license_url,
            title=str(track.get("name") or ""),
        )

