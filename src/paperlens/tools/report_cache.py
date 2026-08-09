from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from paperlens.domain import Evidence

_CACHE_SCHEMA_VERSION = 1


def report_cache_key(
    *,
    report_type: str,
    language: str,
    instructions: str,
    evidence: Sequence[Evidence],
) -> str:
    """Bind a generated report to its prompt and exact retrieved evidence."""
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "report_type": report_type,
        "language": language,
        "instructions": instructions,
        "evidence": [
            {
                "chunk_id": item.chunk_id,
                "paper_id": item.paper_id,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "text": item.text,
            }
            for item in evidence
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_cached_report(
    target: Path,
    *,
    cache_key: str,
    validator: Callable[[str], bool],
) -> str | None:
    metadata_path = _metadata_path(target)
    if not target.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        text = target.read_text(encoding="utf-8").strip()
    except (OSError, json.JSONDecodeError):
        return None
    if (
        metadata.get("schema_version") != _CACHE_SCHEMA_VERSION
        or metadata.get("cache_key") != cache_key
        or not validator(text)
    ):
        return None
    return text


def store_cached_report(target: Path, *, text: str, cache_key: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.strip() + "\n", encoding="utf-8")
    _metadata_path(target).write_text(
        json.dumps(
            {
                "schema_version": _CACHE_SCHEMA_VERSION,
                "cache_key": cache_key,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _metadata_path(target: Path) -> Path:
    return target.with_suffix(".cache.json")
